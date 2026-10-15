"""Native two-stage approval, durable intent and checkpoint recovery in temp databases."""

from hashlib import sha256
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.errors import DomainError
from app.db.models import AuditEventRow, OutboxEventRow, SystemStateRow
from app.models.development_validation import CodeState
from app.models.git_commit import CommitMetadata, CommitPerson
from app.models.git_commit_authorization import (
    ApproveCommitInspection,
    ApproveNativeCommitPreparation,
    CommitSelection,
    PrepareNativeCommitRequest,
    PreviewCommitPreparation,
)
from app.models.identity import AssignPermissionRequest, CreatePermissionRequest
from app.models.source_change import (
    FileIdentity,
    PlanReferences,
    SourceChange,
    SourceChangeIntent,
    SourceContent,
    SourceEntry,
    SourceObservation,
    Topology,
)
from app.models.source_edit_planning import PersistEditingPlanRequest
from app.self_build import commit_service as service_module
from app.self_build.commit_service import CommitPreparationService
from app.source_change.planner import observation_identity

pytest_plugins = ["tests.test_workspace_creation_service"]


@pytest.fixture
def prepared_source(creation):
    app, actor, operator, creation_service, workspace_id, creation_request, _ = creation
    ready = creation_service.create(actor, workspace_id, creation_request)
    service = app.state.self_build_commit_service
    permission = app.state.identity_service.create_definition(
        "permission",
        CreatePermissionRequest(
            stable_key="self_build.git.commit.prepare",
            display_name="Prepare exact commit",
            resource_type="task",
            action="commit_prepare",
        ),
    )
    scope = dict(permission_id=permission.id, resource_type="task", resource_id="task-demo")
    for principal in (actor, operator):
        app.state.identity_service.assign_permission(
            principal.actor_id, AssignPermissionRequest(effect="allow", **scope)
        )
    before = SourceContent(
        text="value = 1\n", digest=sha256(b"value = 1\n").hexdigest(), byte_count=10, mode="100644"
    )
    after = SourceContent(
        text="value = 2\n", digest=sha256(b"value = 2\n").hexdigest(), byte_count=10, mode="100644"
    )
    observed = SourceObservation(
        repository_identity=ready.plan.workspace.repository_identity,
        head_sha=ready.plan.workspace.base_sha,
        base_sha=ready.plan.workspace.base_sha,
        root=Topology(kind="directory"),
        entries=(SourceEntry(path="source.py", kind="file", content=before),),
    )
    reservation = service.workspace.read(actor, workspace_id)
    intent = SourceChangeIntent(
        source=observation_identity(observed),
        references=PlanReferences(
            original_plan_hash=reservation.plan.plan_hash,
            authorization_reference=reservation.approval_id,
        ),
        changes=(
            SourceChange(
                operation="modify",
                path="source.py",
                before=FileIdentity(**before.model_dump(exclude={"text"})),
                after=after,
            ),
        ),
    )
    source = service.source.persist(
        actor,
        PersistEditingPlanRequest(
            workspace_id=workspace_id,
            expected_workspace_version=reservation.version,
            expected_workspace_plan_hash=reservation.plan.plan_hash,
            command_id="source-edit-1",
            source_intent=intent,
        ),
        observed,
        CodeState(
            repository_identity=observed.repository_identity,
            head_sha=observed.head_sha,
            base_sha=observed.base_sha,
            changes=(),
        ),
    )
    policy = service.workspace.policies.get("jarvis")
    target = Path(policy.worktree_root) / workspace_id
    (target / "source.py").write_bytes(b"value = 2\n")
    person = CommitPerson(
        name="Operator reviewed",
        email="fixture@example.invalid",
        timestamp=1791817701,
        timezone="-0400",
    )
    selection = CommitSelection(
        source_edit_record_id=source.record_id,
        selection=("source.py",),
        metadata=CommitMetadata(author=person, committer=person, message="Approved change\n"),
    )
    return app, actor, operator, service, workspace_id, selection, creation_request, scope, target


