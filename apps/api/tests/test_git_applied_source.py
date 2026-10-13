"""Real completed mutation receipt + independent approved Git measurement."""

import pytest

from app.core.errors import DomainError
from app.models.git_commit import CommitMetadata, CommitPerson
from app.models.git_commit_authorization import (
    ApproveCommitInspection,
    ApproveNativeCommitPreparation,
    CommitSelection,
    PrepareNativeCommitRequest,
    PreviewCommitPreparation,
)
from app.models.identity import AssignPermissionRequest, CreatePermissionRequest
from app.self_build.applied_source import AppliedSourceCommitService

pytest_plugins = ["tests.test_workspace_creation_service"]


@pytest.mark.parametrize("acknowledgement", ["workspace_creation", "source_mutation"])
def test_applied_source_requires_real_creation_and_mutation_audit_acknowledgements(
    applied, acknowledgement
):
    from sqlalchemy import select

    from app.db.models import AuditEventRow
    from app.source_mutation.repository import SourceMutationRow

    app, actor, binding, workspace_id, request, mutation, target = applied
    assert binding.source(actor, workspace_id, request.source_edit_record_id).mutation_operation_id
    before = {
        path.relative_to(target): (path.read_bytes(), path.stat().st_ino)
        for path in target.rglob("*")
        if path.is_file()
    }
    with binding.preparation.repository.leases._write() as session:
        events = list(
            session.scalars(
                select(AuditEventRow).where(
                    AuditEventRow.event_type
                    == f"self_build.{acknowledgement}.checkpoint_acknowledged"
                )
            )
        )
        assert events
        for event in events:
            session.delete(event)
    expected = (
        "SOURCE_MUTATION_CREATION_CHECKPOINT_INVALID"
        if acknowledgement == "workspace_creation"
        else "GIT_COMMIT_APPLIED_SOURCE_REQUIRED"
    )
    with pytest.raises(DomainError) as failure:
        binding.bind(actor, workspace_id, request)
    assert failure.value.code == expected
    with app.state.repository.session_factory() as session:
        assert session.get(SourceMutationRow, request.source_edit_record_id).payload == (
            mutation.model_dump(mode="json")
        )
    assert before == {
        path.relative_to(target): (path.read_bytes(), path.stat().st_ino)
        for path in target.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize(
    "path",
    [
        ".jarvis-edit-foreign.before",
        "Source/../.jarvis-edit-" + "a" * 64 + ".before",
        ".jarvis-private/.jarvis-edit-" + "a" * 64 + ".before",
        "Source~1/.jarvis-edit-" + "a" * 64 + ".before",
    ],
)
def test_artifact_contract_rejects_aliases_and_private_parents(path):
    from app.models.git_changes import NativeArtifact

    with pytest.raises((ValueError, DomainError)):
        NativeArtifact(
            path=path,
            kind="source_backup",
            sha256="a" * 64,
            byte_count=10,
            provenance_digest="b" * 64,
        )


def test_legacy_inspection_preserves_original_serialized_hash():
    from app.models.git_commit_authorization import CommitInspectionPlan
    from app.self_build.policy import digest

    person = CommitPerson(
        name="Fixture", email="fixture@example.invalid", timestamp=1791817701, timezone="-0400"
    )
    metadata = CommitMetadata(author=person, committer=person, message="Exact legacy shape\n")
    payload = dict(
        schema_version="1.0",
        operation="git.commit.inspect",
        workspace_id="workspace-1",
        creation_operation_id="creation-1",
        creation_plan_hash="a" * 64,
        creation_checkpoint_id="checkpoint-1",
        source_edit_record_id="b" * 64,
        source_edit_record_plan_hash="c" * 64,
        actor_id="actor-1",
        runtime_run_id="run-1",
        attempt_id="attempt-1",
        worker_id="worker-1",
        tool_identity_digest="d" * 64,
        selection=["source.py"],
        metadata=metadata.model_dump(mode="json"),
        authorization_policy_digest="e" * 64,
    )
    record = CommitInspectionPlan(**payload, plan_hash=digest(payload))
    assert record.model_dump(mode="json", exclude={"plan_hash"}) == payload
    assert CommitInspectionPlan.model_validate_json(record.model_dump_json()) == record


@pytest.fixture
def applied(editing):
    app, actor, operator, mutation, request, target, _ = editing
    result = mutation.apply(actor, request)
    assert result.state == "complete"
    binding = AppliedSourceCommitService(app)
    preparation = binding.preparation
    permission = app.state.identity_service.create_definition(
        "permission",
        CreatePermissionRequest(
            stable_key="self_build.git.commit.prepare",
            display_name="Prepare exact commit",
            resource_type="task",
            action="commit_prepare",
        ),
    )
    for principal in (actor, operator):
        app.state.identity_service.assign_permission(
            principal.actor_id,
            AssignPermissionRequest(
                permission_id=permission.id,
                resource_type="task",
                resource_id="task-demo",
                effect="allow",
            ),
        )
    person = CommitPerson(
        name="Reviewed author",
        email="fixture@example.invalid",
        timestamp=1791817701,
        timezone="-0400",
    )
    selection = CommitSelection(
        source_edit_record_id=request.editing_record_id,
        selection=("source.py",),
        metadata=CommitMetadata(author=person, committer=person, message="Verified source edit\n"),
    )
    workspace_id = result.plan.workspace_id
    scope = preparation.inspection_plan(actor, workspace_id, selection)
    grant = preparation.approve_inspection(
        operator,
        ApproveCommitInspection(
            **selection.model_dump(), workspace_id=workspace_id, expected_plan_hash=scope.plan_hash
        ),
    )
    preview = PreviewCommitPreparation(
        **selection.model_dump(),
        inspection_approval_id=grant.approval_id,
        expected_inspection_plan_hash=scope.plan_hash,
    )
    plan = preparation.preview(actor, workspace_id, preview)
    approval = preparation.approve(
        operator,
        ApproveNativeCommitPreparation(
            **preview.model_dump(), workspace_id=workspace_id, expected_plan_hash=plan.plan_hash
        ),
    )
    commit_request = PrepareNativeCommitRequest(
        **preview.model_dump(),
        command_id="bind-applied-source",
        expected_plan_hash=plan.plan_hash,
        approval_id=approval.approval_id,
        worker_id=request.worker_id,
        lease_token=request.lease_token,
    )
    return app, actor, binding, workspace_id, commit_request, result, target


def test_real_applied_edit_is_bound_to_checkpointed_exact_preparation(applied, monkeypatch):
    _, actor, binding, workspace_id, request, mutation, target = applied
    monkeypatch.setattr(
        binding.history.service, "apply", lambda *a, **k: pytest.fail("source replay")
    )
    result = binding.bind(actor, workspace_id, request)
    assert result.current_source_measured and not result.git_commit_created
    assert result.preparation.state == "checkpointed"
    assert result.source_evidence.mutation_checkpoint_id == mutation.checkpoint_id
    assert result.source_evidence.mutation_operation_id == mutation.operation_id
    assert result.source_evidence.original_mutation_approval_id == mutation.original_approval_id
    assert result.source_evidence.final_observation_digest == mutation.final_observation_digest
    assert result.preparation.plan.proposal.lineage.source_edit_checkpoint_id is None
    from app.self_build.native_artifacts import mutation_artifacts

    source = binding.preparation.source_record(actor, workspace_id, request.source_edit_record_id)
    manifest = mutation_artifacts(binding.preparation.app, actor, source)
    assert len(manifest.artifacts) == 1
    assert manifest.artifacts[0].kind == "source_backup"
    assert (target / manifest.artifacts[0].path).read_bytes() == b"value = 1\n"
    assert (
        result.preparation.plan.inspection_plan.native_artifact_manifest_digest
        == manifest.fingerprint
    )
    assert (target / "source.py").read_bytes() == b"value = 2\n"
    assert request.lease_token not in result.model_dump_json()
    assert binding.bind(actor, workspace_id, request) == result


def test_pending_source_mutation_cannot_become_applied_evidence(editing):
    app, actor, _, mutation, request, _, _ = editing
    mutation.prepare(actor, request)
    binding = AppliedSourceCommitService(app)
    with pytest.raises(DomainError) as failure:
        binding.source(
            actor,
            mutation.preview(actor, request.editing_record_id).workspace_id,
            request.editing_record_id,
        )
    assert failure.value.code == "GIT_COMMIT_APPLIED_SOURCE_REQUIRED"


def test_historical_complete_edit_does_not_authorize_drifted_current_source(applied):
    _, actor, binding, workspace_id, request, _, target = applied
    (target / "source.py").write_bytes(b"drift after completed mutation\n")
    # Historical receipt still exists; independent current measurement must refuse.
    assert binding.source(actor, workspace_id, request.source_edit_record_id).mutation_checkpoint_id
    with pytest.raises(DomainError):
        binding.bind(actor, workspace_id, request)


def test_coherently_rehashed_foreign_receipt_cannot_replace_native_history(applied, monkeypatch):
    from app.models.source_mutation_history import MutationHistory

    _, actor, binding, workspace_id, request, mutation, _ = applied
    forged = mutation.model_copy(update={"completed_count": 0, "prefix_digest": None})
    monkeypatch.setattr(
        binding.history,
        "read",
        lambda *args: MutationHistory(
            record=forged, native_checkpoint_present=True, journal_checkpoint_acknowledged=True
        ),
    )
    with pytest.raises(DomainError):
        binding.source(actor, workspace_id, request.source_edit_record_id)


@pytest.mark.parametrize(
    "tamper", ["backup_bytes", "missing_backup", "foreign_private", "root_lock"]
)
def test_native_artifacts_must_match_completed_history(applied, tamper):
    from app.self_build.native_artifacts import mutation_artifacts

    _, actor, binding, workspace_id, request, _, target = applied
    source = binding.preparation.source_record(actor, workspace_id, request.source_edit_record_id)
    manifest = mutation_artifacts(binding.preparation.app, actor, source)
    backup = target / manifest.artifacts[0].path
    if tamper == "backup_bytes":
        backup.write_bytes(b"value = 9\n")
    elif tamper == "missing_backup":
        backup.unlink()
    elif tamper == "foreign_private":
        (target / ".jarvis-edit-foreign.before").write_bytes(b"value = 1\n")
    else:
        (target / ".jarvis-workspace.lock").write_bytes(b"0")
    with pytest.raises(DomainError):
        binding.bind(actor, workspace_id, request)
