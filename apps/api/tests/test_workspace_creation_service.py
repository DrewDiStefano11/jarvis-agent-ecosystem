"""Native creation intent, exact authority, checkpoint crash gaps and migration."""

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import select

from app.core.errors import DomainError
from app.db.models import AuditEventRow, DevelopmentWorkspaceRow, OutboxEventRow, SystemStateRow
from app.models.agent_runtime import BeginAttemptCommand, StartAttemptCommand
from app.models.identity import AssignPermissionRequest, CreatePermissionRequest
from app.models.workspace_creation import ApproveWorkspaceCreation, CreateWorkspaceRequest
from app.self_build.creation_service import WorkspaceCreationService
from tests.agent_runtime_testkit import ts
from tests.test_autonomous_worker_migration import migration_config

pytest_plugins = ["tests.test_self_build_git_inspection"]


@pytest.fixture
def creation(git_workspace):
    app, actor, operator, git, reservation, inspect_request, root, _ = git_workspace
    observed = git.inspect(actor, reservation.workspace_id, inspect_request)
    runtime = app.state.agent_runtime_service
    snapshot = runtime.read_run_authorized("run-1", actor)
    beginning = runtime.handle_authorized(
        BeginAttemptCommand(
            run_id="run-1",
            command_id="begin-create",
            expected_run_version=snapshot.version,
            timestamp=ts(3),
            executor_reference=inspect_request.worker_id,
        ),
        actor,
    )
    runtime.handle_authorized(
        StartAttemptCommand(
            run_id="run-1",
            command_id="start-create",
            expected_run_version=beginning.snapshot.version,
            timestamp=ts(4),
            attempt_id=beginning.snapshot.active_attempt_id,
        ),
        actor,
    )
    permission = app.state.identity_service.create_definition(
        "permission",
        CreatePermissionRequest(
            stable_key="self_build.workspace.materialize",
            display_name="Materialize owned workspace",
            resource_type="task",
            action="materialize",
        ),
    )
    scope = dict(permission_id=permission.id, resource_type="task", resource_id="task-demo")
    for principal in (actor, operator):
        app.state.identity_service.assign_permission(
            principal.actor_id, AssignPermissionRequest(effect="allow", **scope)
        )
    service = WorkspaceCreationService(app)
    plan = service.preview(actor, reservation.workspace_id, observed.inspection_id)
    approval = service.approve(
        operator,
        ApproveWorkspaceCreation(
            workspace_id=reservation.workspace_id,
            inspection_id=observed.inspection_id,
            expected_plan_hash=plan.plan_hash,
        ),
    )
    request = CreateWorkspaceRequest(
        inspection_id=observed.inspection_id,
        expected_plan_hash=plan.plan_hash,
        approval_id=approval.approval_id,
        worker_id=inspect_request.worker_id,
        lease_token=inspect_request.lease_token,
    )
    return app, actor, operator, service, reservation.workspace_id, request, scope


def test_native_preparation_checkpoint_and_outbox_are_replayable_without_files(creation):
    app, actor, _, service, workspace_id, request, _ = creation
    result = service.prepare(actor, workspace_id, request)
    assert result.state == "prepared" and result.completed_file_count == 0
    assert result.checkpoint_id and service.prepare(actor, workspace_id, request) == result
    assert WorkspaceCreationService(app).read(actor, workspace_id) == result
    checkpoints = app.state.agent_runtime_service.checkpoints_authorized("run-1", actor)
    assert len(checkpoints) == 1 and checkpoints[0].checkpoint_id == result.checkpoint_id
    with app.state.repository.session_factory() as session:
        private = session.get(DevelopmentWorkspaceRow, workspace_id).creation_json
        assert checkpoints[0].integrity_digest == "sha256:" + private["digest"]
        events = list(
            session.scalars(
                select(AuditEventRow).where(
                    AuditEventRow.event_type == "self_build.workspace_creation.prepared"
                )
            )
        )
        assert len(events) == 1
        outbox = session.scalar(
            select(OutboxEventRow).where(
                OutboxEventRow.event_type == "self_build.workspace_creation.prepared"
            )
        )
        assert outbox and outbox.sequence_number == events[0].sequence_number
        assert private["nonce"] not in json.dumps(
            [event.payload for event in session.scalars(select(AuditEventRow))]
        )
    assert request.lease_token not in result.model_dump_json()
    policy = service.workspace.policies.get("jarvis")
    assert list(Path(policy.worktree_root).iterdir()) == []


