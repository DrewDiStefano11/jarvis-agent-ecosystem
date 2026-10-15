"""Authenticated repair evidence remains exact-state, bounded and advisory."""

from datetime import timedelta
from itertools import permutations

import pytest

from app.development_validation.evidence import ReceiptVerifier, build_execution_plan
from app.development_validation.planner import digest
from app.development_validation.repair import build_repair_evidence
from app.models.development_validation import FileChange
from app.self_improvement.engine import analyze, create_baseline
from app.self_improvement.validation import ValidationExecutionSource
from tests.test_validation_evidence import KEY, NOW, receipt, state


def verifier():
    return ReceiptVerifier({("fixture", "key-1"): KEY})


def report(values, *, current=None, now=NOW, **options):
    plan = build_execution_plan(state())
    return build_repair_evidence(
        current or state(),
        plan,
        values,
        verifier(),
        now=now,
        expected_execution_plan_hash=plan.execution_plan_hash,
        **options,
    )


def failed(**changes):
    return receipt(
        build_execution_plan(state()), outcome="failed", exit_code=1, failure_kind="test", **changes
    )


def test_exact_reproduction_and_no_mutation_authority():
    signed = failed()
    result = report([signed])
    finding = result.findings[0]
    assert finding.disposition == "failed" and finding.recommendation == "diagnose_failure"
    assert finding.reproduction.command_run_id == signed.measurement.command_run_id
    assert finding.reproduction.code_state_hash == signed.measurement.code_state_hash
    assert finding.reproduction.suite_digest == signed.measurement.command.suite_digest
    assert not result.execution_authorized and not result.source_mutation_authorized


def test_acknowledged_attempt_wins_regressed_collector_clock():
    old = failed(command_run_id="old", attempt_id="old-attempt")
    current = receipt(
        build_execution_plan(state()),
        command_run_id="current",
        attempt_id="current-attempt",
        started_at=NOW - timedelta(seconds=3),
        finished_at=NOW - timedelta(seconds=2),
    )
    for values in permutations((old, current)):
        finding = report(values, current_attempt=("run-1", "current-attempt")).findings[0]
        assert finding.disposition == "passed"
        assert finding.recommendation == "none"
        assert finding.reproduction.command_run_id == "current"
        assert len(finding.supporting_receipt_digests) == 2


def test_missing_current_attempt_does_not_revive_historical_receipt():
    values = [failed(attempt_id=f"old-{index}") for index in range(3)]
    finding = report(values, current_attempt=("run-1", "current-attempt")).findings[0]
    assert finding.disposition == "missing"
    assert finding.recommendation == "gather_evidence"
    assert finding.reproduction is None
    assert finding.retries_remaining == 0
    assert len(finding.supporting_receipt_digests) == 3


def test_attempt_selection_still_authenticates_older_receipts():
    old = failed(attempt_id="old-attempt").model_copy(update={"authentication_tag": "0" * 64})
    current = receipt(build_execution_plan(state()), attempt_id="current-attempt")
    with pytest.raises(ValueError, match="unauthenticated"):
        report((old, current), current_attempt=("run-1", "current-attempt"))


def test_validation_source_policy_version_names_the_provenance_digest():
    from app.development_validation.planner import POLICY_DIGEST, POLICY_VERSION

    plan = build_execution_plan(state())
    provenance, _ = ValidationExecutionSource(
        "policy-run",
        state(),
        plan,
        [failed()],
        verifier(),
        now=NOW,
        expected_execution_plan_hash=plan.execution_plan_hash,
    ).collect()
    assert (provenance.policy_version, provenance.policy_digest) == (POLICY_VERSION, POLICY_DIGEST)


def test_repair_and_source_require_original_host_approved_scope():
    approved = build_execution_plan(state(), boundary="publication", browser_required=True)
    reduced = build_execution_plan(state(), boundary="publication")
    values = [receipt(reduced, command, sequence=1) for command in reduced.commands]
    with pytest.raises(ValueError, match="approved execution plan"):
        build_repair_evidence(
            state(),
            reduced,
            values,
            verifier(),
            now=NOW,
            expected_execution_plan_hash=approved.execution_plan_hash,
        )
    with pytest.raises(ValueError, match="approved execution plan"):
        ValidationExecutionSource(
            "reduced",
            state(),
            reduced,
            values,
            verifier(),
            now=NOW,
            expected_execution_plan_hash=approved.execution_plan_hash,
        ).collect()
    with pytest.raises(TypeError, match="expected_execution_plan_hash"):
        build_repair_evidence(state(), reduced, values, verifier(), now=NOW)


