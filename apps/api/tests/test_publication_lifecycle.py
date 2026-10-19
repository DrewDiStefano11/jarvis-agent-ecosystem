"""Deterministic durable-state and consumer refusal tests, not runner acceptance."""

import pytest

from app.core.errors import DomainError
from app.github_publication.lifecycle import ZERO, fold
from app.github_publication.validation_consumer import PublicationValidationConsumer, require_guard
from app.models.publication_lifecycle import PublicationEntry, PublicationPlan
from app.self_build.policy import digest
from tests.test_github_publication_readiness import HEAD, OLD, observation


def plan():
    payload = dict(
        workspace_id="workspace",
        task_id="task",
        task_title="Authorized feature",
        task_summary="Implement the persisted task.",
        actor_id="actor",
        runtime_run_id="run",
        attempt_id="attempt",
        native_command_id="native-command",
        native_plan_hash="1" * 64,
        repository_id=1,
        repository_identity="github.com/example/repository",
        branch="codex/job",
        base_branch="main",
        approved_base_sha=OLD,
        parent_sha=OLD,
        commit_sha=HEAD,
        expected_remote_sha=None,
        source_snapshot_digest="2" * 64,
        validation_journal_id="validation-journal",
        validation_execution_plan_hash="3" * 64,
        validation_code_state_hash="4" * 64,
        validation_journal_digest="6" * 64,
        publisher_ids=(42,),
        implementation_policy_digest="5" * 64,
    )
    # Convert tuple using the same canonical JSON representation as the contract.
    payload["publisher_ids"] = list(payload["publisher_ids"])
    return PublicationPlan(**payload, plan_hash=digest(payload))


def entry(kind, previous=None, **changes):
    payload = (
        dict(
            operation_id="publication-operation",
            sequence=1 if previous is None else previous.sequence + 1,
            previous_digest=ZERO if previous is None else previous.entry_digest,
            kind=kind,
            plan=None,
            request_digest=None,
            pr_number=None,
            pr_url=None,
            observation=None,
            validation_confirmed_digest="6" * 64 if kind == "observation" else None,
            error_code=None,
            retry_not_before=None,
            timestamp="2026-10-13T12:00:00Z",
        )
        | changes
    )
    return PublicationEntry(**payload, entry_digest=digest(payload))


def history():
    first = entry("prepared", plan=plan().model_dump(mode="json"))
    push = entry("push_intent", first, request_digest="a" * 64)
    pushed = entry("pushed", push)
    pr = entry("pr_intent", pushed, request_digest="b" * 64)
    created = entry(
        "pr_created", pr, pr_number=12, pr_url="https://github.com/example/repository/pull/12"
    )
    return [first, push, pushed, pr, created]


def test_contiguous_exact_head_observation_reports_manual_readiness():
    entries = history()
    assert fold(entries).state == "pr_created"
    entries.append(
        entry("observation", entries[-1], observation=observation().model_dump(mode="json"))
    )
    assert fold(entries).state == "completed"
    assert fold(entries).plan == plan()


@pytest.mark.parametrize("state", ["failed", "cancelled", "skipped", "stale"])
def test_nonpassing_checks_never_complete_publication(state):
    entries = history()
    measured = observation(
        checks=[
            dict(evidence_id="check-1", context="backend", app_id=42, head_sha=HEAD, state=state)
        ]
    )
    entries.append(entry("observation", entries[-1], observation=measured.model_dump(mode="json")))
    assert fold(entries).state == "review_pending"


def test_changed_head_invalidates_old_passing_readiness():
    entries = history()
    entries.append(
        entry(
            "observation",
            entries[-1],
            observation=observation(
                head_sha="f" * 40, head_sha_after_collection="f" * 40
            ).model_dump(mode="json"),
        )
    )
    assert fold(entries).state == "review_pending"


def test_skipped_intent_and_reordered_history_refuse_even_with_valid_entry_hashes():
    entries = history()
    with pytest.raises(ValueError):
        fold([entries[0], entry("pushed", entries[0])])
    with pytest.raises(ValueError):
        fold([entries[0], entries[2], entries[1]])
    with pytest.raises(ValueError):
        fold(entries + [entry("prepared", entries[-1], plan=plan().model_dump(mode="json"))])


def test_absent_isolation_consumer_fails_before_reading_any_receipt():
    consumer = PublicationValidationConsumer()
    with pytest.raises(DomainError) as error:
        consumer.require(object(), plan())
    assert error.value.code == "GITHUB_VALIDATION_ISOLATION_UNAVAILABLE"


@pytest.mark.parametrize("result", [False, True, "verified", object()])
def test_guard_return_values_cannot_grant_execution_or_hosted_cost_authority(result):
    with pytest.raises(DomainError):
        require_guard(lambda: result, "GITHUB_HOSTED_CI_COST_UNVERIFIED")


def test_async_guard_is_closed_and_refused():
    async def unexecuted():
        pytest.fail("async guard must not execute")

    with pytest.raises(DomainError):
        require_guard(unexecuted, "GITHUB_VALIDATION_ISOLATION_UNAVAILABLE")


def test_passing_ci_without_genuine_validation_binding_is_pending():
    entries = history()
    entries.append(
        entry(
            "observation",
            entries[-1],
            observation=observation().model_dump(mode="json"),
            validation_confirmed_digest=None,
        )
    )
    assert fold(entries).state == "validation_pending"


def test_blocked_retry_retains_original_push_intent_and_never_replays_preparation():
    entries = history()[:2]
    blocked = entry("blocked", entries[-1], error_code="GITHUB_TRANSPORT_UNAVAILABLE")
    resumed = entry("resumed", blocked)
    snapshot = fold(entries + [blocked, resumed])
    assert snapshot.state == "pushing"
    assert snapshot.push_request_digest == "a" * 64


def test_provider_retry_deadline_is_preserved_and_cannot_be_skipped():
    entries = history()[:2]
    blocked = entry(
        "blocked",
        entries[-1],
        error_code="GITHUB_RETRY_REQUIRED",
        retry_not_before="2026-10-13T12:01:00Z",
    )
    with pytest.raises(ValueError):
        fold(entries + [blocked, entry("resumed", blocked)])
    resumed = entry("resumed", blocked, timestamp="2026-10-13T12:01:00Z")
    assert fold(entries + [blocked]).retry_not_before is not None
    assert fold(entries + [blocked, resumed]).state == "pushing"