def test_crash_after_intent_before_checkpoint_recovers_same_operation(creation, monkeypatch):
    app, actor, _, service, workspace_id, request, _ = creation
    native = service.runtime.handle_authorized

    def crashed(*args, **kwargs):
        raise RuntimeError("crash before native checkpoint")

    monkeypatch.setattr(service.runtime, "handle_authorized", crashed)
    with pytest.raises(RuntimeError, match="crash"):
        service.prepare(actor, workspace_id, request)
    pending = service.read(actor, workspace_id)
    assert pending.checkpoint_id is None
    monkeypatch.setattr(service.runtime, "handle_authorized", native)
    restored = WorkspaceCreationService(app).prepare(actor, workspace_id, request)
    assert (
        restored.operation_id == pending.operation_id
        and restored.ownership_digest == pending.ownership_digest
    )
    assert (
        restored.checkpoint_id and len(service.runtime.checkpoints_authorized("run-1", actor)) == 1
    )


def test_crash_after_checkpoint_before_ack_reuses_native_checkpoint(creation, monkeypatch):
    app, actor, _, service, workspace_id, request, _ = creation
    native = service.runtime.handle_authorized

    def crashed_after(*args, **kwargs):
        native(*args, **kwargs)
        raise RuntimeError("crash after native checkpoint")

    monkeypatch.setattr(service.runtime, "handle_authorized", crashed_after)
    with pytest.raises(RuntimeError, match="crash"):
        service.prepare(actor, workspace_id, request)
    pending = service.read(actor, workspace_id)
    assert pending.checkpoint_id is None
    checkpoints = service.runtime.checkpoints_authorized("run-1", actor)
    assert len(checkpoints) == 1
    monkeypatch.setattr(service.runtime, "handle_authorized", native)
    recovered = WorkspaceCreationService(app).prepare(actor, workspace_id, request)
    assert (
        recovered.operation_id == pending.operation_id
        and recovered.checkpoint_id == checkpoints[0].checkpoint_id
    )
    assert len(service.runtime.checkpoints_authorized("run-1", actor)) == 1


@pytest.mark.parametrize("boundary", ["plan", "approval", "lease", "stop", "permission", "expired"])
def test_revoked_creation_authority_never_persists_intent(creation, boundary, monkeypatch):
    app, actor, _, service, workspace_id, request, scope = creation
    if boundary == "plan":
        request = request.model_copy(update={"expected_plan_hash": "0" * 64})
    elif boundary == "approval":
        request = request.model_copy(update={"approval_id": "unknown-approval"})
    elif boundary == "lease":
        request = request.model_copy(update={"lease_token": "wrong-lease"})
    elif boundary == "permission":
        app.state.identity_service.assign_permission(
            actor.actor_id, AssignPermissionRequest(effect="deny", **scope)
        )
    elif boundary == "stop":
        with service.repository.leases._write() as session:
            session.get(SystemStateRow, 1).emergency_stop = True
    else:
        from app.self_build import creation_service

        class ExpiredClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.now(tz) + timedelta(seconds=1000)

        monkeypatch.setattr(creation_service, "datetime", ExpiredClock)
    expected = {
        "plan": "SELF_BUILD_PLAN_CHANGED",
        "approval": "SELF_BUILD_APPROVAL_REQUIRED",
        "lease": "TASK_LEASE_LOST",
        "stop": "EMERGENCY_STOP_ACTIVE",
        "permission": "SELF_BUILD_CREATION_PERMISSION_DENIED",
        "expired": "SELF_BUILD_APPROVAL_REQUIRED",
    }
    with pytest.raises(DomainError) as failure:
        service.prepare(actor, workspace_id, request)
    assert failure.value.code == expected[boundary]
    with app.state.repository.session_factory() as session:
        assert session.get(DevelopmentWorkspaceRow, workspace_id).creation_json is None


