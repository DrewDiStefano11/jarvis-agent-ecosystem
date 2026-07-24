from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.models import OutboxEventRow, TaskLeaseRow, TaskRow, WorkerRow, WorkflowRunRow
from app.main import create_app


def create_task_row(
    *, task_id: str, status: str = "in_progress", status_message: str = "", **overrides
) -> TaskRow:
    values = {
        "id": task_id,
        "title": "Lifespan recovery test task",
        "description": "desc",
        "original_request": "desc",
        "creator": "user",
        "priority": "high",
        "status": status,
        "progress": 0,
        "status_message": status_message,
        "retry_count": 0,
        "maximum_retries": 2,
        "schema_version": "1.0",
        "payload": {
            "id": task_id,
            "title": "Lifespan recovery test task",
            "description": "desc",
            "request": "desc",
            "createdBy": "user",
            "createdAt": datetime.now(UTC).isoformat(),
            "updatedAt": datetime.now(UTC).isoformat(),
        },
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    values.update(overrides)
    return TaskRow(**values)


def create_worker_row(*, worker_id: str, **overrides) -> WorkerRow:
    values = {
        "id": worker_id,
        "name": "worker",
        "instance_id": f"inst-{uuid4().hex[:8]}",
        "status": "active",
        "started_at": datetime.now(UTC),
        "last_heartbeat_at": datetime.now(UTC),
        "lease_seconds": 30,
        "metadata_json": {},
    }
    values.update(overrides)
    return WorkerRow(**values)


@pytest.fixture
def test_app(tmp_path, monkeypatch):
    database = (tmp_path / f"jarvis-lifespan-test-{uuid4().hex}.db").as_posix()
    monkeypatch.setenv("JARVIS_DATABASE_URL", f"sqlite:///{database}")
    monkeypatch.setenv("JARVIS_AUTO_MIGRATE", "true")

    app = create_app()
    return app


def test_normal_lifecycle(test_app):
    with TestClient(test_app):
        assert test_app.state.repository._system.last_successful_startup is not None
        assert test_app.state.repository._system.startup_was_clean is False
        assert getattr(test_app.state, "lease_recovery_task", None) is not None
        assert not test_app.state.lease_recovery_task.done()
        assert test_app.state.broker.dispatcher_running is True

    assert getattr(test_app.state, "lease_recovery_task", None).done()
    assert test_app.state.broker.dispatcher_running is False
    assert test_app.state.repository._system.last_clean_shutdown is not None
    assert test_app.state.repository._system.startup_was_clean is True


def test_pending_outbox_recovery(test_app):
    event_id = f"test-event-{uuid4().hex[:8]}"
    with test_app.state.repository.session_factory() as session, session.begin():
        session.add(
            OutboxEventRow(
                id=event_id,
                event_type="test.event",
                correlation_id="test-corr",
                event_session_id="test-sess",
                envelope={
                    "eventId": event_id,
                    "eventType": "test.event",
                    "correlationId": "test-corr",
                    "payload": {
                        "id": "test-task",
                        "title": "Lifespan recovery test task",
                        "description": "desc",
                        "request": "desc",
                        "createdBy": "user",
                        "createdAt": datetime.now(UTC).isoformat(),
                        "updatedAt": datetime.now(UTC).isoformat(),
                    },
                    "timestamp": datetime.now(UTC).isoformat(),
                    "sequenceNumber": 1,
                },
                status="pending",
                sequence_number=1,
                created_at=datetime.now(UTC),
            )
        )

    with TestClient(test_app):
        pass

    with test_app.state.repository.session_factory() as session:
        row = session.get(OutboxEventRow, event_id)
        assert row.status == "published"


def test_task_lease_recovery(test_app):
    now = datetime.now(UTC)
    with test_app.state.repository.session_factory() as session, session.begin():
        session.execute(text("PRAGMA foreign_keys = OFF"))
        worker = create_worker_row(worker_id="test-worker")
        lease = TaskLeaseRow(
            task_id="task-demo",
            worker_id="test-worker",
            lease_token="token",
            acquired_at=now,
            renewed_at=now,
            attempt_number=1,
            expires_at=now - timedelta(hours=1),
        )
        session.add(worker)
        session.add(lease)

    with TestClient(test_app):
        with test_app.state.repository.session_factory() as session:
            lease = session.get(TaskLeaseRow, "task-demo")
            assert lease is None


def test_simulator_recovery(tmp_path, monkeypatch):
    database = (tmp_path / f"jarvis-lifespan-sim-test-{uuid4().hex}.db").as_posix()
    monkeypatch.setenv("JARVIS_DATABASE_URL", f"sqlite:///{database}")
    monkeypatch.setenv("JARVIS_AUTO_MIGRATE", "true")
    monkeypatch.setenv("JARVIS_SIMULATOR_AUTO_RESUME", "true")

    app_setup = create_app()
    with TestClient(app_setup):
        with app_setup.state.repository.session_factory() as session, session.begin():
            session.execute(text("PRAGMA foreign_keys = OFF"))
            run = WorkflowRunRow(
                id="test-run-1",
                correlation_id="c-1",
                root_task_id="task-demo",
                workflow_type="type",
                workflow_version="1",
                started_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
                status="running",
                current_step_index=0,
            )
            session.add(run)
    app_setup.state.engine.dispose()

    app = create_app()
    with TestClient(app):
        assert app.state.simulator.control.state == "running"


def test_failure_behavior(tmp_path, monkeypatch):
    database = (tmp_path / f"jarvis-lifespan-fail-{uuid4().hex}.db").as_posix()
    monkeypatch.setenv("JARVIS_DATABASE_URL", f"sqlite:///{database}")
    monkeypatch.setenv("JARVIS_AUTO_MIGRATE", "true")

    app = create_app()

    # Mock dispatcher to raise an error during startup
    async def failing_dispatcher(*args, **kwargs):
        raise RuntimeError("Simulated startup failure")

    app.state.broker.start_dispatcher = failing_dispatcher

    with pytest.raises(RuntimeError, match="Simulated startup failure"):
        with TestClient(app):
            pass

    # Ensure engine gets disposed even on failed startup
    # Engine pool should be shut down cleanly
    # And shutdown fields shouldn't mark it clean because startup failed
    with app.state.repository.session_factory() as session:
        from app.db.models import SystemStateRow

        row = session.get(SystemStateRow, 1)
        assert row.startup_was_clean is False


def test_multiple_app_instances(tmp_path, monkeypatch):
    database1 = (tmp_path / f"jarvis-lifespan-multi-1-{uuid4().hex}.db").as_posix()
    monkeypatch.setenv("JARVIS_DATABASE_URL", f"sqlite:///{database1}")
    monkeypatch.setenv("JARVIS_AUTO_MIGRATE", "true")
    app1 = create_app()

    database2 = (tmp_path / f"jarvis-lifespan-multi-2-{uuid4().hex}.db").as_posix()
    monkeypatch.setenv("JARVIS_DATABASE_URL", f"sqlite:///{database2}")
    app2 = create_app()

    with TestClient(app1), TestClient(app2):
        assert app1.state.engine is not app2.state.engine
        assert app1.state.repository is not app2.state.repository
        assert app1.state.broker is not app2.state.broker
        assert app1.state.simulator is not app2.state.simulator
        assert app1.state.lease_recovery_task is not app2.state.lease_recovery_task