@pytest.mark.parametrize("outcome", ["interrupted", "cancelled", "unmeasured"])
def test_unknown_results_require_evidence_and_do_not_invent_failure(outcome):
    signed = receipt(build_execution_plan(state()), outcome=outcome, exit_code=None)
    finding = report([signed]).findings[0]
    assert finding.disposition == outcome and finding.failure_kind is None
    assert finding.recommendation == "gather_evidence"


def test_missing_and_stale_results_request_revalidation():
    assert report([]).findings[0].disposition == "missing"
    expired = report([failed()], now=NOW + timedelta(seconds=3601)).findings[0]
    assert expired.disposition == "stale" and expired.recommendation == "revalidate"
    changed = state().model_copy(
        update={
            "changes": (
                FileChange(
                    path="docs/change.md",
                    before_hash=None,
                    before_mode=None,
                    after_hash="c" * 64,
                    after_mode="100644",
                ),
            )
        }
    )
    stale = report([failed()], current=changed)
    assert stale.source_changed and stale.findings[0].disposition == "stale"
    assert (
        stale.findings[0].reproduction.code_state_hash
        != build_execution_plan(changed).validation.code_state_hash
    )


def test_transient_retry_budget_is_bounded_by_recorded_failures():
    plan = build_execution_plan(state())
    values = []
    for i in range(3):
        values.append(
            receipt(
                plan,
                outcome="failed",
                exit_code=1,
                failure_kind="watchdog",
                diagnostic_codes=("timeout",),
                command_run_id="command-" + str(i),
                attempt_id="attempt-" + str(i),
                sequence=i + 1,
                started_at=NOW - timedelta(seconds=4 - i),
                finished_at=NOW - timedelta(seconds=3 - i),
            )
        )
        result = report(values).findings[0]
        assert result.retries_remaining == max(0, 2 - i)
        assert result.recommendation == ("request_bounded_retry" if i < 2 else "stop_retrying")
    with pytest.raises(ValueError):
        report(values, retry_budget=3)
    assert report(values, retry_budget=0).findings[0].recommendation == "stop_retrying"


def test_failure_category_without_recorded_transient_code_cannot_request_retry():
    plan = build_execution_plan(state())
    signed = receipt(plan, outcome="failed", exit_code=1, failure_kind="infrastructure")
    assert report([signed]).findings[0].recommendation == "diagnose_failure"


def test_tied_permanent_failure_is_not_suppressed_by_transient_failure():
    plan = build_execution_plan(state())
    permanent = failed()
    transient = receipt(
        plan,
        outcome="failed",
        exit_code=1,
        failure_kind="watchdog",
        diagnostic_codes=("timeout",),
        sequence=2,
        command_run_id="command-2",
        attempt_id="attempt-2",
    )
    for values in permutations([permanent, transient]):
        finding = report(values).findings[0]
        assert finding.recommendation == "diagnose_failure"
        assert len(finding.supporting_receipt_digests) == 2


def test_forged_replayed_and_wrong_plan_evidence_rejected():
    signed = failed()
    for values in (
        [signed, signed],
        [signed.model_copy(update={"authentication_tag": "f" * 64})],
        [receipt(build_execution_plan(state()), execution_plan_hash="f" * 64)],
    ):
        with pytest.raises(ValueError):
            report(values)


def test_existing_improvement_engine_accepts_measured_failure_with_incomplete_provenance():
    plan = build_execution_plan(state())
    source = ValidationExecutionSource(
        "validation-run",
        state(),
        plan,
        [failed()],
        verifier(),
        expected_execution_plan_hash=plan.execution_plan_hash,
        now=NOW,
    )
    provenance, facts = source.collect()
    assert provenance.source_type == "validation_execution" and not provenance.complete
    assert provenance.configuration_digest is None
    assert facts[0].actual == 0 and facts[0].hard_gate
    baseline = create_baseline(
        repo_sha=state().head_sha,
        configuration_fingerprint="c" * 64,
        safety_fingerprint="d" * 64,
        sources=(provenance,),
        observations=facts,
    )
    analysis = analyze(baseline)
    assert analysis.weaknesses and not analysis.weaknesses[0].actionable
    assert all(p.status == "needs_evidence" for p in analysis.proposals)