@pytest.fixture
def authorized_commit(prepared_source):
    app, actor, operator, service, workspace_id, selection, creation_request, scope, target = (
        prepared_source
    )
    plan = service.inspection_plan(actor, workspace_id, selection)
    approval = service.approve_inspection(
        operator,
        ApproveCommitInspection(
            **selection.model_dump(), workspace_id=workspace_id, expected_plan_hash=plan.plan_hash
        ),
    )
    preview_body = PreviewCommitPreparation(
        **selection.model_dump(),
        inspection_approval_id=approval.approval_id,
        expected_inspection_plan_hash=plan.plan_hash,
    )
    proposal = service.preview(actor, workspace_id, preview_body)
    approved = service.approve(
        operator,
        ApproveNativeCommitPreparation(
            **preview_body.model_dump(),
            workspace_id=workspace_id,
            expected_plan_hash=proposal.plan_hash,
        ),
    )
    request = PrepareNativeCommitRequest(
        **preview_body.model_dump(),
        command_id="prepare-commit-1",
        expected_plan_hash=proposal.plan_hash,
        approval_id=approved.approval_id,
        worker_id=creation_request.worker_id,
        lease_token=creation_request.lease_token,
    )
    return app, actor, operator, service, workspace_id, request, scope, target


def test_native_preparation_checkpoint_replay_preserves_all_git_effects(authorized_commit):
    app, actor, _, service, workspace_id, request, _, target = authorized_commit
    policy = service.workspace.policies.get("jarvis")
    primary = Path(policy.primary_root)
    index = primary / ".git/worktrees" / workspace_id / "index"
    before_index = index.read_bytes()
    original_head = (primary / ".git/refs/heads" / ("codex/" + workspace_id)).read_bytes()
    result = service.prepare(actor, workspace_id, request)
    assert result.state == "checkpointed" and result.checkpoint_id
    assert result.plan.proposal.lineage.source_edit_checkpoint_id is None
    assert not result.plan.proposal.commit_creation_supported
    assert service.prepare(actor, workspace_id, request) == result
    assert CommitPreparationService(app).read(actor, workspace_id, request.command_id) == result
    assert index.read_bytes() == before_index
    assert (primary / ".git/refs/heads" / ("codex/" + workspace_id)).read_bytes() == original_head
    assert (target / "source.py").read_bytes() == b"value = 2\n"
    assert request.lease_token not in result.model_dump_json()
    with service.repository.sessions() as session:
        events = list(
            session.scalars(
                select(AuditEventRow).where(AuditEventRow.event_type == service_module.PREPARED)
            )
        )
        assert len(events) == 1
        outbox = session.scalar(
            select(OutboxEventRow).where(OutboxEventRow.event_type == service_module.PREPARED)
        )
        assert outbox and outbox.sequence_number == events[0].sequence_number
    assert len(service.runtime.checkpoints_authorized("run-1", actor)) == 5


@pytest.mark.parametrize("gap", ["before", "after"])
def test_preparation_checkpoint_crash_recovers_same_intent(authorized_commit, monkeypatch, gap):
    app, actor, _, service, workspace_id, request, _, _ = authorized_commit
    native = service.runtime.handle_authorized

    def crash(*args, **kwargs):
        if gap == "after":
            native(*args, **kwargs)
        raise RuntimeError("native checkpoint crash")

    monkeypatch.setattr(service.runtime, "handle_authorized", crash)
    with pytest.raises(RuntimeError, match="checkpoint crash"):
        service.prepare(actor, workspace_id, request)
    pending = service.read(actor, workspace_id, request.command_id)
    assert pending.state == "prepared" and pending.checkpoint_id is None
    monkeypatch.setattr(service.runtime, "handle_authorized", native)
    restored = CommitPreparationService(app).prepare(actor, workspace_id, request)
    assert restored.operation_id == pending.operation_id and restored.state == "checkpointed"
    assert len(service.runtime.checkpoints_authorized("run-1", actor)) == 5


def test_unapproved_inspection_never_invokes_native_git(prepared_source, monkeypatch):
    _, actor, _, service, workspace_id, selection, _, _, _ = prepared_source
    plan = service.inspection_plan(actor, workspace_id, selection)
    body = PreviewCommitPreparation(
        **selection.model_dump(),
        inspection_approval_id="missing",
        expected_inspection_plan_hash=plan.plan_hash,
    )
    monkeypatch.setattr(
        service_module.ChangeVerifier,
        "inspect",
        lambda *a, **k: pytest.fail("unapproved native read"),
    )
    with pytest.raises(DomainError) as failure:
        service.preview(actor, workspace_id, body)
    assert failure.value.code == "GIT_COMMIT_INSPECTION_APPROVAL_REQUIRED"


