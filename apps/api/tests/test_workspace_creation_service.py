"""Native creation intent, exact authority, checkpoint crash gaps and migration."""

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from fastapi.testclient import TestClient
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


@pytest.mark.parametrize("gap", ["before_checkpoint", "acknowledged"])
def test_preparation_invalidates_prior_abandonment_approval(creation, gap, monkeypatch):
    from app.models.self_build import AbandonWorkspaceRequest, ApproveWorkspaceAbandonRequest

    app, actor, operator, service, workspace_id, request, _ = creation
    workspace = app.state.self_build_workspace_service
    prior = workspace.preview_abandon(actor, workspace_id)
    assert prior.creation_intent_digest is None
    approval = workspace.approve_abandon(
        operator,
        ApproveWorkspaceAbandonRequest(
            workspace_id=workspace_id, expected_plan_hash=prior.plan_hash
        ),
    )
    abandonment = AbandonWorkspaceRequest(
        expected_version=1,
        expected_plan_hash=prior.plan_hash,
        approval_id=approval.approval_id,
        worker_id=request.worker_id,
        lease_token=request.lease_token,
    )
    if gap == "before_checkpoint":

        def crash(*args, **kwargs):
            raise RuntimeError("crash before native checkpoint")

        monkeypatch.setattr(service.runtime, "handle_authorized", crash)
        with pytest.raises(RuntimeError, match="crash before native checkpoint"):
            service.prepare(actor, workspace_id, request)
    else:
        service.prepare(actor, workspace_id, request)
    current = workspace.preview_abandon(actor, workspace_id)
    assert current.creation_intent_digest is not None and current.plan_hash != prior.plan_hash
    with pytest.raises(DomainError) as failure:
        workspace.abandon(actor, workspace_id, abandonment)
    assert failure.value.code == "SELF_BUILD_PLAN_CHANGED"
    assert workspace.read(actor, workspace_id).state == "reserved"
    with app.state.repository.session_factory() as session:
        private = session.get(DevelopmentWorkspaceRow, workspace_id).creation_json
    fresh = workspace.approve_abandon(
        operator,
        ApproveWorkspaceAbandonRequest(
            workspace_id=workspace_id, expected_plan_hash=current.plan_hash
        ),
    )
    result = workspace.abandon(
        actor,
        workspace_id,
        abandonment.model_copy(
            update={"expected_plan_hash": current.plan_hash, "approval_id": fresh.approval_id}
        ),
    )
    assert result.state == "abandoned"
    with app.state.repository.session_factory() as session:
        assert session.get(DevelopmentWorkspaceRow, workspace_id).creation_json == private


def test_native_creation_ready_checkpoints_and_replay_preserve_primary(creation):
    app, actor, _, service, workspace_id, request, _ = creation
    policy = service.workspace.policies.get("jarvis")
    primary = Path(policy.primary_root)
    index = (primary / ".git/index").read_bytes()
    result = service.create(actor, workspace_id, request)
    assert result.state == "ready"
    assert result.completed_file_count == result.plan.file_count
    assert result.registration_digest and result.source_digest and result.checkpoint_id
    assert WorkspaceCreationService(app).read(actor, workspace_id) == result
    assert WorkspaceCreationService(app).create(actor, workspace_id, request) == result
    checkpoints = service.runtime.checkpoints_authorized("run-1", actor)
    assert {item.resume_cursor for item in checkpoints} == {
        "prepared",
        "git_created",
        "materializing-1",
        "ready",
    }
    assert (primary / ".git/index").read_bytes() == index
    target = Path(policy.worktree_root) / workspace_id
    assert (target / "source.py").read_bytes() == b"value = 1\n"
    assert (target / ".jarvis-workspace.json").is_file()


@pytest.mark.parametrize("phase", ["git_created", "materializing-1", "ready"])
def test_native_creation_checkpoint_crash_resumes_same_owned_effects(creation, monkeypatch, phase):
    app, actor, _, service, workspace_id, request, _ = creation
    native = service.runtime.handle_authorized

    def crash(command, *args, **kwargs):
        result = native(command, *args, **kwargs)
        if getattr(command, "resume_cursor", None) == phase:
            raise RuntimeError("after native phase before acknowledgement")
        return result

    monkeypatch.setattr(service.runtime, "handle_authorized", crash)
    with pytest.raises(RuntimeError, match="before acknowledgement"):
        service.create(actor, workspace_id, request)
    pending = service.read(actor, workspace_id)
    assert pending.checkpoint_id is None
    original_operation = pending.operation_id
    monkeypatch.setattr(service.runtime, "handle_authorized", native)
    result = WorkspaceCreationService(app).create(actor, workspace_id, request)
    assert result.state == "ready" and result.operation_id == original_operation
    assert len(service.runtime.checkpoints_authorized("run-1", actor)) == 4