def test_expired_evidence_is_unmeasured_in_improvement_projection():
    plan = build_execution_plan(state())
    source = ValidationExecutionSource(
        "validation-run",
        state(),
        plan,
        [failed()],
        verifier(),
        expected_execution_plan_hash=plan.execution_plan_hash,
        now=NOW + timedelta(seconds=3601),
    )
    _, facts = source.collect()
    assert facts[0].actual is None and not facts[0].measured


@pytest.mark.parametrize(
    "failure_kind", ["infrastructure", "ci_environment", "dependency", "watchdog"]
)
def test_transient_failures_produce_retry_diagnoses_without_product_repairs(failure_kind):
    plan = build_execution_plan(state())
    signed = receipt(
        plan,
        outcome="failed",
        exit_code=1,
        failure_kind=failure_kind,
        diagnostic_codes=("timeout",),
    )
    provenance, facts = ValidationExecutionSource(
        "transient-run",
        state(),
        plan,
        [signed],
        verifier(),
        expected_execution_plan_hash=plan.execution_plan_hash,
        now=NOW,
    ).collect()
    assert facts[0].category == "reliability"
    baseline = create_baseline(
        repo_sha=state().head_sha,
        configuration_fingerprint="c" * 64,
        safety_fingerprint="d" * 64,
        sources=(provenance,),
        observations=facts,
    )
    proposals = analyze(baseline).proposals
    assert proposals and all(p.category == "retry_policy" for p in proposals)


def test_repair_disposition_follows_higher_sequence_after_clock_regression():
    plan = build_execution_plan(state())
    earlier_pass = receipt(plan)
    later_failure = failed(
        command_run_id="command-2",
        sequence=2,
        started_at=NOW - timedelta(seconds=3),
        finished_at=NOW - timedelta(seconds=2),
    )
    for values in permutations([earlier_pass, later_failure]):
        finding = report(values).findings[0]
        assert finding.disposition == "failed"
        assert finding.reproduction.command_run_id == "command-2"


@pytest.mark.parametrize(
    "failure_kind", ["infrastructure", "ci_environment", "dependency", "watchdog", "unknown"]
)
def test_unconfirmed_transient_kind_retains_failure_diagnosis(failure_kind):
    plan = build_execution_plan(state())
    signed = receipt(plan, outcome="failed", exit_code=1, failure_kind=failure_kind)
    provenance, facts = ValidationExecutionSource(
        "unconfirmed-run",
        state(),
        plan,
        [signed],
        verifier(),
        expected_execution_plan_hash=plan.execution_plan_hash,
        now=NOW,
    ).collect()
    assert facts[0].category == "reliability"
    assert report([signed]).findings[0].recommendation == "diagnose_failure"
    baseline = create_baseline(
        repo_sha=state().head_sha,
        configuration_fingerprint="c" * 64,
        safety_fingerprint="d" * 64,
        sources=(provenance,),
        observations=facts,
    )
    proposals = analyze(baseline).proposals
    assert proposals and all(p.category == "retry_policy" for p in proposals)
    assert all(p.status == "needs_evidence" for p in proposals)


def test_source_aliases_cannot_replay_same_measured_artifact():
    plan = build_execution_plan(state())
    payload = [failed()]
    left = ValidationExecutionSource(
        "left",
        state(),
        plan,
        payload,
        verifier(),
        expected_execution_plan_hash=plan.execution_plan_hash,
        now=NOW,
    )
    right = ValidationExecutionSource(
        "right",
        state(),
        plan,
        payload,
        verifier(),
        expected_execution_plan_hash=plan.execution_plan_hash,
        now=NOW,
    )
    a, x = left.collect()
    b, y = right.collect()
    assert a.digest == b.digest
    with pytest.raises(ValueError):
        create_baseline(
            repo_sha=state().head_sha,
            configuration_fingerprint="c" * 64,
            safety_fingerprint="d" * 64,
            sources=(a, b),
            observations=(*x, *y),
        )


def test_report_digest_uses_canonical_validated_timestamp_serialization():
    result = report([failed()])
    assert result.report_digest == digest(result.model_dump(mode="json", exclude={"report_digest"}))


def test_validation_baseline_head_must_match_measured_execution():
    from app.self_improvement.service import ImprovementService

    plan = build_execution_plan(state())
    source = ValidationExecutionSource(
        "validation-run",
        state(),
        plan,
        [failed()],
        verifier(),
        expected_execution_plan_hash=plan.execution_plan_hash,
        now=NOW,
    )
    with pytest.raises(ValueError, match="Validation evidence head differs"):
        ImprovementService(None).analyze(
            (source,),
            repo_sha="b" * 40,
            configuration_fingerprint="c" * 64,
            safety_fingerprint="d" * 64,
        )


