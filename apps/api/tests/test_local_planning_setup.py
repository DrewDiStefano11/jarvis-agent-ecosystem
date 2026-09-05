from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.autonomous_worker.provisioning import configure_task_actor
from app.core.errors import DomainError
from app.main import create_app
from tests.test_persistence import database_url


def test_setup_is_task_scoped_repeatable_and_keeps_execution_disabled(tmp_path: Path):
    app = create_app(database_url=database_url(tmp_path / "setup.db"))
    with TestClient(app):
        actor_id = configure_task_actor(app, "task-demo", "local-planner")
        assert configure_task_actor(app, "task-demo", "local-planner") == actor_id
        identity = app.state.identity_service
        assert identity.check_permission(actor_id, "runtime.execute", "task", "task-demo").allowed
        assert not identity.check_permission(
            actor_id, "runtime.execute", "task", "other-task"
        ).allowed
        assert not identity.check_permission(
            actor_id, "runtime.admin", "administrative_function", "agent_runtime"
        ).allowed
        assert not app.state.settings.autonomous_worker_enabled
        identity.transition(actor_id, "suspended")
        with pytest.raises(DomainError, match="reactivate"):
            configure_task_actor(app, "task-demo", "local-planner")


def test_missing_task_does_not_create_an_identity(tmp_path: Path):
    app = create_app(database_url=database_url(tmp_path / "missing.db"))
    with TestClient(app):
        before = len(app.state.identity_service.list_agents(0, 100))
        with pytest.raises(DomainError):
            configure_task_actor(app, "missing-task", "local-planner")
        assert len(app.state.identity_service.list_agents(0, 100)) == before


def test_setup_endpoint_reuses_configured_identity_without_enabling_execution(tmp_path: Path):
    app = create_app(database_url=database_url(tmp_path / "ui-setup.db"))
    with TestClient(app) as client:
        first = client.post("/api/local-planning/setup", json={"taskId": "task-demo"})
        assert first.status_code == 200
        data = first.json()["data"]
        assert data["executionEnabledBySetup"] is False
        assert data["workerActorConfigured"] is False
        assert (
            client.post("/api/local-planning/setup", json={"taskId": "task-demo"}).json()["data"]
            == data
        )
        actor = configure_task_actor(app, "task-demo", "custom-configured-worker")
        app.state.settings.autonomous_worker_actor_id = actor
        configured = client.post("/api/local-planning/setup", json={"taskId": "task-demo"}).json()[
            "data"
        ]
        assert configured["actorId"] == actor
        assert configured["workerActorConfigured"] is True
        assert not app.state.settings.autonomous_worker_enabled
        assert not app.state.identity_service.check_permission(
            actor, "runtime.execute", "task", "other-task"
        ).allowed
        app.state.identity_service.transition(actor, "suspended")
        assert (
            client.post("/api/local-planning/setup", json={"taskId": "task-demo"}).status_code
            == 409
        )


def test_setup_endpoint_rejects_remote_callers_and_unknown_tasks(tmp_path: Path):
    app = create_app(database_url=database_url(tmp_path / "ui-denied.db"))
    with TestClient(app) as client:
        assert (
            client.post("/api/local-planning/setup", json={"taskId": "missing"}).status_code == 404
        )
        assert not app.state.identity_service.list_agents(0, 100)
        assert (
            client.post(
                "/api/local-planning/setup", json={"taskId": "task-demo", "grantAdmin": True}
            ).status_code
            == 422
        )
    with TestClient(
        create_app(database_url=database_url(tmp_path / "ui-denied.db")),
        client=("192.0.2.10", 4567),
    ) as remote:
        assert (
            remote.post("/api/local-planning/setup", json={"taskId": "task-demo"}).status_code
            == 403
        )


def test_setup_endpoint_preserves_existing_denial(tmp_path: Path):
    from app.models.identity import AssignPermissionRequest

    app = create_app(database_url=database_url(tmp_path / "ui-policy.db"))
    with TestClient(app) as client:
        response = client.post("/api/local-planning/setup", json={"taskId": "task-demo"})
        actor = response.json()["data"]["actorId"]
        service = app.state.identity_service
        permission = next(
            item
            for item in service.list_definitions("permission", 0, 100)
            if item.stable_key == "runtime.execute"
        )
        service.assign_permission(
            actor,
            AssignPermissionRequest(
                permission_id=permission.id,
                effect="deny",
                resource_type="task",
                resource_id="task-demo",
                reason="Explicit operator restriction",
            ),
        )
        denied = client.post("/api/local-planning/setup", json={"taskId": "task-demo"})
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "LOCAL_PLANNING_PERMISSION_DENIED"
        assert not service.check_permission(actor, "runtime.execute", "task", "task-demo").allowed
