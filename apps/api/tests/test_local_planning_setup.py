from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.autonomous_worker.setup import configure_task_actor
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