@pytest.mark.parametrize("boundary", ["lease", "approval", "plan", "permission", "stop"])
def test_revoked_preparation_authority_creates_no_intent(authorized_commit, monkeypatch, boundary):
    app, actor, _, service, workspace_id, request, scope, _ = authorized_commit
    if boundary == "lease":
        request = request.model_copy(update={"lease_token": "wrong"})
    elif boundary == "approval":
        request = request.model_copy(update={"approval_id": "wrong"})
    elif boundary == "plan":
        request = request.model_copy(update={"expected_plan_hash": "0" * 64})
    elif boundary == "permission":
        app.state.identity_service.assign_permission(
            actor.actor_id, AssignPermissionRequest(effect="deny", **scope)
        )
    else:
        with service.repository.leases._write() as session:
            session.get(SystemStateRow, 1).emergency_stop = True
    with pytest.raises(DomainError):
        service.prepare(actor, workspace_id, request)
    with service.repository.sessions() as session:
        assert not list(
            session.scalars(
                select(AuditEventRow).where(AuditEventRow.event_type == service_module.PREPARED)
            )
        )


def test_changed_source_invalidates_operator_plan_without_staging(authorized_commit):
    _, actor, _, service, workspace_id, request, _, target = authorized_commit
    (target / "source.py").write_bytes(b"changed after approval\n")
    with pytest.raises(DomainError):
        service.prepare(actor, workspace_id, request)


def test_missing_native_checkpoint_cannot_be_acknowledged(authorized_commit, monkeypatch):
    _, actor, _, service, workspace_id, request, _, _ = authorized_commit
    monkeypatch.setattr(service.runtime, "handle_authorized", lambda *args, **kwargs: None)
    with pytest.raises(DomainError) as failure:
        service.prepare(actor, workspace_id, request)
    assert failure.value.code == "GIT_COMMIT_CHECKPOINT_INVALID"
    assert service.read(actor, workspace_id, request.command_id).state == "prepared"


@pytest.mark.parametrize("approval_field", ["inspection_approval_id", "approval_id"])
def test_expired_exact_approval_never_persists_preparation(authorized_commit, approval_field):
    from datetime import UTC, datetime, timedelta

    _, actor, _, service, workspace_id, request, _, _ = authorized_commit
    with service.repository.leases._write() as session:
        event = session.get(AuditEventRow, getattr(request, approval_field))
        # Simulate expiry without changing the original plan or permission scope.
        payload = dict(event.payload)
        nested = dict(payload["payload"])
        nested["expiresAt"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        payload["payload"] = nested
        event.payload = payload
    with pytest.raises(DomainError):
        service.prepare(actor, workspace_id, request)
    with service.repository.sessions() as session:
        assert not list(
            session.scalars(
                select(AuditEventRow).where(AuditEventRow.event_type == service_module.PREPARED)
            )
        )


def test_builder_cannot_approve_its_own_inspection(prepared_source):
    _, actor, _, service, workspace_id, body, _, _, _ = prepared_source
    plan = service.inspection_plan(actor, workspace_id, body)
    with pytest.raises(DomainError):
        service.approve_inspection(
            actor,
            ApproveCommitInspection(
                **body.model_dump(), workspace_id=workspace_id, expected_plan_hash=plan.plan_hash
            ),
        )


def test_native_preparation_openapi_and_operator_authentication(prepared_source):
    from app.self_build.commit_router import operator_router

    app, actor, _, service, workspace_id, body, _, _, _ = prepared_source
    app.include_router(operator_router)
    with TestClient(app, base_url="https://testserver") as client:
        headers = {"X-Jarvis-Actor-Id": actor.actor_id}
        preview = client.post(
            f"/api/self-build/workspaces/{workspace_id}/commit-preparation/inspection/preview",
            headers=headers,
            json=body.model_dump(mode="json"),
        )
        assert preview.status_code == 200, preview.text
        approval_body = body.model_dump(mode="json") | dict(
            workspace_id=workspace_id, expected_plan_hash=preview.json()["data"]["plan_hash"]
        )
        rejected = client.post(
            "/api/remote/self-build/commit-inspections/approve", headers=headers, json=approval_body
        )
        assert rejected.status_code == 401
        approved = client.post(
            "/api/remote/self-build/commit-inspections/approve",
            headers={"Authorization": "Bearer " + "x" * 48},
            json=approval_body,
        )
        assert approved.status_code == 200, approved.text
    schemas = app.openapi()["components"]["schemas"]
    assert "CommitAuthorizationPlan" in schemas and "PreparedCommitRecord" in schemas
