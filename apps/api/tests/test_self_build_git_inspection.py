"""Native production-path approval, lease, outbox and read-only Git acceptance."""

import os
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.errors import DomainError
from app.db.models import AuditEventRow, OutboxEventRow, SystemStateRow
from app.models.git_inspection import ApproveRepositoryInspection, InspectRepositoryRequest
from app.models.identity import AssignPermissionRequest, CreatePermissionRequest
from app.models.self_build import ApproveWorkspaceRequest, WorkspaceIntent
from app.self_build.git_observer import GitObserver

pytest_plugins = ["tests.test_self_build_workspaces"]


@pytest.fixture
def git_workspace(workspace):
    app, actor, workspace_service, reservation_request, _, policy = workspace
    root = Path(policy["primary_root"])
    executable = shutil.which("git")
    if (
        executable
        and os.name == "nt"
        and (
            Path(executable).parent.name.casefold() == "cmd"
            or (
                Path(executable).parent.name.casefold() == "bin"
                and Path(executable).parent.parent.name.casefold() not in {"mingw32", "mingw64"}
            )
        )
    ):
        installation = Path(executable).parent.parent
        implementations = [
            installation / family / "bin/git.exe" for family in ("mingw64", "mingw32")
        ]
        executable = str(next(path for path in implementations if path.is_file()))
    assert executable, "Native Git is required for repository acceptance"

    def git(*args):
        return (
            subprocess.run([executable, *args], cwd=root, check=True, capture_output=True)
            .stdout.decode()
            .strip()
        )

    git("init", "--initial-branch=main")
    git("config", "user.name", "Isolated fixture")
    git("config", "user.email", "fixture@example.invalid")
    git("config", "commit.gpgsign", "false")
    (root / "source.py").write_text("value = 1\n")
    git("add", "source.py")
    git("commit", "-m", "base")
    head = git("rev-parse", "HEAD")
    git("remote", "add", "origin", "https://github.com/example/jarvis.git")
    git("update-ref", "refs/remotes/origin/main", head)
    app.state.settings.self_build_git_executable = executable
    app.state.settings.self_build_git_sha256 = sha256(Path(executable).read_bytes()).hexdigest()
    access = app.state.remote_control_service.access
    operator = access.authenticate("Bearer " + "x" * 48, secure_transport=True)
    permission = app.state.identity_service.create_definition(
        "permission",
        CreatePermissionRequest(
            stable_key="self_build.git.inspect",
            display_name="Read registered Git metadata",
            resource_type="task",
            action="git_inspect",
        ),
    )
    scope = dict(permission_id=permission.id, resource_type="task", resource_id="task-demo")
    for principal in (actor, operator):
        app.state.identity_service.assign_permission(
            principal.actor_id, AssignPermissionRequest(effect="allow", **scope)
        )
    intent = WorkspaceIntent(repository_id="jarvis", runtime_run_id="run-1", base_sha=head)
    plan = workspace_service.preview(actor, intent)
    approval = workspace_service.approve(
        operator, ApproveWorkspaceRequest(**intent.model_dump(), expected_plan_hash=plan.plan_hash)
    )
    request = reservation_request.model_copy(
        update={
            "base_sha": head,
            "expected_plan_hash": plan.plan_hash,
            "approval_id": approval.approval_id,
        }
    )
    reservation = workspace_service.reserve(actor, request)
    service = app.state.self_build_git_service
    inspection_plan = service.preview(actor, reservation.workspace_id)
    inspection_approval = service.approve(
        operator,
        ApproveRepositoryInspection(
            workspace_id=reservation.workspace_id, expected_plan_hash=inspection_plan.plan_hash
        ),
    )
    inspect_request = InspectRepositoryRequest(
        expected_plan_hash=inspection_plan.plan_hash,
        approval_id=inspection_approval.approval_id,
        worker_id=request.worker_id,
        lease_token=request.lease_token,
    )
    return app, actor, operator, service, reservation, inspect_request, root, scope


def test_production_inspection_preserves_dirty_source_and_persists_atomic_evidence(git_workspace):
    app, actor, _, service, reservation, request, root, _ = git_workspace
    (root / "source.py").write_text("operator edit\n")
    before = (root / ".git" / "index").read_bytes()
    result = service.inspect(actor, reservation.workspace_id, request)
    assert result.observation.base_sha == reservation.plan.base_sha
    assert (
        result.observation.file_count == 1 and result.observation.base_matches_origin_tracking_ref
    )
    assert result.observation.checkout_state == "unobserved"
    assert service.read(actor, reservation.workspace_id, result.inspection_id) == result
    assert (root / "source.py").read_text() == "operator edit\n"
    assert (root / ".git" / "index").read_bytes() == before
    assert (
        request.lease_token not in result.model_dump_json()
        and str(root) not in result.model_dump_json()
    )
    with app.state.repository.session_factory() as session:
        audit = session.get(AuditEventRow, result.inspection_id)
        assert audit.payload["payload"]["observation"] == result.observation.model_dump(mode="json")
        outbox = session.scalar(
            select(OutboxEventRow).where(
                OutboxEventRow.event_type == "self_build.git_inspection.observed"
            )
        )
        assert outbox is not None and outbox.status == "pending"
        assert outbox.sequence_number == audit.sequence_number
    with TestClient(app) as client:
        response = client.get(
            f"/api/self-build/workspaces/{reservation.workspace_id}/repository-inspections/{result.inspection_id}",
            headers={"X-Jarvis-Actor-Id": actor.actor_id},
        )
        assert response.status_code == 200 and response.json()["data"] == result.model_dump(
            mode="json"
        )