def test_collector_key_rotation_cannot_replay_same_execution_as_distinct_artifacts():
    import hmac
    import json
    from hashlib import sha256

    from app.models.validation_execution import SignedReceipt

    plan = build_execution_plan(state())
    original = failed()
    envelope = original.model_dump(mode="json", exclude={"authentication_tag"})
    envelope["key_id"] = "key-2"
    tag = hmac.new(
        KEY,
        b"jarvis-validation-receipt-v1\0"
        + json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode(),
        sha256,
    ).hexdigest()
    rotated = SignedReceipt(**envelope, authentication_tag=tag)
    trusted = ReceiptVerifier({("fixture", "key-1"): KEY, ("fixture", "key-2"): KEY})
    a, _ = ValidationExecutionSource(
        "original",
        state(),
        plan,
        [original],
        trusted,
        expected_execution_plan_hash=plan.execution_plan_hash,
        now=NOW,
    ).collect()
    b, _ = ValidationExecutionSource(
        "rotated",
        state(),
        plan,
        [rotated],
        trusted,
        expected_execution_plan_hash=plan.execution_plan_hash,
        now=NOW,
    ).collect()
    assert a.digest == b.digest


def test_key_rotation_cannot_change_tied_measurement_facts_under_same_provenance():
    import hmac
    import json
    from hashlib import sha256

    from app.models.validation_execution import SignedReceipt

    plan = build_execution_plan(state())
    original = [
        receipt(plan, outcome="failed", exit_code=1, failure_kind="test", runtime_run_id="run-a"),
        receipt(
            plan, outcome="failed", exit_code=1, failure_kind="dependency", runtime_run_id="run-b"
        ),
    ]
    original_order = digest(original[0].model_dump(mode="json")) < digest(
        original[1].model_dump(mode="json")
    )
    # Find an authenticated rotation that reverses envelope ordering while
    # retaining exactly the same independently measured executions.
    for index in range(128):
        key_id = "rotated-" + str(index)
        rotated = []
        for value in original:
            envelope = value.model_dump(mode="json", exclude={"authentication_tag"})
            envelope["key_id"] = key_id
            tag = hmac.new(
                KEY,
                b"jarvis-validation-receipt-v1\0"
                + json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode(),
                sha256,
            ).hexdigest()
            rotated.append(SignedReceipt(**envelope, authentication_tag=tag))
        rotated_order = digest(rotated[0].model_dump(mode="json")) < digest(
            rotated[1].model_dump(mode="json")
        )
        if rotated_order != original_order:
            break
    else:
        pytest.fail("No reversed authenticated envelope ordering in rotation fixture")
    trusted = ReceiptVerifier({("fixture", "key-1"): KEY, ("fixture", key_id): KEY})
    sources = [
        ValidationExecutionSource(
            "rotation",
            state(),
            plan,
            values,
            trusted,
            now=NOW,
            expected_execution_plan_hash=plan.execution_plan_hash,
        ).collect()
        for values in (original, rotated, list(reversed(rotated)))
    ]
    assert all(value[0].digest == sources[0][0].digest for value in sources)
    assert all(value[1] == sources[0][1] for value in sources)


@pytest.mark.parametrize("path", [".env", "state.db-wal"])
def test_blocked_repository_policy_cannot_export_passing_repair_or_source(path):
    blocked = state().model_copy(
        update={
            "changes": (
                FileChange(
                    path=path,
                    before_hash=None,
                    before_mode=None,
                    after_hash="c" * 64,
                    after_mode="100644",
                ),
            )
        }
    )
    plan = build_execution_plan(blocked, boundary="publication")
    assert plan.validation.blocked_paths == (path,)
    values = [receipt(plan, command, sequence=1) for command in plan.commands]
    with pytest.raises(ValueError, match="blocked repository policy"):
        build_repair_evidence(
            blocked,
            plan,
            values,
            verifier(),
            now=NOW,
            expected_execution_plan_hash=plan.execution_plan_hash,
        )
    with pytest.raises(ValueError, match="blocked repository policy"):
        ValidationExecutionSource(
            "blocked",
            blocked,
            plan,
            values,
            verifier(),
            now=NOW,
            expected_execution_plan_hash=plan.execution_plan_hash,
        ).collect()