def test_private_intent_tampering_blocks_recovery(creation):
    app, actor, _, service, workspace_id, request, _ = creation
    service.prepare(actor, workspace_id, request)
    with service.repository.leases._write() as session:
        row = session.get(DevelopmentWorkspaceRow, workspace_id)
        row.creation_json = row.creation_json | {"nonce": "0" * 64}
    with pytest.raises(DomainError) as failure:
        service.prepare(actor, workspace_id, request)
    assert failure.value.code == "SELF_BUILD_CREATION_RECORD_INVALID"


def test_creation_migration_refuses_to_discard_private_intent(creation):
    app, actor, _, service, workspace_id, request, _ = creation
    service.prepare(actor, workspace_id, request)
    config = migration_config(Path(app.state.engine.url.database))
    with pytest.raises(RuntimeError, match="Export workspace creation intent"):
        command.downgrade(config, "20261006_12")
    assert service.read(actor, workspace_id).checkpoint_id


def test_application_restart_preserves_native_intent_and_checkpoint(creation):
    from app.main import create_app

    app, actor, _, service, workspace_id, request, _ = creation
    result = service.prepare(actor, workspace_id, request)
    restarted = create_app(delay_ms=1, database_url=str(app.state.engine.url))
    try:
        # Historical read remains available with mutation disabled by default.
        assert not restarted.state.settings.self_build_enabled
        assert WorkspaceCreationService(restarted).read(actor, workspace_id) == result
    finally:
        restarted.state.engine.dispose()


def test_revocation_inside_checkpoint_commit_guard_leaves_no_checkpoint(creation, monkeypatch):
    app, actor, _, service, workspace_id, request, scope = creation
    native = service.runtime.handle_authorized

    def revoked_before_commit(*args, **kwargs):
        app.state.identity_service.assign_permission(
            actor.actor_id, AssignPermissionRequest(effect="deny", **scope)
        )
        return native(*args, **kwargs)

    monkeypatch.setattr(service.runtime, "handle_authorized", revoked_before_commit)
    with pytest.raises(DomainError) as failure:
        service.prepare(actor, workspace_id, request)
    assert failure.value.code == "SELF_BUILD_CREATION_PERMISSION_DENIED"
    pending = service.read(actor, workspace_id)
    assert pending.checkpoint_id is None
    assert len(service.runtime.checkpoints_authorized("run-1", actor)) == 0
    assert list(Path(service.workspace.policies.get("jarvis").worktree_root).iterdir()) == []


def test_unacknowledged_native_checkpoint_mismatch_blocks_recovery(creation, monkeypatch):
    from app.models.agent_runtime import AgentRunCheckpoint

    app, actor, _, service, workspace_id, request, _ = creation
    result = service.prepare(actor, workspace_id, request)
    checkpoint = service.runtime.checkpoints_authorized("run-1", actor)[0]
    corrupted = AgentRunCheckpoint.model_validate(
        checkpoint.model_dump() | {"resume_cursor": "invented"}
    )
    monkeypatch.setattr(service.runtime, "checkpoints_authorized", lambda *args: (corrupted,))
    with pytest.raises(DomainError) as failure:
        service.prepare(actor, workspace_id, request)
    assert failure.value.code == "SELF_BUILD_CREATION_CHECKPOINT_INVALID"
    with app.state.repository.session_factory() as session:
        assert (
            session.get(DevelopmentWorkspaceRow, workspace_id).creation_json["record"][
                "checkpoint_id"
            ]
            == result.checkpoint_id
        )
