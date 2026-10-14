"""Adapter boundary tests only: no independently verified publication evidence."""

from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.core.errors import DomainError
from app.development_agent.publication import read_anchor, verify_destination
from app.models.development_publication import PublicationSourceAnchor, PublicationSourceReference
from app.self_build.policy import digest
from tests.test_development_navigation import context


def evidence():
    native = context()
    baseline = SimpleNamespace(
        context=native,
        task_id=native.task_id,
        editing_record_id="d" * 64,
        editing_plan_hash="e" * 64,
        mutation_operation_id="mutation-1",
        mutation_checkpoint_id="checkpoint-1",
    )
    reference = PublicationSourceReference(
        publication_operation_id="publication-1",
        expected_head_sha="b" * 40,
        observation_digest="f" * 64,
    )
    anchor = PublicationSourceAnchor(
        reference=reference,
        task_id=baseline.task_id,
        workspace_id=native.workspace_id,
        source_edit_record_id=baseline.editing_record_id,
        source_edit_plan_hash=baseline.editing_plan_hash,
        mutation_operation_id=baseline.mutation_operation_id,
        mutation_checkpoint_id=baseline.mutation_checkpoint_id,
        parent_code_state_hash=digest(native.code_state.model_dump(mode="json")),
        commit_sha=reference.expected_head_sha,
        tree_sha="c" * 40,
        parent_sha=native.code_state.head_sha,
        native_checkpoint_id="publication-checkpoint",
        native_checkpoint_integrity="1" * 64,
        link_hash="2" * 64,
        observed_at=datetime(2026, 10, 14, tzinfo=UTC),
    )
    destination = native.model_copy(
        update={
            "workspace_id": "workspace-2",
            "code_state": native.code_state.model_copy(
                update={
                    "head_sha": anchor.commit_sha,
                    "base_sha": anchor.commit_sha,
                }
            ),
        }
    )
    return baseline, reference, anchor, destination


def test_unconfigured_reader_is_unavailable():
    baseline, reference, _, _ = evidence()
    with pytest.raises(DomainError) as error:
        read_anchor(SimpleNamespace(state=SimpleNamespace()), object(), baseline, reference)
    assert error.value.code == "DEVELOPMENT_PUBLICATION_LINK_UNAVAILABLE"


def test_reader_receives_operator_and_original_workspace():
    baseline, reference, anchor, _ = evidence()
    actor = object()
    calls = []

    class Reader:
        def read(self, operator, workspace_id, requested):
            calls.append((operator, workspace_id, requested))
            return anchor

    app = SimpleNamespace(state=SimpleNamespace(development_publication_source_reader=Reader()))
    assert read_anchor(app, actor, baseline, reference) == anchor
    assert calls == [(actor, baseline.context.workspace_id, reference)]


@pytest.mark.parametrize(
    "field,value",
    [
        ("task_id", "task-other"),
        ("workspace_id", "workspace-other"),
        ("source_edit_record_id", "3" * 64),
        ("source_edit_plan_hash", "3" * 64),
        ("mutation_operation_id", "mutation-other"),
        ("mutation_checkpoint_id", "checkpoint-other"),
        ("parent_code_state_hash", "3" * 64),
        ("parent_sha", "d" * 40),
        ("commit_sha", "d" * 40),
    ],
)
def test_reader_link_must_match_protected_baseline(field, value):
    baseline, reference, anchor, _ = evidence()
    changed = anchor.model_copy(update={field: value})
    app = SimpleNamespace(
        state=SimpleNamespace(
            development_publication_source_reader=SimpleNamespace(read=lambda *args: changed),
        )
    )
    with pytest.raises(DomainError) as error:
        read_anchor(app, object(), baseline, reference)
    assert error.value.code == "DEVELOPMENT_PUBLICATION_LINK_CONFLICT"


def test_committed_destination_requires_anchor_and_matching_bytes():
    baseline, _, anchor, destination = evidence()
    verify_destination(baseline, destination, anchor)
    with pytest.raises(DomainError):
        verify_destination(baseline, destination, None)
    entries = destination.observation.entries
    changed = entries[0].model_copy(update={"content": None})
    destination = destination.model_copy(
        update={
            "observation": destination.observation.model_copy(
                update={"entries": (changed, *entries[1:])}
            ),
        }
    )
    with pytest.raises(DomainError) as error:
        verify_destination(baseline, destination, anchor)
    assert error.value.code == "DEVELOPMENT_CORRECTION_CONTEXT_INVALID"


def test_unread_baseline_and_destination_cannot_match_as_committed_source():
    baseline, _, anchor, destination = evidence()
    entries = baseline.context.observation.entries
    unread = baseline.context.observation.model_copy(
        update={
            "entries": (entries[0].model_copy(update={"content": None}), *entries[1:]),
        }
    )
    baseline.context = baseline.context.model_copy(update={"observation": unread})
    destination = destination.model_copy(update={"observation": unread})
    with pytest.raises(DomainError):
        verify_destination(baseline, destination, anchor)


@pytest.mark.parametrize(
    "field,value",
    [
        ("historical_only", False),
        ("mutation_authority", True),
        ("validation_authority", True),
    ],
)
def test_anchor_never_grants_authority(field, value):
    _, _, anchor, _ = evidence()
    with pytest.raises(ValidationError):
        PublicationSourceAnchor.model_validate({**anchor.model_dump(), field: value})


def test_anchor_timestamp_is_aware_and_canonical_utc():
    _, _, anchor, _ = evidence()
    payload = anchor.model_dump()
    payload["observed_at"] = datetime(2026, 10, 14, tzinfo=timezone(timedelta(hours=-4)))
    assert PublicationSourceAnchor.model_validate(payload).observed_at.tzinfo == UTC
    payload["observed_at"] = datetime(2026, 10, 14)
    with pytest.raises(ValidationError):
        PublicationSourceAnchor.model_validate(payload)


def test_publication_anchor_requires_complete_correction_lineage():
    from app.models.development_agent import ContextSelection
    from app.models.development_dispatch import AdmissionPlan, DevelopmentLimits

    _, _, anchor, _ = evidence()
    identifiers = (
        "task_id",
        "runtime_run_id",
        "attempt_id",
        "workspace_id",
        "creation_operation_id",
        "workspace_approval_id",
        "actor_id",
        "worker_id",
    )
    hashes = (
        "task_fingerprint",
        "creation_plan_hash",
        "workspace_plan_hash",
        "lease_fingerprint",
        "initial_context_hash",
        "policy_digest",
        "plan_hash",
    )
    payload = {key: "reference-1" for key in identifiers}
    payload.update({key: "a" * 64 for key in hashes})
    selection = ContextSelection(paths=("source.py",))
    payload.update(
        selection=selection,
        limits=DevelopmentLimits(
            provider="codex_app_server",
            model="coding-model",
            completion_criteria=("Return value.",),
        ),
    )
    original = AdmissionPlan.model_validate(payload)
    assert "publication_source" not in original.model_dump(mode="json")
    with pytest.raises(ValidationError):
        AdmissionPlan.model_validate({**payload, "publication_source": anchor})
    payload.update(
        publication_source=anchor,
        origin_admission_digest="b" * 64,
        baseline_id="c" * 64,
        baseline_snapshot_hash="d" * 64,
        repair_selection=selection,
    )
    assert AdmissionPlan.model_validate(payload).publication_source == anchor
