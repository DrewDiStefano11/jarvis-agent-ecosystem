from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.db.models import AuditEventRow, OutboxEventRow, TaskRow
from app.main import create_app
from app.services.unit_of_work import UnitOfWork


def database_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def test_blank_database_migrates_to_head(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "migration.db"
    monkeypatch.setenv("JARVIS_DATABASE_URL", database_url(path))
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    command.upgrade(config, "head")
    engine = create_engine(database_url(path))
    tables = set(inspect(engine).get_table_names())
    assert {
        "agents",
        "approvals",
        "audit_events",
        "idempotency_records",
        "outbox_events",
        "system_state",
        "tasks",
        "workflow_checkpoints",
        "workflow_runs",
    } <= tables
    with engine.connect() as connection:
        assert connection.scalar(text("select version_num from alembic_version")) == "20260720_01"
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1


def test_state_and_idempotency_survive_application_recreation(tmp_path: Path) -> None:
    url = database_url(tmp_path / "restart.db")
    headers = {"Idempotency-Key": "create-durable-task"}
    body = {"title": "Durable task", "description": "Survive a backend restart", "priority": "high"}
    with TestClient(create_app(delay_ms=1, database_url=url)) as first:
        created = first.post("/api/tasks", json=body, headers=headers)
        assert created.status_code == 201
        task_id = created.json()["data"]["id"]
        first.post("/api/approvals/approval-pending/approve", json={"decisionNote": "Durable"})
        first.post("/api/notifications/notification-1/read")
        first.post("/api/system/emergency-stop")
        audit_count = len(first.get("/api/audit-events").json()["data"])
        session_id = first.get("/api/system/status").json()["data"]["eventSessionId"]

    with TestClient(create_app(delay_ms=1, database_url=url)) as second:
        assert second.get(f"/api/tasks/{task_id}").status_code == 200
        replay = second.post("/api/tasks", json=body, headers=headers)
        assert replay.json()["data"]["id"] == task_id
        assert second.get("/api/approvals/approval-pending").json()["data"]["status"] == "approved"
        notifications = second.get("/api/notifications").json()["data"]
        assert (
            next(item for item in notifications if item["id"] == "notification-1")["isRead"] is True
        )
        status = second.get("/api/system/status").json()["data"]
        assert status["emergencyStop"] is True
        assert status["eventSessionId"] == session_id
        assert len(second.get("/api/audit-events").json()["data"]) >= audit_count


def test_idempotency_conflict_is_structured(tmp_path: Path) -> None:
    url = database_url(tmp_path / "idempotency.db")
    headers = {"Idempotency-Key": "same-key"}
    with TestClient(create_app(delay_ms=1, database_url=url)) as api:
        assert (
            api.post(
                "/api/tasks",
                json={"title": "First task", "description": "First request"},
                headers=headers,
            ).status_code
            == 201
        )
        conflict = api.post(
            "/api/tasks",
            json={"title": "Other task", "description": "Different request"},
            headers=headers,
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"


def test_concurrent_duplicate_submission_creates_one_task(tmp_path: Path) -> None:
    url = database_url(tmp_path / "concurrent.db")
    body = {"title": "One durable task", "description": "Submitted concurrently"}
    headers = {"Idempotency-Key": "concurrent-task-key"}
    first_app = create_app(delay_ms=1, database_url=url)
    second_app = create_app(delay_ms=1, database_url=url)
    third_app = create_app(delay_ms=1, database_url=url)
    entered_publish = threading.Event()
    release_publish = threading.Event()
    original_publish = first_app.state.broker._publish

    async def delayed_publish(event) -> None:
        entered_publish.set()
        released = await asyncio.to_thread(release_publish.wait, 5)
        assert released
        await original_publish(event)

    first_app.state.broker._publish = delayed_publish
    with (
        TestClient(first_app) as first,
        TestClient(second_app) as second,
        TestClient(third_app) as third,
        ThreadPoolExecutor(max_workers=1) as pool,
    ):
        first_response = pool.submit(first.post, "/api/tasks", json=body, headers=headers)
        assert entered_publish.wait(5)
        second_response = second.post("/api/tasks", json=body, headers=headers)
        third_response = third.post("/api/tasks", json=body, headers=headers)
        assert second_response.status_code == 409
        assert second_response.json()["error"]["code"] == "IDEMPOTENCY_REQUEST_IN_PROGRESS"
        assert third_response.status_code == 409
        assert third_response.json()["error"]["code"] == "IDEMPOTENCY_REQUEST_IN_PROGRESS"
        release_publish.set()
        created = first_response.result(timeout=5)
        assert created.status_code == 201
    with TestClient(create_app(delay_ms=1, database_url=url)) as verifier:
        matches = [
            item
            for item in verifier.get("/api/tasks").json()["data"]
            if item["title"] == body["title"]
        ]
        assert len(matches) == 1
        replay = verifier.post("/api/tasks", json=body, headers=headers)
        assert replay.status_code == 201
        assert replay.json()["data"]["id"] == matches[0]["id"]


def test_paused_workflow_remains_resumable_after_application_recreation(tmp_path: Path) -> None:
    url = database_url(tmp_path / "paused-recovery.db")
    with TestClient(create_app(delay_ms=20, database_url=url)) as first:
        first.post("/api/simulator/start")
        asyncio.run(asyncio.sleep(0.07))
        paused = first.post("/api/simulator/pause")
        assert paused.status_code == 200
        paused_step = paused.json()["data"]["currentStep"]
        paused_status = first.get("/api/system/status").json()["data"]
        run_id = paused_status["activeWorkflowRunId"]
        checkpoint_id = paused_status["lastCheckpointId"]

    with TestClient(create_app(delay_ms=1, database_url=url)) as second:
        restored = second.get("/api/system/status").json()["data"]
        assert restored["simulator"]["state"] == "paused"
        assert restored["simulator"]["currentStep"] == paused_step
        assert restored["activeWorkflowRunId"] == run_id
        assert restored["lastCheckpointId"] == checkpoint_id
        assert second.post("/api/simulator/resume").status_code == 200
        for _ in range(150):
            status = second.get("/api/system/status").json()["data"]
            if status["simulator"]["state"] == "completed":
                break
            asyncio.run(asyncio.sleep(0.005))
        assert status["simulator"]["state"] == "completed"
        audit = second.get("/api/audit-events").json()["data"]
        step_numbers = [item["payload"].get("step") for item in audit if "step" in item["payload"]]
        assert len(step_numbers) == len(set(step_numbers))


def test_rapid_reset_and_restart_create_distinct_workflow_run_ids(tmp_path: Path) -> None:
    url = database_url(tmp_path / "run-ids.db")
    app = create_app(delay_ms=100, database_url=url)
    with TestClient(app) as api:
        assert api.post("/api/simulator/start").status_code == 200
        first_run_id = api.get("/api/system/status").json()["data"]["activeWorkflowRunId"]
        assert api.post("/api/simulator/reset").status_code == 200
        assert api.post("/api/simulator/start").status_code == 200
        second_run_id = api.get("/api/system/status").json()["data"]["activeWorkflowRunId"]
        assert first_run_id != second_run_id
        assert api.post("/api/simulator/reset").status_code == 200


def test_reset_idempotency_replays_after_lost_response(tmp_path: Path) -> None:
    url = database_url(tmp_path / "reset-idempotency.db")
    headers = {"Idempotency-Key": "lost-reset-response"}
    with TestClient(create_app(delay_ms=1, database_url=url)) as api:
        before_session = api.get("/api/system/status").json()["data"]["eventSessionId"]
        first = api.post("/api/simulator/reset", headers=headers)
        after_first_session = api.get("/api/system/status").json()["data"]["eventSessionId"]
        retry = api.post("/api/simulator/reset", headers=headers)
        after_retry_session = api.get("/api/system/status").json()["data"]["eventSessionId"]
        assert first.status_code == 200
        assert retry.status_code == 200
        assert retry.json() == first.json()
        assert before_session != after_first_session
        assert after_retry_session == after_first_session
        reset_audits = [
            item
            for item in api.get("/api/audit-events").json()["data"]
            if item["eventType"] == "system.simulator.reset"
        ]
        assert len(reset_audits) == 1


def test_interrupted_workflow_has_checkpoint_and_resumes(tmp_path: Path) -> None:
    url = database_url(tmp_path / "recovery.db")
    first_app = create_app(delay_ms=20, database_url=url)
    with TestClient(first_app) as first:
        assert first.post("/api/simulator/start").status_code == 200
        asyncio.run(asyncio.sleep(0.09))
        status = first.get("/api/system/status").json()["data"]
        assert status["lastCheckpointId"] is not None
        completed_steps = status["simulator"]["currentStep"]
        assert completed_steps > 0

    with TestClient(create_app(delay_ms=1, database_url=url)) as second:
        interrupted = second.get("/api/system/status").json()["data"]
        assert interrupted["recoveryRequired"] is True
        assert interrupted["simulator"]["state"] == "recovery_required"
        assert second.post("/api/simulator/resume").status_code == 200
        for _ in range(150):
            status = second.get("/api/system/status").json()["data"]
            if status["simulator"]["state"] == "completed":
                break
            asyncio.run(asyncio.sleep(0.005))
        assert status["simulator"]["state"] == "completed"
        assert second.get("/api/tasks/task-demo").json()["data"]["status"] == "completed"
        audit = second.get("/api/audit-events").json()["data"]
        step_numbers = [item["payload"].get("step") for item in audit if "step" in item["payload"]]
        assert len(step_numbers) == len(set(step_numbers))


def test_outbox_events_are_committed_and_published(tmp_path: Path) -> None:
    url = database_url(tmp_path / "outbox.db")
    app = create_app(delay_ms=1, database_url=url)
    with TestClient(app) as api:
        api.post("/api/tasks", json={"title": "Outbox task", "description": "Verify delivery"})
        assert api.get("/api/system/status").json()["data"]["outboxPendingCount"] == 0
    engine = create_engine(url)
    with engine.connect() as connection:
        rows = connection.execute(
            text("select event_type, status, publish_attempt_count from outbox_events")
        ).all()
    assert rows
    assert all(status == "published" and attempts == 1 for _, status, attempts in rows)


def test_reset_remains_clean_after_restart_and_preserves_user_task(tmp_path: Path) -> None:
    url = database_url(tmp_path / "reset.db")
    with TestClient(create_app(delay_ms=1, database_url=url)) as first:
        user_task = first.post(
            "/api/tasks", json={"title": "Keep this task", "description": "Unrelated durable work"}
        ).json()["data"]["id"]
        first.post("/api/simulator/start")
        asyncio.run(asyncio.sleep(0.04))
        first.post("/api/simulator/reset")
    with TestClient(create_app(delay_ms=1, database_url=url)) as second:
        tasks = second.get("/api/tasks").json()["data"]
        assert len(tasks) == 5
        assert any(item["id"] == user_task for item in tasks)
        assert not any(item["id"].startswith("task-demo-") for item in tasks)


def test_unit_of_work_rolls_back_domain_audit_and_outbox_together(tmp_path: Path) -> None:
    url = database_url(tmp_path / "rollback.db")
    app = create_app(delay_ms=1, database_url=url)
    repository = app.state.repository
    now = datetime.now(UTC)
    with TestClient(app) as api:
        api.post("/api/tasks", json={"title": "Existing event", "description": "Create outbox row"})
    with repository.session_factory() as session:
        existing_event_id = session.scalar(select(OutboxEventRow.id))
    assert existing_event_id
    with pytest.raises(IntegrityError):
        with UnitOfWork(repository.session_factory) as uow:
            assert uow.session is not None
            task = TaskRow(
                id="transaction-task",
                title="Rolled back",
                description="Rollback fixture",
                original_request="Rollback fixture",
                parent_task_id=None,
                project_id=None,
                creator="test",
                assigned_manager_id="jarvis",
                priority="medium",
                status="queued",
                progress=0,
                status_message="Queued",
                result=None,
                error=None,
                retry_count=0,
                maximum_retries=2,
                schema_version="1.0",
                payload={"id": "transaction-task"},
                created_at=now,
                started_at=None,
                updated_at=now,
                completed_at=None,
            )
            uow.session.add(task)
            uow.session.flush()
            uow.session.add(
                AuditEventRow(
                    id="transaction-audit",
                    event_type="task.created",
                    actor="test",
                    agent_id=None,
                    task_id=task.id,
                    approval_id=None,
                    previous_state=None,
                    new_state="queued",
                    correlation_id="rollback",
                    sequence_number=999,
                    event_session_id="rollback",
                    timestamp=now,
                    payload={},
                    schema_version="1.0",
                )
            )
            uow.session.add(
                OutboxEventRow(
                    id=existing_event_id,
                    event_type="task.created",
                    envelope={},
                    correlation_id="rollback",
                    event_session_id="rollback",
                    sequence_number=999,
                    status="pending",
                    created_at=now,
                    published_at=None,
                    publish_attempt_count=0,
                    last_publish_error=None,
                )
            )
    with repository.session_factory() as session:
        assert session.get(TaskRow, "transaction-task") is None
        assert session.get(AuditEventRow, "transaction-audit") is None
