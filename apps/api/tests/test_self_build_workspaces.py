"""Real native task/runtime ownership, recovery, authority and migration regressions."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select

from app.agent_runtime.errors import AgentRuntimeError
from app.core.errors import DomainError
from app.db.models import AuditEventRow, DevelopmentWorkspaceRow, OutboxEventRow, TaskLeaseRow
from app.main import create_app
from app.models.agent_runtime import (
    ClaimAgentRunCommand,
    CreateAgentRunCommand,
    QueueAgentRunCommand,
)
from app.models.identity import AssignPermissionRequest, CreatePermissionRequest
from app.models.self_build import (
    AbandonWorkspaceRequest,
    ApproveWorkspaceRequest,
    ReserveWorkspaceRequest,
    WorkspaceIntent,
)
from app.self_build.service import WorkspaceService
from tests.agent_runtime_testkit import make_spec, ts
from tests.test_agent_runtime_sql_control_plane import grant_runtime_permissions
from tests.test_autonomous_worker import queue_only_demo_task
from tests.test_autonomous_worker_migration import migration_config
from tests.test_persistence import database_url


@pytest.fixture
def workspace(tmp_path):
    primary, worktrees = tmp_path / "repository", tmp_path / "worktrees"
    primary.mkdir()
    worktrees.mkdir()
    app = create_app(database_url=database_url(tmp_path / "workspace.db"))
    app.state.settings.self_build_enabled = True
    policy = dict(
        repository_identity="github.com/example/jarvis",
        primary_root=str(primary),
        worktree_root=str(worktrees),
        base_branch="main",
    )
    app.state.settings.self_build_repositories_json = json.dumps({"jarvis": policy})
    queue_only_demo_task(app)
    actor_id = grant_runtime_permissions(app, "builder", task_id="task-demo")
    actor = app.state.agent_runtime_service.authenticate_actor(actor_id)
    permission = app.state.identity_service.create_definition(
        "permission",
        CreatePermissionRequest(
            stable_key="self_build.workspace",
            display_name="Reserve workspace",
            resource_type="task",
            action="workspace",
        ),
    )
    scope = dict(permission_id=permission.id, resource_type="task", resource_id="task-demo")
    app.state.identity_service.assign_permission(
        actor_id, AssignPermissionRequest(effect="allow", **scope)
    )
    runtime = app.state.agent_runtime_service
    runtime.handle_authorized(
        CreateAgentRunCommand(
            specification=make_spec(agent_id=actor_id, task_id="task-demo"),
            command_id="create",
            expected_run_version=0,
            timestamp=ts(0),
        ),
        actor,
    )
    runtime.handle_authorized(
        QueueAgentRunCommand(
            run_id="run-1", command_id="queue", expected_run_version=1, timestamp=ts(1)
        ),
        actor,
    )
    worker = app.state.task_leases.register_worker("builder", "builder", lease_seconds=3600)
    lease = app.state.task_leases.acquire_task(worker.id, lease_seconds=3600, task_id="task-demo")
    assert lease is not None
    runtime.handle_authorized(
        ClaimAgentRunCommand(
            run_id="run-1",
            command_id="claim",
            expected_run_version=2,
            timestamp=ts(2),
            executor_reference=worker.id,
        ),
        actor,
    )
    service = app.state.self_build_workspace_service
    intent = WorkspaceIntent(repository_id="jarvis", runtime_run_id="run-1", base_sha="a" * 40)
    plan = service.preview(actor, intent)
    from types import SimpleNamespace

    from pydantic import SecretStr

    from app.db.models import IdentityPermissionRow
    from app.remote_control.access import RemoteControlAccess
    from tests.test_agent_runtime_authorization import create_actor

    operator_id = create_actor(app, "workspace-operator")
    with app.state.repository.session_factory() as session:
        permissions = list(
            session.scalars(
                select(IdentityPermissionRow).where(
                    IdentityPermissionRow.stable_key.in_(
                        ["runtime.read", "runtime.execute", "self_build.workspace"]
                    )
                )
            )
        )
    for permission in permissions:
        app.state.identity_service.assign_permission(
            operator_id,
            AssignPermissionRequest(
                permission_id=permission.id,
                effect="allow",
                resource_type="task",
                resource_id="task-demo",
            ),
        )
    remote_permission = app.state.identity_service.create_definition(
        "permission",
        CreatePermissionRequest(
            stable_key="remote.control",
            display_name="Operator control",
            resource_type="administrative_function",
            action="control",
        ),
    )
    app.state.identity_service.assign_permission(
        operator_id,
        AssignPermissionRequest(
            permission_id=remote_permission.id,
            effect="allow",
            resource_type="administrative_function",
            resource_id="remote_control",
        ),
    )
    access = RemoteControlAccess(app.state.identity_service, operator_id, SecretStr("x" * 48))
    app.state.remote_control_service = SimpleNamespace(access=access)
    operator = access.authenticate("Bearer " + "x" * 48, secure_transport=True)
    approval = service.approve(
        operator,
        ApproveWorkspaceRequest(
            **intent.model_dump(), expected_plan_hash=plan.plan_hash, valid_for_seconds=3600
        ),
    )
    request = ReserveWorkspaceRequest(
        **intent.model_dump(),
        expected_plan_hash=plan.plan_hash,
        approval_id=approval.approval_id,
        worker_id=worker.id,
        lease_token=lease[1].leaseToken,
    )
    yield app, actor, service, request, scope, policy
    app.state.engine.dispose()


def test_reservation_restart_replay_preserves_exact_plan_and_atomic_events(workspace):
    app, actor, service, request, _, policy = workspace
    reservation = service.reserve(actor, request)
    assert reservation.state == "reserved" and reservation.checkout_state == "unobserved"
    assert not reservation.recovery_required
    assert reservation.plan.base_sha == request.base_sha
    assert reservation.plan.branch.startswith("codex/jarvis-")
    assert WorkspaceService(app).reserve(actor, request) == reservation
    assert WorkspaceService(app).read(actor, reservation.workspace_id) == reservation
    assert list(__import__("pathlib").Path(policy["worktree_root"]).iterdir()) == []
    assert request.lease_token not in reservation.model_dump_json()
    assert policy["primary_root"] not in reservation.model_dump_json()
    with app.state.repository.session_factory() as session:
        rows = list(session.scalars(select(DevelopmentWorkspaceRow)))
        assert len(rows) == 1
        for model in (AuditEventRow, OutboxEventRow):
            events = list(
                session.scalars(
                    select(model).where(model.event_type == "self_build.workspace.reserved")
                )
            )
            assert len(events) == 1
            assert request.lease_token not in str(events[0].__dict__)
        assert session.get(TaskLeaseRow, "task-demo").lease_token == request.lease_token


def test_concurrent_reservations_converge_without_duplicate_work(workspace):
    _, actor, service, request, _, _ = workspace
    with ThreadPoolExecutor(max_workers=2) as pool:
        records = list(pool.map(lambda _: service.reserve(actor, request), range(2)))
    assert records[0] == records[1]


@pytest.mark.parametrize("boundary", ["deny", "suspend", "stop", "lease", "hash", "disabled"])
def test_mutation_fences_leave_no_reservation(workspace, boundary):
    app, actor, service, request, scope, _ = workspace
    if boundary == "deny":
        app.state.identity_service.assign_permission(
            actor.actor_id, AssignPermissionRequest(effect="deny", **scope)
        )
    elif boundary == "suspend":
        app.state.identity_service.transition(actor.actor_id, "suspended")
    elif boundary == "stop":
        app.state.repository.emergency_stop = True
        app.state.repository.persist()
    elif boundary == "lease":
        request = request.model_copy(update={"lease_token": "stale"})
    elif boundary == "hash":
        request = request.model_copy(update={"expected_plan_hash": "0" * 64})
    else:
        app.state.settings.self_build_enabled = False
    with pytest.raises((DomainError, AgentRuntimeError)):
        service.reserve(actor, request)
    with app.state.repository.session_factory() as session:
        assert session.scalar(select(DevelopmentWorkspaceRow)) is None
        assert (
            session.scalar(
                select(OutboxEventRow).where(
                    OutboxEventRow.event_type == "self_build.workspace.reserved"
                )
            )
            is None
        )


def test_expired_ownership_is_visible_and_cannot_create_duplicate(workspace):
    app, actor, service, request, _, _ = workspace
    reservation = service.reserve(actor, request)
    with app.state.task_leases._write() as session:
        session.get(TaskLeaseRow, "task-demo").expires_at = datetime.now(UTC) - timedelta(seconds=1)
    recovered = service.read(actor, reservation.workspace_id)
    assert recovered.recovery_required and recovered.recovery_reason == "lease_lost"
    with pytest.raises(DomainError, match="lease"):
        service.reserve(actor, request)
    assert recovered.plan == reservation.plan


def test_policy_changes_invalidate_approval_and_preserve_old_ownership(workspace):
    app, actor, service, request, _, policy = workspace
    reservation = service.reserve(actor, request)
    app.state.settings.self_build_repositories_json = json.dumps(
        {"jarvis": policy | {"base_branch": "release"}}
    )
    assert service.read(actor, reservation.workspace_id).recovery_reason == "policy_changed"
    with pytest.raises(DomainError) as error:
        service.reserve(actor, request)
    assert error.value.code == "SELF_BUILD_PLAN_CHANGED"


def test_abandon_is_versioned_and_does_not_touch_files_or_task_lease(workspace):
    app, actor, service, request, _, policy = workspace
    from pathlib import Path

    unrelated = Path(policy["worktree_root"]) / "another-session"
    unrelated.mkdir()
    file = unrelated / "important.txt"
    file.write_text("preserve")
    reservation = service.reserve(actor, request)
    result = service.abandon(
        actor, reservation.workspace_id, AbandonWorkspaceRequest(expected_version=1)
    )
    assert result.state == "abandoned" and result.version == 2
    assert (
        service.abandon(
            actor, reservation.workspace_id, AbandonWorkspaceRequest(expected_version=2)
        )
        == result
    )
    with pytest.raises(DomainError) as error:
        service.abandon(
            actor, reservation.workspace_id, AbandonWorkspaceRequest(expected_version=1)
        )
    assert error.value.code == "SELF_BUILD_VERSION_CONFLICT"
    assert file.read_text() == "preserve"
    with app.state.repository.session_factory() as session:
        assert session.get(TaskLeaseRow, "task-demo").lease_token == request.lease_token


def test_http_contract_rejects_model_paths_and_checks_actor(workspace):
    app, actor, _, request, _, _ = workspace
    with TestClient(app) as client:
        headers = {"X-Jarvis-Actor-Id": actor.actor_id}
        response = client.post(
            "/api/self-build/workspaces/reserve",
            json=request.model_dump(mode="json"),
            headers=headers,
        )
        assert response.status_code == 200, response.text
        assert response.json()["meta"]["schemaVersion"] == "1.0"
        assert (
            client.post(
                "/api/self-build/workspaces/reserve",
                json=request.model_dump(mode="json") | {"worktree_root": "C:/"},
                headers=headers,
            ).status_code
            == 422
        )
        assert (
            client.get(
                "/api/self-build/workspaces/" + response.json()["data"]["workspace_id"]
            ).status_code
            == 401
        )


@pytest.mark.parametrize("mutation", ["overlap", "identity", "relative", "traversal", "branch"])
def test_unsafe_operator_policy_fails_closed(workspace, mutation):
    app, actor, service, request, _, policy = workspace
    changes = {
        "overlap": {"worktree_root": policy["primary_root"]},
        "identity": {"repository_identity": "https://user:password@github.com/example/jarvis"},
        "relative": {"worktree_root": "relative"},
        "traversal": {"worktree_root": policy["worktree_root"] + "/../worktrees"},
        "branch": {"base_branch": "--upload-pack=evil"},
    }
    app.state.settings.self_build_repositories_json = json.dumps(
        {"jarvis": policy | changes[mutation]}
    )
    with pytest.raises(DomainError) as error:
        service.preview(actor, request)
    assert error.value.code == "SELF_BUILD_POLICY_INVALID"


def test_workspace_migration_empty_roundtrip_and_populated_guard(workspace, tmp_path):
    app, actor, service, request, _, _ = workspace
    path = tmp_path / "roundtrip.db"
    config = migration_config(path)
    command.upgrade(config, "head")
    from sqlalchemy import create_engine

    engine = create_engine(database_url(path))
    assert "development_workspaces" in inspect(engine).get_table_names()
    command.downgrade(config, "20260907_11")
    assert "development_workspaces" not in inspect(engine).get_table_names()
    command.upgrade(config, "head")
    engine.dispose()
    service.reserve(actor, request)
    guarded = migration_config(__import__("pathlib").Path(app.state.engine.url.database))
    with pytest.raises(RuntimeError, match="Export development workspace history"):
        command.downgrade(guarded, "20260907_11")
    # SQLite DDL from newer empty revisions can commit before an older populated
    # downgrade guard rejects the chain. Restore head before using current ORM models.
    command.upgrade(guarded, "head")
    assert service.read(actor, service.preview(actor, request).worktree_key).state == "reserved"


def test_real_application_restart_preserves_workspace_and_does_not_create_files(workspace):
    app, actor, service, request, _, _ = workspace
    reservation = service.reserve(actor, request)
    restarted = create_app(database_url=str(app.state.engine.url))
    try:
        restarted.state.settings.self_build_enabled = True
        restarted.state.settings.self_build_repositories_json = (
            app.state.settings.self_build_repositories_json
        )
        assert (
            restarted.state.self_build_workspace_service.read(actor, reservation.workspace_id)
            == reservation
        )
        from types import SimpleNamespace

        from pydantic import SecretStr

        from app.remote_control.access import RemoteControlAccess

        restarted.state.remote_control_service = SimpleNamespace(
            access=RemoteControlAccess(
                restarted.state.identity_service,
                app.state.remote_control_service.access.actor_id,
                SecretStr("x" * 48),
            )
        )
        assert restarted.state.self_build_workspace_service.reserve(actor, request) == reservation
    finally:
        restarted.state.engine.dispose()


def test_native_runtime_cancellation_invalidates_workspace_owner(workspace):
    from app.models.agent_runtime import RequestCancellationCommand

    app, actor, service, request, _, _ = workspace
    reservation = service.reserve(actor, request)
    app.state.agent_runtime_service.handle_authorized(
        RequestCancellationCommand(
            run_id="run-1",
            command_id="cancel",
            expected_run_version=3,
            timestamp=ts(3),
            reason_code="operator_cancel",
            requester_reference=actor.actor_id,
            detail="Cancel development mission",
        ),
        actor,
    )
    assert service.read(actor, reservation.workspace_id).recovery_reason == "runtime_inactive"
    with pytest.raises(DomainError) as error:
        service.reserve(actor, request)
    assert error.value.code == "SELF_BUILD_OWNER_INACTIVE"


def test_successor_lease_cannot_replay_old_workspace_authority(workspace):
    app, actor, service, request, _, _ = workspace
    reservation = service.reserve(actor, request)
    with app.state.task_leases._write() as session:
        session.get(TaskLeaseRow, "task-demo").expires_at = datetime.now(UTC) - timedelta(seconds=1)
    app.state.task_leases.recover_expired_leases()
    successor = app.state.task_leases.register_worker("successor", "successor", lease_seconds=3600)
    acquired = app.state.task_leases.acquire_task(
        successor.id, task_id="task-demo", lease_seconds=3600
    )
    assert acquired is not None
    retry = request.model_copy(
        update={"worker_id": successor.id, "lease_token": acquired[1].leaseToken}
    )
    with pytest.raises(DomainError) as error:
        service.reserve(actor, retry)
    assert error.value.code == "SELF_BUILD_EXECUTOR_MISMATCH"
    assert service.read(actor, reservation.workspace_id).plan == reservation.plan


def test_private_policy_and_plan_tampering_fail_closed(workspace):
    app, actor, service, request, _, _ = workspace
    reservation = service.reserve(actor, request)
    with app.state.task_leases._write() as session:
        row = session.get(DevelopmentWorkspaceRow, reservation.workspace_id)
        row.policy_json = row.policy_json | {"base_branch": "malicious"}
    with pytest.raises(DomainError) as error:
        service.read(actor, reservation.workspace_id)
    assert error.value.code == "SELF_BUILD_RECORD_INVALID"


def test_runtime_read_permission_does_not_grant_workspace_mutation(workspace):
    app, actor, service, request, scope, _ = workspace
    app.state.identity_service.assign_permission(
        actor.actor_id, AssignPermissionRequest(effect="deny", **scope)
    )
    assert service.preview(actor, request).plan_hash == request.expected_plan_hash
    with pytest.raises(DomainError) as error:
        service.reserve(actor, request)
    assert error.value.code == "SELF_BUILD_PERMISSION_DENIED"


def test_runtime_execution_revocation_blocks_workspace_reservation(workspace):
    from app.db.models import IdentityPermissionRow

    app, actor, service, request, _, _ = workspace
    with app.state.repository.session_factory() as session:
        permission = session.scalar(
            select(IdentityPermissionRow).where(
                IdentityPermissionRow.stable_key == "runtime.execute"
            )
        )
    app.state.identity_service.assign_permission(
        actor.actor_id,
        AssignPermissionRequest(
            permission_id=permission.id,
            effect="deny",
            resource_type="task",
            resource_id="task-demo",
        ),
    )
    with pytest.raises(AgentRuntimeError):
        service.reserve(actor, request)


def test_registered_junction_or_symlink_root_is_rejected(workspace, tmp_path):
    import os

    app, actor, service, request, _, policy = workspace
    link = tmp_path / "worktree-alias"
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(policy["worktree_root"], str(link))
    else:
        from pathlib import Path

        link.symlink_to(Path(policy["worktree_root"]), target_is_directory=True)
    app.state.settings.self_build_repositories_json = json.dumps(
        {"jarvis": policy | {"worktree_root": str(link)}}
    )
    with pytest.raises(DomainError) as error:
        service.preview(actor, request)
    assert error.value.code == "SELF_BUILD_POLICY_INVALID"


@pytest.mark.parametrize("boundary", ["missing", "expired", "wrong_plan", "revoked", "worker"])
def test_operator_approval_is_durable_exact_and_live(workspace, boundary):
    app, actor, service, request, _, _ = workspace
    if boundary == "missing":
        request = request.model_copy(update={"approval_id": "audit-missing"})
    elif boundary in {"expired", "wrong_plan"}:
        with app.state.task_leases._write() as session:
            row = session.get(AuditEventRow, request.approval_id)
            payload = row.payload["payload"] | (
                {"expiresAt": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()}
                if boundary == "expired"
                else {"planHash": "0" * 64}
            )
            # Deliberate corruption fixture; production history remains append-only.
            row.payload = row.payload | {"payload": payload}
    elif boundary == "revoked":
        app.state.identity_service.transition(
            app.state.remote_control_service.access.actor_id, "suspended"
        )
    else:
        with pytest.raises(DomainError):
            service.approve(
                actor,
                ApproveWorkspaceRequest(
                    **request.model_dump(
                        include={
                            "repository_id",
                            "runtime_run_id",
                            "base_sha",
                            "expected_plan_hash",
                        }
                    )
                ),
            )
        request = request.model_copy(update={"approval_id": "audit-missing"})
    with pytest.raises((DomainError, AgentRuntimeError)):
        service.reserve(actor, request)
    with app.state.repository.session_factory() as session:
        assert session.scalar(select(DevelopmentWorkspaceRow)) is None
        assert (
            session.scalar(
                select(OutboxEventRow).where(
                    OutboxEventRow.event_type == "self_build.workspace.reserved"
                )
            )
            is None
        )


def test_successor_lease_cannot_create_first_reservation_for_old_runtime(workspace):
    app, actor, service, request, _, _ = workspace
    with app.state.task_leases._write() as session:
        session.get(TaskLeaseRow, "task-demo").expires_at = datetime.now(UTC) - timedelta(seconds=1)
    app.state.task_leases.recover_expired_leases()
    worker = app.state.task_leases.register_worker("new", "new", lease_seconds=3600)
    lease = app.state.task_leases.acquire_task(worker.id, task_id="task-demo", lease_seconds=3600)
    assert lease is not None
    with pytest.raises(DomainError) as failure:
        service.reserve(
            actor,
            request.model_copy(update={"worker_id": worker.id, "lease_token": lease[1].leaseToken}),
        )
    assert failure.value.code == "SELF_BUILD_EXECUTOR_MISMATCH"


def test_operator_approval_http_requires_bearer_not_actor_header(workspace):
    from app.remote_control.router import router

    app, actor, _, request, _, _ = workspace
    app.include_router(router)
    body = ApproveWorkspaceRequest(
        **request.model_dump(
            include={"repository_id", "runtime_run_id", "base_sha", "expected_plan_hash"}
        )
    ).model_dump(mode="json")
    with TestClient(app, base_url="https://testserver") as client:
        path = "/api/remote/self-build/workspaces/approve"
        assert (
            client.post(
                path,
                json=body,
                headers={"X-Jarvis-Actor-Id": app.state.remote_control_service.access.actor_id},
            ).status_code
            == 401
        )
        response = client.post(path, json=body, headers={"Authorization": "Bearer " + "x" * 48})
        assert response.status_code == 200, response.text
        assert response.json()["data"]["approved_by"] != actor.actor_id
        assert client.post(
            "/api/self-build/workspaces/approve",
            json=body,
            headers={"X-Jarvis-Actor-Id": actor.actor_id},
        ).status_code in {404, 405}


@pytest.mark.parametrize("mismatch", [False, True])
def test_active_attempt_executor_owns_workspace(workspace, mismatch):
    from app.models.agent_runtime import BeginAttemptCommand, StartAttemptCommand

    app, actor, service, request, _, _ = workspace
    runtime = app.state.agent_runtime_service
    result = runtime.handle_authorized(
        BeginAttemptCommand(
            run_id="run-1",
            command_id="begin",
            expected_run_version=3,
            timestamp=ts(3),
            executor_reference="other-worker" if mismatch else request.worker_id,
        ),
        actor,
    )
    runtime.handle_authorized(
        StartAttemptCommand(
            run_id="run-1",
            command_id="start",
            expected_run_version=result.snapshot.version,
            timestamp=ts(4),
            attempt_id=result.snapshot.active_attempt_id,
        ),
        actor,
    )
    if mismatch:
        with pytest.raises(DomainError) as failure:
            service.reserve(actor, request)
        assert failure.value.code == "SELF_BUILD_EXECUTOR_MISMATCH"
    else:
        assert not service.reserve(actor, request).recovery_required


def test_configured_runtime_agent_cannot_self_approve(workspace):
    from types import SimpleNamespace

    from pydantic import SecretStr

    from app.db.models import IdentityPermissionRow
    from app.remote_control.access import RemoteControlAccess

    app, actor, service, request, _, _ = workspace
    with app.state.repository.session_factory() as session:
        permission = session.scalar(
            select(IdentityPermissionRow).where(
                IdentityPermissionRow.stable_key == "remote.control"
            )
        )
    app.state.identity_service.assign_permission(
        actor.actor_id,
        AssignPermissionRequest(
            permission_id=permission.id,
            effect="allow",
            resource_type="administrative_function",
            resource_id="remote_control",
        ),
    )
    app.state.remote_control_service = SimpleNamespace(
        access=RemoteControlAccess(app.state.identity_service, actor.actor_id, SecretStr("x" * 48))
    )
    with pytest.raises(DomainError) as failure:
        service.approve(
            actor,
            ApproveWorkspaceRequest(
                **request.model_dump(
                    include={"repository_id", "runtime_run_id", "base_sha", "expected_plan_hash"}
                )
            ),
        )
    assert failure.value.code == "SELF_BUILD_SELF_APPROVAL_DENIED"


@pytest.mark.parametrize(
    "field,value",
    [
        ("event_sequence_number", 2),
        ("active_attempt_id", "unrelated-attempt"),
        ("agent_id", "different-agent"),
    ],
)
def test_runtime_ownership_lineage_tampering_fails_closed(workspace, field, value):
    from app.db.models import AgentRuntimeRunRow

    app, actor, service, request, _, _ = workspace
    with app.state.task_leases._write() as session:
        setattr(session.get(AgentRuntimeRunRow, "run-1"), field, value)
    with pytest.raises(DomainError) as failure:
        service.reserve(actor, request)
    assert failure.value.code == "SELF_BUILD_LINEAGE_INVALID"


def test_fresh_identity_approval_conflicts_with_existing_uniqueness_owner(workspace):
    app, actor, service, request, _, policy = workspace
    reservation = service.reserve(actor, request)
    app.state.settings.self_build_repositories_json = json.dumps(
        {"jarvis": policy | {"repository_identity": "github.com/example/renamed"}}
    )
    intent = WorkspaceIntent(
        repository_id=request.repository_id,
        runtime_run_id=request.runtime_run_id,
        base_sha=request.base_sha,
    )
    fresh = service.preview(actor, intent)
    assert fresh.worktree_key != reservation.workspace_id
    operator = app.state.remote_control_service.access.authenticate(
        "Bearer " + "x" * 48, secure_transport=True
    )
    approval = service.approve(
        operator,
        ApproveWorkspaceRequest(
            **intent.model_dump(),
            expected_plan_hash=fresh.plan_hash,
        ),
    )
    fresh_request = request.model_copy(
        update={
            "expected_plan_hash": fresh.plan_hash,
            "approval_id": approval.approval_id,
        }
    )
    with pytest.raises(DomainError) as failure:
        service.reserve(actor, fresh_request)
    assert failure.value.code == "SELF_BUILD_WORKSPACE_CONFLICT"
    with app.state.repository.session_factory() as session:
        rows = list(session.scalars(select(DevelopmentWorkspaceRow)))
        assert len(rows) == 1 and rows[0].id == reservation.workspace_id
        events = list(
            session.scalars(
                select(AuditEventRow).where(
                    AuditEventRow.event_type == "self_build.workspace.reserved"
                )
            )
        )
        assert len(events) == 1
    assert list(__import__("pathlib").Path(policy["worktree_root"]).iterdir()) == []