def test_production_http_creation_requires_separate_operator_and_returns_native_ready(creation):
    from app.self_build.git_operator_router import router

    app, actor, _, _, workspace_id, request, _ = creation
    # Fixture enables remote access after create_app; production startup registers
    # this router only when remote_control_enabled is configured at startup.
    app.include_router(router)
    actor_headers = {"X-Jarvis-Actor-Id": actor.actor_id}
    with TestClient(app, base_url="https://testserver") as client:
        preview = client.post(
            f"/api/self-build/workspaces/{workspace_id}/creation/preview",
            headers=actor_headers,
            json={"inspection_id": request.inspection_id},
        )
        assert preview.status_code == 200
        approval_body = dict(
            workspace_id=workspace_id,
            inspection_id=request.inspection_id,
            expected_plan_hash=preview.json()["data"]["plan_hash"],
        )
        denied = client.post(
            "/api/remote/self-build/workspace-creation/approve",
            headers=actor_headers,
            json=approval_body,
        )
        assert denied.status_code == 401
        approved = client.post(
            "/api/remote/self-build/workspace-creation/approve",
            headers={"Authorization": "Bearer " + "x" * 48},
            json=approval_body,
        )
        assert approved.status_code == 200
        current_request = request.model_copy(
            update={"approval_id": approved.json()["data"]["approval_id"]}
        )
        result = client.post(
            f"/api/self-build/workspaces/{workspace_id}/creation",
            headers=actor_headers,
            json=current_request.model_dump(mode="json"),
        )
        assert result.status_code == 200, result.text
        assert result.json()["data"]["state"] == "ready"
        observed = client.get(
            f"/api/self-build/workspaces/{workspace_id}/creation", headers=actor_headers
        )
        assert observed.status_code == 200 and observed.json()["data"] == result.json()["data"]
    schema = app.openapi()
    assert "WorkspaceCreationPlan" in schema["components"]["schemas"]
    assert "WorkspaceCreationRecord" in schema["components"]["schemas"]


def test_legacy_prepared_projection_remains_readable_after_schema_extension(creation):
    from app.self_build.creation_service import persisted_intent_digest
    from app.self_build.policy import digest

    _, actor, _, service, workspace_id, request, _ = creation
    service.prepare(actor, workspace_id, request)
    with service.repository.leases._write() as session:
        row = session.get(DevelopmentWorkspaceRow, workspace_id)
        private = dict(row.creation_json)
        raw = dict(private["record"])
        raw.pop("registration_digest")
        raw.pop("source_digest")
        raw["checkpoint_id"] = None
        plan = dict(raw["plan"])
        plan.pop("mutation_platform")
        plan["plan_hash"] = digest(
            {key: value for key, value in plan.items() if key != "plan_hash"}
        )
        raw["plan"] = plan
        raw["ownership_digest"] = digest(
            dict(
                workspaceId=workspace_id,
                operationId=raw["operation_id"],
                planHash=plan["plan_hash"],
                nonce=private["nonce"],
            )
        )
        private.update(record=raw, digest=persisted_intent_digest(raw, private["nonce"]))
        row.creation_json = private
    historical = service.read(actor, workspace_id)
    assert historical.state == "prepared" and historical.checkpoint_id is None
    with pytest.raises(DomainError) as failure:
        service.create(actor, workspace_id, request)
    assert failure.value.code == "SELF_BUILD_CREATION_CONFLICT"


def test_revocation_after_registration_preserves_effects_without_ready(creation, monkeypatch):
    _, actor, _, service, workspace_id, request, _ = creation
    transition = service.transition

    def stopped(actor, workspace_id, request, previous, **changes):
        if changes.get("state") == "git_created":
            with service.repository.leases._write() as session:
                session.get(SystemStateRow, 1).emergency_stop = True
        return transition(actor, workspace_id, request, previous, **changes)

    monkeypatch.setattr(service, "transition", stopped)
    with pytest.raises(DomainError) as failure:
        service.create(actor, workspace_id, request)
    assert failure.value.code == "EMERGENCY_STOP_ACTIVE"
    policy = service.workspace.policies.get("jarvis")
    target = Path(policy.worktree_root) / workspace_id
    assert (target / ".git").is_file() and not (target / "source.py").exists()
    assert service.read(actor, workspace_id).state == "prepared"


