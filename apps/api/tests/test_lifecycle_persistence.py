from fastapi.testclient import TestClient

from app.db.models import SystemStateRow, TaskRow
from app.main import create_app
from tests.test_persistence import database_url


def test_api_lifecycle_preserves_separate_worker_task_and_system_updates(tmp_path):
    url = database_url(tmp_path / "lifecycle.db")
    app = create_app(database_url=url)

    def worker_update(status, sequence):
        with app.state.repository.session_factory() as session, session.begin():
            task = session.get(TaskRow, "task-demo")
            task.status = status
            task.project_id = "worker-project"
            task.payload = task.payload | {"status": status, "projectId": "worker-project"}
            system = session.get(SystemStateRow, 1)
            system.event_session_id = "worker-session"
            system.current_sequence_number = sequence
            system.emergency_stop = True
            return task.payload

    def assert_system(sequence):
        with app.state.repository.session_factory() as session:
            system = session.get(SystemStateRow, 1)
            assert system.event_session_id == "worker-session"
            assert system.current_sequence_number == sequence
            assert system.emergency_stop is True

    before_start = worker_update("under_review", 31)
    with TestClient(app) as client:
        assert client.get("/api/tasks/task-demo").json()["data"] == before_start
        assert_system(31)
        before_stop = worker_update("completed", 32)
    assert_system(32)
    with TestClient(create_app(database_url=url)) as restarted:
        assert restarted.get("/api/tasks/task-demo").json()["data"] == before_stop
        assert_system(32)