@pytest.mark.parametrize(
    "boundary",
    [
        "permission",
        "stop",
        "missing_approval",
        "plan",
        "lease",
        "tool",
        "operator_revoked",
        "approval_expired",
        "self_approved",
        "disabled",
    ],
)
def test_revoked_or_changed_authority_never_starts_git(git_workspace, monkeypatch, boundary):
    app, actor, _, service, reservation, request, _, scope = git_workspace
    if boundary == "permission":
        app.state.identity_service.assign_permission(
            actor.actor_id, AssignPermissionRequest(effect="deny", **scope)
        )
    elif boundary == "stop":
        with app.state.task_leases._write() as session:
            session.get(SystemStateRow, 1).emergency_stop = True
    elif boundary == "missing_approval":
        request = request.model_copy(update={"approval_id": "missing"})
    elif boundary == "plan":
        request = request.model_copy(update={"expected_plan_hash": "0" * 64})
    elif boundary == "lease":
        request = request.model_copy(update={"lease_token": "lost"})
    elif boundary == "tool":
        app.state.settings.self_build_git_sha256 = "0" * 64
    elif boundary == "operator_revoked":
        app.state.identity_service.transition(
            app.state.remote_control_service.access.actor_id, "suspended"
        )
    elif boundary == "disabled":
        app.state.settings.self_build_enabled = False
    else:
        with app.state.task_leases._write() as session:
            event = session.get(AuditEventRow, request.approval_id)
            if boundary == "self_approved":
                event.actor = actor.actor_id
            else:
                event.payload = event.payload | {
                    "payload": event.payload["payload"]
                    | {"expiresAt": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()}
                }

    def forbidden(*args, **kwargs):
        raise AssertionError("Git started after revocation")

    monkeypatch.setattr(GitObserver, "inspect", forbidden)
    with pytest.raises(DomainError):
        service.inspect(actor, reservation.workspace_id, request)


def test_stop_between_native_reads_prevents_further_git_and_evidence(git_workspace, monkeypatch):
    app, actor, _, service, reservation, request, _, _ = git_workspace
    original = GitObserver._read
    reads = []

    def stop_after_read(observer, policy, operation, base):
        result = original(observer, policy, operation, base)
        reads.append(operation)
        if len(reads) == 1:
            with app.state.task_leases._write() as session:
                session.get(SystemStateRow, 1).emergency_stop = True
        return result

    monkeypatch.setattr(GitObserver, "_read", stop_after_read)
    with pytest.raises(DomainError) as failure:
        service.inspect(actor, reservation.workspace_id, request)
    assert failure.value.code == "EMERGENCY_STOP_ACTIVE"
    assert reads == ["root"]
    with app.state.repository.session_factory() as session:
        assert (
            session.scalar(
                select(AuditEventRow).where(
                    AuditEventRow.event_type == "self_build.git_inspection.observed"
                )
            )
            is None
        )


def test_git_approval_http_requires_configured_operator_credential(git_workspace):
    from app.self_build.git_operator_router import router

    app, actor, _, service, reservation, _, _, _ = git_workspace
    app.include_router(router)
    plan = service.preview(actor, reservation.workspace_id)
    body = ApproveRepositoryInspection(
        workspace_id=reservation.workspace_id, expected_plan_hash=plan.plan_hash
    ).model_dump(mode="json")
    with TestClient(app, base_url="https://testserver") as client:
        path = "/api/remote/self-build/repository-inspections/approve"
        assert (
            client.post(path, json=body, headers={"X-Jarvis-Actor-Id": actor.actor_id}).status_code
            == 401
        )
        response = client.post(path, json=body, headers={"Authorization": "Bearer " + "x" * 48})
        assert response.status_code == 200, response.text
        assert response.json()["data"]["plan"] == plan.model_dump(mode="json")


def test_restart_preserves_observation_when_native_git_is_disabled(git_workspace):
    from app.main import create_app

    app, actor, _, service, reservation, request, _, _ = git_workspace
    result = service.inspect(actor, reservation.workspace_id, request)
    restarted = create_app(database_url=str(app.state.engine.url))
    try:
        assert not restarted.state.settings.self_build_enabled
        assert (
            restarted.state.self_build_git_service.read(
                actor, reservation.workspace_id, result.inspection_id
            )
            == result
        )
    finally:
        restarted.state.engine.dispose()


def test_revocation_after_git_read_prevents_successful_evidence(git_workspace, monkeypatch):
    app, actor, _, service, reservation, request, _, _ = git_workspace
    original = GitObserver.inspect

    def revoke_after_observation(observer, *args):
        result = original(observer, *args)
        with app.state.task_leases._write() as session:
            session.get(SystemStateRow, 1).emergency_stop = True
        return result

    monkeypatch.setattr(GitObserver, "inspect", revoke_after_observation)
    with pytest.raises(DomainError) as failure:
        service.inspect(actor, reservation.workspace_id, request)
    assert failure.value.code == "EMERGENCY_STOP_ACTIVE"
    with app.state.repository.session_factory() as session:
        assert (
            session.scalar(
                select(AuditEventRow).where(
                    AuditEventRow.event_type == "self_build.git_inspection.observed"
                )
            )
            is None
        )


def test_stored_observation_lineage_corruption_fails_closed(git_workspace):
    app, actor, _, service, reservation, request, _, _ = git_workspace
    result = service.inspect(actor, reservation.workspace_id, request)
    with app.state.task_leases._write() as session:
        event = session.get(AuditEventRow, result.inspection_id)
        payload = event.payload["payload"]
        event.payload = event.payload | {
            "payload": payload | {"observation": payload["observation"] | {"base_sha": "0" * 40}}
        }
    with pytest.raises(DomainError):
        service.read(actor, reservation.workspace_id, result.inspection_id)