def test_reordered_model_copy_state_cannot_alias_the_same_measured_artifact():
    current = state().model_copy(
        update={
            "changes": tuple(
                FileChange(
                    path=path,
                    before_hash=None,
                    before_mode=None,
                    after_hash="c" * 64,
                    after_mode="100644",
                )
                for path in ("docs/a.md", "docs/b.md")
            )
        }
    )
    reversed_state = current.model_copy(update={"changes": tuple(reversed(current.changes))})
    plan = build_execution_plan(state())
    values = [failed()]
    a, x = ValidationExecutionSource(
        "canonical",
        current,
        plan,
        values,
        verifier(),
        now=NOW,
        expected_execution_plan_hash=plan.execution_plan_hash,
    ).collect()
    b, y = ValidationExecutionSource(
        "alias",
        reversed_state,
        plan,
        values,
        verifier(),
        now=NOW,
        expected_execution_plan_hash=plan.execution_plan_hash,
    ).collect()
    assert a.digest == b.digest
    with pytest.raises(ValueError):
        create_baseline(
            repo_sha=state().head_sha,
            configuration_fingerprint="c" * 64,
            safety_fingerprint="d" * 64,
            sources=(a, b),
            observations=(*x, *y),
        )


def test_equivalent_model_copy_timestamp_cannot_alias_verified_measurement():
    plan = build_execution_plan(state())
    signed = failed()
    aliased = signed.model_copy(
        update={
            "measurement": signed.measurement.model_copy(
                update={
                    "started_at": signed.measurement.started_at.isoformat(),
                }
            )
        }
    )
    assert verifier().verify(aliased) == verifier().verify(signed)
    assert report([aliased]) == report([signed])
    a, x = ValidationExecutionSource(
        "canonical",
        state(),
        plan,
        [signed],
        verifier(),
        now=NOW,
        expected_execution_plan_hash=plan.execution_plan_hash,
    ).collect()
    b, y = ValidationExecutionSource(
        "aliased",
        state(),
        plan,
        [aliased],
        verifier(),
        now=NOW,
        expected_execution_plan_hash=plan.execution_plan_hash,
    ).collect()
    assert a.digest == b.digest
    with pytest.raises(ValueError):
        create_baseline(
            repo_sha=state().head_sha,
            configuration_fingerprint="c" * 64,
            safety_fingerprint="d" * 64,
            sources=(a, b),
            observations=(*x, *y),
        )


def test_same_attempt_sequence_updates_do_not_consume_distinct_retries():
    plan = build_execution_plan(state())
    values = [
        receipt(
            plan,
            command_run_id="revision-" + str(sequence),
            sequence=sequence,
            outcome="failed",
            exit_code=1,
            failure_kind="watchdog",
            diagnostic_codes=("timeout",),
        )
        for sequence in (1, 2, 3)
    ]
    finding = report(values).findings[0]
    assert finding.recommendation == "request_bounded_retry" and finding.retries_remaining == 2
    assert finding.reproduction.command_run_id == "revision-3"
    assert finding.supporting_receipt_digests == (digest(values[-1].model_dump(mode="json")),)


def test_retry_recommendation_binds_all_counted_attempt_evidence():
    plan = build_execution_plan(state())
    latest = receipt(
        plan,
        runtime_run_id="latest",
        command_run_id="latest",
        outcome="failed",
        exit_code=1,
        failure_kind="watchdog",
        diagnostic_codes=("timeout",),
    )
    old = [
        receipt(
            plan,
            runtime_run_id="older-" + str(index),
            command_run_id="older-" + str(index),
            outcome="failed",
            exit_code=1,
            failure_kind="watchdog",
            diagnostic_codes=("timeout",),
            started_at=NOW - timedelta(seconds=4),
            finished_at=NOW - timedelta(seconds=3),
        )
        for index in (1, 2)
    ]
    alternate = receipt(
        plan,
        runtime_run_id="older-1",
        command_run_id="alternate-1",
        outcome="failed",
        exit_code=2,
        failure_kind="watchdog",
        diagnostic_codes=("timeout",),
        started_at=NOW - timedelta(seconds=4),
        finished_at=NOW - timedelta(seconds=3),
    )
    original = report([*old, latest])
    changed = report([alternate, old[1], latest])
    finding = original.findings[0]
    assert finding.recommendation == changed.findings[0].recommendation == "stop_retrying"
    assert finding.reproduction == changed.findings[0].reproduction
    assert set(finding.supporting_receipt_digests) == {
        digest(item.model_dump(mode="json")) for item in [*old, latest]
    }
    assert original.report_digest != changed.report_digest
    assert report([latest, *reversed(old)]) == original