@pytest.mark.parametrize("after_native", [False, True])
def test_renewed_exact_operator_approval_recovers_original_preparation(
    creation, monkeypatch, after_native
):
    from app.self_build import creation_service

    _, actor, operator, service, workspace_id, request, _ = creation
    native = service.runtime.handle_authorized

    def crash(*args, **kwargs):
        if after_native:
            native(*args, **kwargs)
        raise RuntimeError("checkpoint crash gap")

    monkeypatch.setattr(service.runtime, "handle_authorized", crash)
    with pytest.raises(RuntimeError, match="crash gap"):
        service.prepare(actor, workspace_id, request)
    pending = service.read(actor, workspace_id)
    with service.repository.sessions() as session:
        original_private = dict(session.get(DevelopmentWorkspaceRow, workspace_id).creation_json)

    class ExpiredClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(seconds=1000)

    monkeypatch.setattr(creation_service, "datetime", ExpiredClock)
    with pytest.raises(DomainError) as failure:
        service.prepare(actor, workspace_id, request)
    assert failure.value.code == "SELF_BUILD_APPROVAL_REQUIRED"
    renewed = service.approve(
        operator,
        ApproveWorkspaceCreation(
            workspace_id=workspace_id,
            inspection_id=request.inspection_id,
            expected_plan_hash=request.expected_plan_hash,
        ),
    )
    monkeypatch.setattr(service.runtime, "handle_authorized", native)
    recovered = service.prepare(
        actor, workspace_id, request.model_copy(update={"approval_id": renewed.approval_id})
    )
    assert recovered.operation_id == pending.operation_id
    assert recovered.approval_id == pending.approval_id != renewed.approval_id
    assert (
        recovered.checkpoint_id and len(service.runtime.checkpoints_authorized("run-1", actor)) == 1
    )
    with service.repository.sessions() as session:
        current = session.get(DevelopmentWorkspaceRow, workspace_id).creation_json
        assert current["digest"] == original_private["digest"]
        assert current["nonce"] == original_private["nonce"]


@pytest.mark.parametrize("git_workspace", ["empty"], indirect=True)
def test_native_empty_base_has_complete_verified_ready_checkpoint(creation):
    _, actor, _, service, workspace_id, request, _ = creation
    result = service.create(actor, workspace_id, request)
    assert result.state == "ready" and result.completed_file_count == result.plan.file_count == 0
    assert result.source_digest and result.registration_digest and result.checkpoint_id
    assert {
        item.resume_cursor for item in service.runtime.checkpoints_authorized("run-1", actor)
    } == {"prepared", "git_created", "ready"}
    policy = service.workspace.policies.get("jarvis")
    assert {path.name for path in (Path(policy.worktree_root) / workspace_id).iterdir()} == {
        ".git",
        ".jarvis-workspace.json",
    }


def test_final_artifacts_remain_deny_write_pinned_through_native_ready_publication(
    creation, monkeypatch
):
    _, actor, _, service, workspace_id, request, _ = creation
    policy = service.workspace.policies.get("jarvis")
    target = Path(policy.worktree_root) / workspace_id
    index = Path(policy.primary_root) / ".git/worktrees" / workspace_id / "index"
    owner = Path(policy.worktree_root) / (".jarvis-owner-" + workspace_id) / "owner.json"
    native = service.runtime.handle_authorized
    blocked = []

    def guarded(command, *args, **kwargs):
        if getattr(command, "resume_cursor", None) == "ready":
            for path in (
                target / "source.py",
                target / ".git",
                target / ".jarvis-workspace.json",
                index,
                owner,
            ):
                with pytest.raises(PermissionError):
                    with path.open("r+b") as stream:
                        stream.write(b"foreign")
                blocked.append(path.name)
        return native(command, *args, **kwargs)

    monkeypatch.setattr(service.runtime, "handle_authorized", guarded)
    result = service.create(actor, workspace_id, request)
    assert result.state == "ready" and len(blocked) == 5
    assert (target / "source.py").read_bytes() == b"value = 1\n"
    # Publication releases every pin; later operator changes are separate effects.
    with (target / "source.py").open("r+b") as stream:
        stream.write(b"value = 2\n")


def test_finalization_recovery_reverifies_files_before_acknowledging_ready(creation, monkeypatch):
    _, actor, _, service, workspace_id, request, _ = creation
    native = service.runtime.handle_authorized

    def crash(command, *args, **kwargs):
        result = native(command, *args, **kwargs)
        if getattr(command, "resume_cursor", None) == "ready":
            raise RuntimeError("native ready before projection ack")
        return result

    monkeypatch.setattr(service.runtime, "handle_authorized", crash)
    with pytest.raises(RuntimeError, match="projection ack"):
        service.create(actor, workspace_id, request)
    target = Path(service.workspace.policies.get("jarvis").worktree_root) / workspace_id
    (target / "source.py").write_bytes(b"value = 9\n")
    monkeypatch.setattr(service.runtime, "handle_authorized", native)
    with pytest.raises(DomainError) as failure:
        service.create(actor, workspace_id, request)
    assert failure.value.code == "SELF_BUILD_MATERIALIZATION_CONFLICT"
    assert service.read(actor, workspace_id).state == "finalizing"
    assert (target / "source.py").read_bytes() == b"value = 9\n"
