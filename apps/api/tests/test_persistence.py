from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, func, inspect, select, text, update
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings
from app.db.models import (
    ApprovalRow,
    AuditEventRow,
    IdempotencyRecordRow,
    OutboxEventRow,
    SystemStateRow,
    TaskRow,
    WorkflowCheckpointRow,
    WorkflowRunRow,
)
from app.db.session import create_database_engine
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
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    expected_tables = {
        "agents",
        "approvals",
        "artifacts",
        "audit_events",
        "departments",
        "idempotency_records",
        "notifications",
        "outbox_events",
        "system_state",
        "task_agents",
        "task_blockers",
        "task_dependencies",
        "tasks",
        "workflow_checkpoints",
        "workflow_runs",
    }
    assert expected_tables <= tables
    assert {item["name"] for item in inspector.get_indexes("audit_events")} == {
        "ix_audit_events_correlation_id",
        "ix_audit_events_event_session_id",
        "ix_audit_events_event_type",
        "ix_audit_events_task_id",
    }
    assert {
        tuple(item["column_names"])
        for item in inspector.get_unique_constraints("idempotency_records")
    } == {("idempotency_key", "command_type")}
    assert {
        tuple(item["constrained_columns"])
        for item in inspector.get_foreign_keys("workflow_checkpoints")
    } == {("root_task_id",), ("workflow_run_id",)}
    with engine.connect() as connection:
        assert connection.scalar(text("select version_num from alembic_version")) == "20260720_01"
    engine.dispose()
    command.downgrade(config, "base")
    downgraded_tables = set(inspect(create_engine(database_url(path))).get_table_names())
    assert not expected_tables & downgraded_tables
    command.upgrade(config, "head")
    command.current(config)
    with create_database_engine(database_url(path)).connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert connection.scalar(text("select version_num from alembic_version")) == "20260720_01"
    revision = root / "migrations" / "versions" / "20260720_01_durable_control_plane.py"
    source = revision.read_text(encoding="utf-8")
    assert "Base.metadata" not in source
    assert "create_all" not in source
    assert "drop_all" not in source


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
    with create_engine(url).connect() as connection:
        assert (
            connection.scalar(
                text(
                    "select response_status from idempotency_records "
                    "where idempotency_key = 'create-durable-task'"
                )
            )
            == 201
        )


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
    original_enqueue = first_app.state.repository.enqueue_event

    def delayed_enqueue(envelope, idempotency=None) -> None:
        entered_publish.set()
        released = release_publish.wait(5)
        assert released
        original_enqueue(envelope, idempotency)

    first_app.state.repository.enqueue_event = delayed_enqueue
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


def test_orphaned_idempotency_claim_lease_is_reclaimed_once_after_restart(
    tmp_path: Path,
) -> None:
    url = database_url(tmp_path / "orphaned-claim.db")
    body = {
        "title": "Reclaimed exactly once",
        "description": "Crash before command execution",
        "priority": "medium",
    }
    headers = {"Idempotency-Key": "orphaned-command"}
    command = "task.create"
    first_app = create_app(delay_ms=1, database_url=url)
    orphan = first_app.state.repository.idempotency_claim(headers["Idempotency-Key"], command, body)
    assert orphan.owned is True
    assert orphan.lease_expires_at is not None
    first_app.state.engine.dispose()

    with TestClient(create_app(delay_ms=1, database_url=url)) as unexpired:
        blocked = unexpired.post("/api/tasks", json=body, headers=headers)
        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "IDEMPOTENCY_REQUEST_IN_PROGRESS"

    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            update(IdempotencyRecordRow)
            .where(IdempotencyRecordRow.idempotency_key == headers["Idempotency-Key"])
            .values(expiration_at=datetime.now(UTC) - timedelta(seconds=1))
        )

    reclaiming_app = create_app(delay_ms=1, database_url=url)
    duplicate_app = create_app(delay_ms=1, database_url=url)
    third_app = create_app(delay_ms=1, database_url=url)
    entered_command = threading.Event()
    release_command = threading.Event()
    original_enqueue = reclaiming_app.state.repository.enqueue_event

    def delayed_enqueue(envelope, idempotency=None) -> None:
        entered_command.set()
        assert release_command.wait(5)
        original_enqueue(envelope, idempotency)

    reclaiming_app.state.repository.enqueue_event = delayed_enqueue
    with (
        TestClient(reclaiming_app) as reclaiming,
        TestClient(duplicate_app) as duplicate,
        TestClient(third_app) as third,
        ThreadPoolExecutor(max_workers=1) as pool,
    ):
        winner = pool.submit(reclaiming.post, "/api/tasks", json=body, headers=headers)
        assert entered_command.wait(5)
        second = duplicate.post("/api/tasks", json=body, headers=headers)
        third_response = third.post("/api/tasks", json=body, headers=headers)
        assert second.status_code == 409
        assert third_response.status_code == 409
        assert second.json()["error"]["code"] == "IDEMPOTENCY_REQUEST_IN_PROGRESS"
        assert third_response.json()["error"]["code"] == "IDEMPOTENCY_REQUEST_IN_PROGRESS"
        release_command.set()
        completed = winner.result(timeout=5)
        assert completed.status_code == 201
        task_id = completed.json()["data"]["id"]

    with engine.connect() as connection:
        assert (
            connection.scalar(
                select(func.count()).select_from(TaskRow).where(TaskRow.title == body["title"])
            )
            == 1
        )
        assert (
            connection.scalar(
                select(func.count())
                .select_from(AuditEventRow)
                .where(AuditEventRow.task_id == task_id)
            )
            == 1
        )
        matching_outbox = [
            envelope
            for envelope in connection.scalars(
                select(OutboxEventRow.envelope).where(OutboxEventRow.event_type == "task.created")
            )
            if envelope["payload"]["task"]["id"] == task_id
        ]
        assert len(matching_outbox) == 1
        record = connection.execute(
            select(
                IdempotencyRecordRow.response_status,
                IdempotencyRecordRow.created_resource_id,
                IdempotencyRecordRow.expiration_at,
            ).where(IdempotencyRecordRow.idempotency_key == headers["Idempotency-Key"])
        ).one()
        assert record == (201, task_id, None)


def _exercise_checkpoint_publication_race(tmp_path: Path, operation: str) -> None:
    url = database_url(tmp_path / f"{operation}-checkpoint-race.db")
    app = create_app(delay_ms=20, database_url=url)
    step_committed = threading.Event()
    release_publication = threading.Event()
    original_publish = app.state.broker._publish
    delayed_once = False

    async def delayed_step_publish(event) -> None:
        nonlocal delayed_once
        if event.eventType == "agent.status.changed" and not delayed_once:
            delayed_once = True
            step_committed.set()
            assert await asyncio.to_thread(release_publication.wait, 5)
        await original_publish(event)

    app.state.broker._publish = delayed_step_publish
    route = "/api/simulator/pause" if operation == "pause" else "/api/system/emergency-stop"
    with TestClient(app) as first, ThreadPoolExecutor(max_workers=1) as pool:
        assert first.post("/api/simulator/start").status_code == 200
        assert step_committed.wait(5)
        operation_response = pool.submit(first.post, route)
        asyncio.run(asyncio.sleep(0.05))
        assert not operation_response.done()
        release_publication.set()
        assert operation_response.result(timeout=5).status_code == 200
        paused = first.get("/api/system/status").json()["data"]
        assert paused["simulator"]["state"] == "paused"
        paused_step = paused["simulator"]["currentStep"]
        run_id = paused["activeWorkflowRunId"]
        checkpoint_id = paused["lastCheckpointId"]
        assert paused_step >= 1

    with create_engine(url).connect() as connection:
        run = connection.execute(
            select(
                WorkflowRunRow.current_step_index,
                WorkflowRunRow.checkpoint_id,
                WorkflowRunRow.status,
            ).where(WorkflowRunRow.id == run_id)
        ).one()
        checkpoint_step = connection.scalar(
            select(WorkflowCheckpointRow.step_index).where(
                WorkflowCheckpointRow.id == checkpoint_id
            )
        )
        assert run == (paused_step, checkpoint_id, "paused")
        assert checkpoint_step == paused_step

    with TestClient(create_app(delay_ms=1, database_url=url)) as resumed:
        restored = resumed.get("/api/system/status").json()["data"]
        assert restored["simulator"]["currentStep"] == paused_step
        if operation == "emergency-stop":
            assert resumed.post("/api/system/resume").status_code == 200
        assert resumed.post("/api/simulator/resume").status_code == 200
        for _ in range(200):
            final = resumed.get("/api/system/status").json()["data"]
            if final["simulator"]["state"] == "completed":
                break
            asyncio.run(asyncio.sleep(0.005))
        assert final["simulator"]["state"] == "completed"
        assert resumed.get("/api/tasks/task-demo").json()["data"]["status"] == "completed"
        audits = resumed.get("/api/audit-events").json()["data"]
        step_numbers = [item["payload"]["step"] for item in audits if "step" in item["payload"]]
        assert len(step_numbers) == len(set(step_numbers)) == 25

    with create_engine(url).connect() as connection:
        final_run = connection.execute(
            select(WorkflowRunRow.current_step_index, WorkflowRunRow.checkpoint_id).where(
                WorkflowRunRow.id == run_id
            )
        ).one()
        final_checkpoint_step = connection.scalar(
            select(WorkflowCheckpointRow.step_index).where(
                WorkflowCheckpointRow.id == final_run.checkpoint_id
            )
        )
        assert final_run.current_step_index == final_checkpoint_step == 25


def test_pause_waits_for_in_flight_step_checkpoint_before_persisting(tmp_path: Path) -> None:
    _exercise_checkpoint_publication_race(tmp_path, "pause")


def test_emergency_stop_waits_for_in_flight_step_checkpoint_before_persisting(
    tmp_path: Path,
) -> None:
    _exercise_checkpoint_publication_race(tmp_path, "emergency-stop")


def test_reset_audit_and_next_event_use_monotonic_session_sequences(tmp_path: Path) -> None:
    url = database_url(tmp_path / "reset-sequences.db")
    before_body = {"title": "Before reset", "description": "Old event session"}
    after_body = {"title": "After reset", "description": "New event session"}
    with TestClient(create_app(delay_ms=1, database_url=url)) as api:
        old_session = api.get("/api/system/status").json()["data"]["eventSessionId"]
        before = api.post("/api/tasks", json=before_body)
        assert before.status_code == 201
        before_id = before.json()["data"]["id"]
        assert api.post("/api/simulator/reset").status_code == 200
        reset_status = api.get("/api/system/status").json()["data"]
        new_session = reset_status["eventSessionId"]
        assert new_session != old_session
        after = api.post("/api/tasks", json=after_body)
        assert after.status_code == 201
        after_id = after.json()["data"]["id"]
        final_status = api.get("/api/system/status").json()["data"]
        assert final_status["eventSessionId"] == new_session

    engine = create_engine(url)
    with engine.connect() as connection:
        before_audit = connection.execute(
            select(AuditEventRow.event_session_id, AuditEventRow.sequence_number).where(
                AuditEventRow.task_id == before_id
            )
        ).one()
        reset_audit = connection.execute(
            select(AuditEventRow.event_session_id, AuditEventRow.sequence_number).where(
                AuditEventRow.event_type == "system.simulator.reset"
            )
        ).one()
        after_audit = connection.execute(
            select(AuditEventRow.event_session_id, AuditEventRow.sequence_number).where(
                AuditEventRow.task_id == after_id
            )
        ).one()
        after_outbox = connection.execute(
            select(OutboxEventRow.event_session_id, OutboxEventRow.sequence_number).where(
                OutboxEventRow.envelope["taskId"].as_string() == after_id
            )
        ).one()
        assert before_audit.event_session_id == reset_audit.event_session_id == old_session
        assert reset_audit.sequence_number == before_audit.sequence_number + 1
        assert after_audit == (new_session, 1)
        assert after_outbox == (new_session, 1)
        assert (
            connection.scalar(
                select(func.count())
                .select_from(OutboxEventRow)
                .where(OutboxEventRow.event_type == "system.simulator.reset")
            )
            == 0
        )

        audit_pairs = connection.execute(
            select(
                AuditEventRow.event_session_id,
                AuditEventRow.sequence_number,
                AuditEventRow.timestamp,
            ).order_by(AuditEventRow.timestamp)
        ).all()
        audit_by_session: dict[str, list[int]] = {}
        for session_id, sequence, _ in audit_pairs:
            audit_by_session.setdefault(session_id, []).append(sequence)
        for sequences in audit_by_session.values():
            assert sequences == sorted(sequences)
            assert len(sequences) == len(set(sequences))

        outbox_pairs = connection.execute(
            select(
                OutboxEventRow.event_session_id,
                OutboxEventRow.sequence_number,
                OutboxEventRow.created_at,
            ).order_by(OutboxEventRow.created_at)
        ).all()
        outbox_by_session: dict[str, list[int]] = {}
        for session_id, sequence, _ in outbox_pairs:
            outbox_by_session.setdefault(session_id, []).append(sequence)
        for sequences in outbox_by_session.values():
            assert sequences == sorted(sequences)
            assert len(sequences) == len(set(sequences))

        state = connection.get_isolation_level()
        assert state
        durable = connection.execute(
            select(SystemStateRow.event_session_id, SystemStateRow.current_sequence_number)
        ).one()
        assert durable == (new_session, 1)

    with TestClient(create_app(delay_ms=1, database_url=url)) as recreated:
        restored = recreated.get("/api/system/status").json()["data"]
        assert restored["eventSessionId"] == new_session
        assert recreated.app.state.repository.sequence == 1


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
        assert restored["status"] == "healthy"
        assert restored["recoveryRequired"] is False
        health = second.get("/api/health").json()["data"]
        assert health["status"] == "healthy"
        assert health["recoveryRequired"] is False
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


def test_recovery_required_workflow_survives_repeated_application_recreation(
    tmp_path: Path,
) -> None:
    url = database_url(tmp_path / "repeated-recovery.db")
    with TestClient(create_app(delay_ms=20, database_url=url)) as first:
        assert first.post("/api/simulator/start").status_code == 200
        asyncio.run(asyncio.sleep(0.07))
        original = first.get("/api/system/status").json()["data"]
        assert original["simulator"]["currentStep"] > 0

    with TestClient(create_app(delay_ms=1, database_url=url)) as second:
        first_recovery = second.get("/api/system/status").json()["data"]
        assert first_recovery["simulator"]["state"] == "recovery_required"
        assert first_recovery["recoveryRequired"] is True

    with TestClient(create_app(delay_ms=1, database_url=url)) as third:
        repeated_recovery = third.get("/api/system/status").json()["data"]
        assert repeated_recovery["simulator"]["state"] == "recovery_required"
        assert (
            repeated_recovery["simulator"]["currentStep"]
            == first_recovery["simulator"]["currentStep"]
        )
        assert repeated_recovery["activeWorkflowRunId"] == first_recovery["activeWorkflowRunId"]
        assert repeated_recovery["lastCheckpointId"] == first_recovery["lastCheckpointId"]
        assert repeated_recovery["recoveryRequired"] is True
        assert third.post("/api/simulator/resume").status_code == 200
        for _ in range(150):
            status = third.get("/api/system/status").json()["data"]
            if status["simulator"]["state"] == "completed":
                break
            asyncio.run(asyncio.sleep(0.005))
        assert status["simulator"]["state"] == "completed"
        audit = third.get("/api/audit-events").json()["data"]
        step_numbers = [item["payload"].get("step") for item in audit if "step" in item["payload"]]
        assert len(step_numbers) == len(set(step_numbers))


def test_completed_workflow_requires_reset_after_clean_application_recreation(
    tmp_path: Path,
) -> None:
    url = database_url(tmp_path / "completed-recovery.db")
    with TestClient(create_app(delay_ms=1, database_url=url)) as first:
        assert first.post("/api/simulator/start").status_code == 200
        for _ in range(150):
            completed = first.get("/api/system/status").json()["data"]
            if completed["simulator"]["state"] == "completed":
                break
            asyncio.run(asyncio.sleep(0.005))
        assert completed["simulator"]["state"] == "completed"
        run_id = completed["activeWorkflowRunId"]
        checkpoint_id = completed["lastCheckpointId"]
        artifacts = first.get("/api/artifacts").json()["data"]

    with TestClient(create_app(delay_ms=1, database_url=url)) as second:
        restored = second.get("/api/system/status").json()["data"]
        assert restored["simulator"]["state"] == "completed"
        assert restored["simulator"]["currentStep"] == 25
        assert restored["activeWorkflowRunId"] == run_id
        assert restored["lastCheckpointId"] == checkpoint_id
        rejected = second.post("/api/simulator/start")
        assert rejected.status_code == 409
        assert rejected.json()["error"]["code"] == "SIMULATOR_RESET_REQUIRED"
        assert second.get("/api/artifacts").json()["data"] == artifacts
        assert second.post("/api/simulator/reset").status_code == 200
        assert second.post("/api/simulator/start").status_code == 200
        assert second.post("/api/simulator/reset").status_code == 200


def test_health_degrades_for_unreachable_database_or_stale_schema(tmp_path: Path) -> None:
    url = database_url(tmp_path / "health-probe.db")
    app = create_app(delay_ms=1, database_url=url)
    with TestClient(app) as api:
        repository = app.state.repository
        original_probe = repository.health_probe
        repository.health_probe = lambda _revision: (False, False)
        unavailable = api.get("/api/health").json()["data"]
        assert unavailable["status"] == "degraded"
        assert unavailable["databaseReachable"] is False
        assert unavailable["schemaCurrent"] is False
        unavailable_system = api.get("/api/system/status").json()["data"]
        assert unavailable_system["status"] == "degraded"
        assert unavailable_system["databaseHealthy"] is False
        assert unavailable_system["schemaCurrent"] is False
        repository.health_probe = original_probe

        with app.state.engine.begin() as connection:
            connection.execute(text("DROP TABLE alembic_version"))
        stale = api.get("/api/health").json()["data"]
        assert stale["status"] == "degraded"
        assert stale["databaseReachable"] is True
        assert stale["schemaCurrent"] is False
        stale_system = api.get("/api/system/status").json()["data"]
        assert stale_system["status"] == "degraded"
        assert stale_system["schemaCurrent"] is False


def test_active_failure_is_atomic_terminal_and_survives_repeated_recreation(
    tmp_path: Path,
) -> None:
    url = database_url(tmp_path / "active-failure.db")
    app = create_app(delay_ms=20, database_url=url)
    with TestClient(app) as api:
        assert api.post("/api/simulator/start").status_code == 200
        for _ in range(100):
            before = api.get("/api/system/status").json()["data"]
            if before["simulator"]["currentStep"] >= 1:
                break
            asyncio.run(asyncio.sleep(0.005))
        runner = app.state.simulator._runner
        failed = api.post("/api/simulator/failure", json={"scenario": "scout_research_failure"})
        assert failed.status_code == 200
        assert failed.json()["data"]["status"] == "failed"
        assert runner is not None and runner.done()
        assert app.state.simulator._runner is None
        terminal = api.get("/api/system/status").json()["data"]
        failed_step = terminal["simulator"]["currentStep"]
        assert terminal["simulator"]["state"] == "failed"
        assert api.get("/api/health").status_code == 200
        assert (
            api.get("/api/system/status").json()["data"]["simulator"]["currentStep"] == failed_step
        )
        run_id = terminal["activeWorkflowRunId"]
        checkpoint_id = terminal["lastCheckpointId"]

    engine = create_engine(url)
    with engine.connect() as connection:
        task = connection.execute(
            select(TaskRow.status, TaskRow.payload).where(TaskRow.id == "task-demo")
        ).one()
        run = connection.execute(
            select(
                WorkflowRunRow.status,
                WorkflowRunRow.checkpoint_id,
                WorkflowRunRow.current_step_index,
                WorkflowRunRow.current_step_identifier,
                WorkflowRunRow.completed_at,
                WorkflowRunRow.resume_eligibility,
                WorkflowRunRow.failure_reason,
            ).where(WorkflowRunRow.id == run_id)
        ).one()
        checkpoint = connection.execute(
            select(WorkflowCheckpointRow.step_index, WorkflowCheckpointRow.payload).where(
                WorkflowCheckpointRow.id == checkpoint_id
            )
        ).one()
        system = connection.execute(
            select(
                SystemStateRow.simulator_status,
                SystemStateRow.last_checkpoint_id,
                SystemStateRow.recovery_status,
            )
        ).one()
        assert task.status == task.payload["status"] == "failed"
        assert run.status == "failed"
        assert run.checkpoint_id == checkpoint_id
        assert run.current_step_index == checkpoint.step_index == failed_step
        assert run.current_step_identifier == "failure.scout_research_failure"
        assert run.completed_at is not None
        assert run.resume_eligibility is False
        assert run.failure_reason == "scout_research_failure"
        assert checkpoint.payload["stepIndex"] == failed_step
        assert checkpoint.payload["taskStatuses"]["task-demo"] == "failed"
        assert system == ("failed", checkpoint_id, "none")
        assert (
            connection.scalar(
                select(func.count())
                .select_from(AuditEventRow)
                .where(AuditEventRow.event_type == "error.simulated")
            )
            == 1
        )
        assert (
            connection.scalar(
                select(func.count())
                .select_from(OutboxEventRow)
                .where(OutboxEventRow.event_type == "error.simulated")
            )
            == 1
        )
        baseline_counts = (
            connection.scalar(select(func.count()).select_from(AuditEventRow)),
            connection.scalar(select(func.count()).select_from(OutboxEventRow)),
        )

    for _ in range(2):
        with TestClient(create_app(delay_ms=1, database_url=url)) as recreated:
            restored = recreated.get("/api/system/status").json()["data"]
            assert restored["simulator"] == {
                "state": "failed",
                "currentStep": failed_step,
                "totalSteps": 25,
                "accelerated": True,
            }
            assert restored["recoveryRequired"] is False
            assert recreated.post("/api/simulator/resume").status_code == 409
            assert (
                recreated.post("/api/simulator/start").json()["error"]["code"]
                == "SIMULATOR_RESET_REQUIRED"
            )
            assert (
                recreated.get("/api/tasks/task-demo").json()["data"]["progress"]
                == task.payload["progress"]
            )
    with engine.connect() as connection:
        assert (
            connection.scalar(select(func.count()).select_from(AuditEventRow)),
            connection.scalar(select(func.count()).select_from(OutboxEventRow)),
        ) == baseline_counts


def test_failure_waits_for_published_step_and_persists_non_regressing_checkpoint(
    tmp_path: Path,
) -> None:
    url = database_url(tmp_path / "failure-publication-race.db")
    app = create_app(delay_ms=20, database_url=url)
    step_committed = threading.Event()
    release_publication = threading.Event()
    original_publish = app.state.broker._publish
    delayed_once = False

    async def delayed_publish(event) -> None:
        nonlocal delayed_once
        if event.eventType == "agent.status.changed" and not delayed_once:
            delayed_once = True
            step_committed.set()
            assert await asyncio.to_thread(release_publication.wait, 5)
        await original_publish(event)

    app.state.broker._publish = delayed_publish
    with TestClient(app) as api, ThreadPoolExecutor(max_workers=1) as pool:
        assert api.post("/api/simulator/start").status_code == 200
        assert step_committed.wait(5)
        failure = pool.submit(
            api.post,
            "/api/simulator/failure",
            json={"scenario": "archive_unavailable"},
        )
        asyncio.run(asyncio.sleep(0.05))
        assert not failure.done()
        release_publication.set()
        assert failure.result(timeout=5).status_code == 200
        status = api.get("/api/system/status").json()["data"]
        assert status["simulator"]["state"] == "failed"
        assert status["simulator"]["currentStep"] == 1
        assert app.state.simulator._runner is None
        audits = api.get("/api/audit-events").json()["data"]
        assert [
            item["payload"]["step"]
            for item in audits
            if item["eventType"] == "agent.status.changed"
        ] == [1]
        asyncio.run(asyncio.sleep(0.05))
        assert api.get("/api/system/status").json()["data"]["simulator"]["currentStep"] == 1

    with create_engine(url).connect() as connection:
        run = connection.execute(
            select(
                WorkflowRunRow.current_step_index,
                WorkflowRunRow.checkpoint_id,
                WorkflowRunRow.status,
            )
        ).one()
        checkpoint = connection.execute(
            select(WorkflowCheckpointRow.step_index, WorkflowCheckpointRow.payload).where(
                WorkflowCheckpointRow.id == run.checkpoint_id
            )
        ).one()
        assert run.current_step_index == checkpoint.step_index == 1
        assert run.status == "failed"
        assert checkpoint.payload["stepIndex"] == 1


def test_paused_failure_stops_blocked_runner_and_failure_rollback_restores_pause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = database_url(tmp_path / "paused-failure-rollback.db")
    app = create_app(delay_ms=20, database_url=url)
    original_commit = UnitOfWork.commit

    def fail_commit(_: UnitOfWork) -> None:
        raise RuntimeError("controlled failed-transition commit failure")

    with TestClient(app, raise_server_exceptions=False) as api:
        assert api.post("/api/simulator/start").status_code == 200
        asyncio.run(asyncio.sleep(0.04))
        paused = api.post("/api/simulator/pause").json()["data"]
        old_checkpoint = api.get("/api/system/status").json()["data"]["lastCheckpointId"]
        monkeypatch.setattr(UnitOfWork, "commit", fail_commit)
        failed_commit = api.post("/api/simulator/failure", json={"scenario": "archive_unavailable"})
        monkeypatch.setattr(UnitOfWork, "commit", original_commit)
        assert failed_commit.status_code == 500
        rolled_back = api.get("/api/system/status").json()["data"]
        assert rolled_back["simulator"]["state"] == "paused"
        assert rolled_back["simulator"]["currentStep"] == paused["currentStep"]
        assert rolled_back["lastCheckpointId"] == old_checkpoint
        assert api.get("/api/tasks/task-demo").json()["data"]["status"] != "failed"
        successful = api.post("/api/simulator/failure", json={"scenario": "sentinel_rejection"})
        assert successful.status_code == 200
        assert app.state.simulator._runner is None
        assert api.post("/api/simulator/resume").status_code == 409
        assert api.get("/api/health").status_code == 200
        assert api.post("/api/simulator/reset").status_code == 200

    with create_engine(url).connect() as connection:
        assert (
            connection.scalar(
                select(func.count())
                .select_from(WorkflowCheckpointRow)
                .where(WorkflowCheckpointRow.step_identifier == "failure.archive_unavailable")
            )
            == 0
        )
        assert (
            connection.scalar(
                select(func.count())
                .select_from(AuditEventRow)
                .where(
                    AuditEventRow.payload["payload"]["scenario"].as_string()
                    == "archive_unavailable"
                )
            )
            == 0
        )
        assert (
            connection.scalar(
                select(func.count())
                .select_from(OutboxEventRow)
                .where(
                    OutboxEventRow.envelope["payload"]["scenario"].as_string()
                    == "archive_unavailable"
                )
            )
            == 0
        )


def test_idle_failure_is_durable_without_fabricating_workflow_run(tmp_path: Path) -> None:
    url = database_url(tmp_path / "idle-failure.db")
    with TestClient(create_app(delay_ms=1, database_url=url)) as first:
        assert (
            first.post(
                "/api/simulator/failure", json={"scenario": "websocket_disconnect"}
            ).status_code
            == 200
        )
        assert first.get("/api/system/status").json()["data"]["simulator"]["state"] == "failed"
    with create_engine(url).connect() as connection:
        assert connection.scalar(select(func.count()).select_from(WorkflowRunRow)) == 0
        assert connection.scalar(select(func.count()).select_from(WorkflowCheckpointRow)) == 0
        assert connection.scalar(select(SystemStateRow.simulator_status)) == "failed"
    with TestClient(create_app(delay_ms=1, database_url=url)) as second:
        assert second.get("/api/system/status").json()["data"]["simulator"]["state"] == "failed"
        assert (
            second.post("/api/simulator/start").json()["error"]["code"]
            == "SIMULATOR_RESET_REQUIRED"
        )
        assert second.post("/api/simulator/reset").status_code == 200
        assert second.get("/api/system/status").json()["data"]["simulator"]["state"] == "idle"


def _durable_mutation_counts(url: str) -> tuple[int, int, int, int]:
    with create_engine(url).connect() as connection:
        return (
            connection.scalar(select(func.count()).select_from(WorkflowRunRow)) or 0,
            connection.scalar(select(func.count()).select_from(WorkflowCheckpointRow)) or 0,
            connection.scalar(
                select(func.count())
                .select_from(AuditEventRow)
                .where(AuditEventRow.event_type == "system.simulator.started")
            )
            or 0,
            connection.scalar(
                select(func.count())
                .select_from(OutboxEventRow)
                .where(OutboxEventRow.event_type == "system.simulator.started")
            )
            or 0,
        )


def test_start_rejects_every_non_idle_state_without_creating_durable_work(
    tmp_path: Path,
) -> None:
    cases = {
        "paused": "SIMULATOR_RESUME_OR_RESET_REQUIRED",
        "recovery": "SIMULATOR_RECOVERY_REQUIRED",
        "failed": "SIMULATOR_RESET_REQUIRED",
        "completed": "SIMULATOR_RESET_REQUIRED",
        "emergency": "EMERGENCY_STOP_ACTIVE",
        "running": "SIMULATOR_ALREADY_RUNNING",
    }
    for case, error_code in cases.items():
        url = database_url(tmp_path / f"start-guard-{case}.db")
        if case == "failed":
            with TestClient(create_app(delay_ms=1, database_url=url)) as setup:
                setup.post("/api/simulator/failure", json={"scenario": "sentinel_rejection"})
        elif case == "emergency":
            with TestClient(create_app(delay_ms=1, database_url=url)) as setup:
                setup.post("/api/system/emergency-stop")
        else:
            with TestClient(
                create_app(delay_ms=20 if case != "completed" else 1, database_url=url)
            ) as setup:
                setup.post("/api/simulator/start")
                if case == "paused":
                    asyncio.run(asyncio.sleep(0.03))
                    setup.post("/api/simulator/pause")
                elif case == "completed":
                    for _ in range(200):
                        if (
                            setup.get("/api/system/status").json()["data"]["simulator"]["state"]
                            == "completed"
                        ):
                            break
                        asyncio.run(asyncio.sleep(0.005))
                elif case == "running":
                    setup.app.state.simulator._resume.clear()
                    asyncio.run(asyncio.sleep(0.02))
                    before = _durable_mutation_counts(url)
                    rejected = setup.post("/api/simulator/start")
                    assert rejected.json()["error"]["code"] == error_code
                    assert _durable_mutation_counts(url) == before
                    setup.post("/api/simulator/reset")
                    continue

        with TestClient(create_app(delay_ms=1, database_url=url)) as verifier:
            before = _durable_mutation_counts(url)
            rejected = verifier.post("/api/simulator/start")
            assert rejected.status_code == 409
            assert rejected.json()["error"]["code"] == error_code
            assert _durable_mutation_counts(url) == before
            assert verifier.post("/api/simulator/reset").status_code == 200
            assert verifier.post("/api/simulator/start").status_code == 200
            assert verifier.post("/api/simulator/reset").status_code == 200


def test_expired_pending_approval_commits_once_restarts_and_abandons_claim(
    tmp_path: Path,
) -> None:
    url = database_url(tmp_path / "approval-expiration.db")
    app = create_app(delay_ms=1, database_url=url)
    approval = app.state.repository.approvals["approval-pending"]
    approval.expiresAt = datetime.now(UTC) - timedelta(seconds=1)
    app.state.repository.persist()
    headers = {"Idempotency-Key": "expired-decision"}
    with TestClient(app) as api:
        with api.websocket_connect("/ws/events") as socket:
            snapshot = socket.receive_json()
            first = api.post("/api/approvals/approval-pending/approve", json={}, headers=headers)
            event = socket.receive_json()
        assert first.status_code == 409
        assert first.json()["error"]["code"] == "APPROVAL_EXPIRED"
        assert event["eventType"] == "approval.expired"
        assert event["eventSessionId"] == snapshot["eventSessionId"]
        assert event["sequenceNumber"] == snapshot["sequenceNumber"] + 1
        assert api.get("/api/approvals/approval-pending").json()["data"]["status"] == "expired"
        repeated = api.post("/api/approvals/approval-pending/approve", json={}, headers=headers)
        assert repeated.status_code == 409
        assert repeated.json()["error"]["code"] == "APPROVAL_ALREADY_PROCESSED"

    with create_engine(url).connect() as connection:
        row = connection.execute(
            select(ApprovalRow.status, ApprovalRow.payload).where(
                ApprovalRow.id == "approval-pending"
            )
        ).one()
        assert row.status == row.payload["status"] == "expired"
        assert (
            connection.scalar(
                select(func.count())
                .select_from(AuditEventRow)
                .where(AuditEventRow.event_type == "approval.expired")
            )
            == 1
        )
        assert (
            connection.scalar(
                select(func.count())
                .select_from(OutboxEventRow)
                .where(OutboxEventRow.event_type == "approval.expired")
            )
            == 1
        )
        assert (
            connection.scalar(
                select(IdempotencyRecordRow.id).where(
                    IdempotencyRecordRow.idempotency_key == headers["Idempotency-Key"]
                )
            )
            is None
        )
    with TestClient(create_app(delay_ms=1, database_url=url)) as recreated:
        assert (
            recreated.get("/api/approvals/approval-pending").json()["data"]["status"] == "expired"
        )
        assert (
            recreated.post("/api/approvals/approval-pending/approve", json={}).json()["error"][
                "code"
            ]
            == "APPROVAL_ALREADY_PROCESSED"
        )


def test_concurrent_expiration_attempts_commit_one_transition(tmp_path: Path) -> None:
    url = database_url(tmp_path / "approval-expiration-concurrent.db")
    app = create_app(delay_ms=1, database_url=url)
    approval = app.state.repository.approvals["approval-pending"]
    approval.expiresAt = datetime.now(UTC) - timedelta(seconds=1)
    app.state.repository.persist()
    with TestClient(app) as api, ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(api.post, "/api/approvals/approval-pending/approve", json={})
        second = pool.submit(api.post, "/api/approvals/approval-pending/reject", json={})
        responses = [first.result(timeout=5), second.result(timeout=5)]
        assert {response.json()["error"]["code"] for response in responses} == {
            "APPROVAL_EXPIRED",
            "APPROVAL_ALREADY_PROCESSED",
        }
    with create_engine(url).connect() as connection:
        assert (
            connection.scalar(
                select(func.count())
                .select_from(AuditEventRow)
                .where(AuditEventRow.event_type == "approval.expired")
            )
            == 1
        )
        assert (
            connection.scalar(
                select(func.count())
                .select_from(OutboxEventRow)
                .where(OutboxEventRow.event_type == "approval.expired")
            )
            == 1
        )


def test_expiration_commit_failure_rolls_back_cache_audit_outbox_and_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = database_url(tmp_path / "approval-expiration-rollback.db")
    app = create_app(delay_ms=1, database_url=url)
    approval = app.state.repository.approvals["approval-pending"]
    approval.expiresAt = datetime.now(UTC) - timedelta(seconds=1)
    app.state.repository.persist()
    original_commit = UnitOfWork.commit

    def fail_commit(_: UnitOfWork) -> None:
        raise RuntimeError("controlled expiration commit failure")

    with TestClient(app, raise_server_exceptions=False) as api:
        monkeypatch.setattr(UnitOfWork, "commit", fail_commit)
        failed = api.post("/api/approvals/approval-pending/approve", json={})
        monkeypatch.setattr(UnitOfWork, "commit", original_commit)
        assert failed.status_code == 500
        assert api.get("/api/approvals/approval-pending").json()["data"]["status"] == "pending"
        retry = api.post("/api/approvals/approval-pending/approve", json={})
        assert retry.status_code == 409
        assert retry.json()["error"]["code"] == "APPROVAL_EXPIRED"

    with create_engine(url).connect() as connection:
        assert (
            connection.scalar(
                select(func.count())
                .select_from(AuditEventRow)
                .where(AuditEventRow.event_type == "approval.expired")
            )
            == 1
        )
        assert (
            connection.scalar(
                select(func.count())
                .select_from(OutboxEventRow)
                .where(OutboxEventRow.event_type == "approval.expired")
            )
            == 1
        )


def test_emergency_stop_preserves_completed_and_failed_terminal_runs(tmp_path: Path) -> None:
    for terminal_state in ("completed", "failed"):
        url = database_url(tmp_path / f"terminal-emergency-{terminal_state}.db")
        app = create_app(delay_ms=1, database_url=url)
        with TestClient(app) as api:
            assert api.post("/api/simulator/start").status_code == 200
            if terminal_state == "completed":
                for _ in range(200):
                    state = api.get("/api/system/status").json()["data"]["simulator"]["state"]
                    if state == "completed":
                        break
                    asyncio.run(asyncio.sleep(0.005))
                assert state == "completed"
            else:
                assert (
                    api.post(
                        "/api/simulator/failure", json={"scenario": "archive_unavailable"}
                    ).status_code
                    == 200
                )
            before = api.get("/api/system/status").json()["data"]
            run_id = before["activeWorkflowRunId"]
            checkpoint_id = before["lastCheckpointId"]
            with create_engine(url).connect() as connection:
                checkpoint_count = connection.scalar(
                    select(func.count()).select_from(WorkflowCheckpointRow)
                )
            stopped = api.post("/api/system/emergency-stop").json()["data"]
            assert stopped["emergencyStop"] is True
            assert stopped["simulator"]["state"] == terminal_state
            assert stopped["lastCheckpointId"] == checkpoint_id
            repeated = api.post("/api/system/emergency-stop").json()["data"]
            assert repeated["emergencyStop"] is True
            assert repeated["simulator"] == stopped["simulator"]
            assert repeated["lastCheckpointId"] == stopped["lastCheckpointId"]

        with create_engine(url).connect() as connection:
            run = connection.execute(
                select(
                    WorkflowRunRow.status,
                    WorkflowRunRow.checkpoint_id,
                    WorkflowRunRow.resume_eligibility,
                ).where(WorkflowRunRow.id == run_id)
            ).one()
            assert run == (terminal_state, checkpoint_id, False)
            assert (
                connection.scalar(select(func.count()).select_from(WorkflowCheckpointRow))
                == checkpoint_count
            )

        with TestClient(create_app(delay_ms=1, database_url=url)) as recreated:
            restored = recreated.get("/api/system/status").json()["data"]
            assert restored["simulator"]["state"] == terminal_state
            assert restored["lastCheckpointId"] == checkpoint_id
            assert recreated.post("/api/simulator/resume").status_code == 409
            assert recreated.post("/api/system/resume").status_code == 200
            assert recreated.post("/api/simulator/reset").status_code == 200


def test_repeated_emergency_stop_does_not_duplicate_active_checkpoint_or_event(
    tmp_path: Path,
) -> None:
    url = database_url(tmp_path / "repeated-active-emergency.db")
    with TestClient(create_app(delay_ms=100, database_url=url)) as api:
        assert api.post("/api/simulator/start").status_code == 200
        assert api.post("/api/system/emergency-stop").status_code == 200
        first_status = api.get("/api/system/status").json()["data"]
        with create_engine(url).connect() as connection:
            first_counts = (
                connection.scalar(select(func.count()).select_from(WorkflowCheckpointRow)),
                connection.scalar(
                    select(func.count())
                    .select_from(AuditEventRow)
                    .where(AuditEventRow.event_type == "system.emergency_stop")
                ),
                connection.scalar(
                    select(func.count())
                    .select_from(OutboxEventRow)
                    .where(OutboxEventRow.event_type == "system.emergency_stop")
                ),
            )

        assert api.post("/api/system/emergency-stop").status_code == 200
        repeated_status = api.get("/api/system/status").json()["data"]
        assert repeated_status["emergencyStop"] is True
        assert repeated_status["simulator"] == first_status["simulator"]
        assert repeated_status["lastCheckpointId"] == first_status["lastCheckpointId"]
        with create_engine(url).connect() as connection:
            repeated_counts = (
                connection.scalar(select(func.count()).select_from(WorkflowCheckpointRow)),
                connection.scalar(
                    select(func.count())
                    .select_from(AuditEventRow)
                    .where(AuditEventRow.event_type == "system.emergency_stop")
                ),
                connection.scalar(
                    select(func.count())
                    .select_from(OutboxEventRow)
                    .where(OutboxEventRow.event_type == "system.emergency_stop")
                ),
            )
        assert repeated_counts == first_counts


@pytest.mark.parametrize("max_attempts", [1, 2])
def test_outbox_dispatch_stops_at_configured_retry_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, max_attempts: int
) -> None:
    url = database_url(tmp_path / f"outbox-retry-limit-{max_attempts}.db")
    monkeypatch.setenv("JARVIS_OUTBOX_MAX_ATTEMPTS", str(max_attempts))
    with TestClient(create_app(delay_ms=1, database_url=url)) as api:
        broken = api.post(
            "/api/tasks",
            json={"title": "Retry ceiling", "description": "Corrupt this durable envelope"},
        )
        healthy = api.post(
            "/api/tasks",
            json={"title": "Healthy delivery", "description": "Keep this envelope valid"},
        )
        assert broken.status_code == healthy.status_code == 201
        broken_task_id = broken.json()["data"]["id"]
        healthy_task_id = healthy.json()["data"]["id"]

    engine = create_engine(url)
    with engine.begin() as connection:
        rows = connection.execute(
            select(OutboxEventRow.id, OutboxEventRow.envelope).where(
                OutboxEventRow.envelope["taskId"].as_string().in_([broken_task_id, healthy_task_id])
            )
        ).all()
        broken_row = next(row for row in rows if row.envelope["taskId"] == broken_task_id)
        healthy_row = next(row for row in rows if row.envelope["taskId"] == healthy_task_id)
        invalid_envelope = dict(broken_row.envelope)
        invalid_envelope.pop("eventType")
        invalid_envelope.pop("eventId")
        connection.execute(
            update(OutboxEventRow)
            .where(OutboxEventRow.id == broken_row.id)
            .values(
                envelope=invalid_envelope,
                status="failed",
                publish_attempt_count=0,
                last_publish_error=None,
            )
        )
        connection.execute(
            update(OutboxEventRow)
            .where(OutboxEventRow.id == healthy_row.id)
            .values(
                status="pending",
                publish_attempt_count=0,
                published_at=None,
                last_publish_error=None,
            )
        )

    retrying = create_app(delay_ms=1, database_url=url)
    assert len(retrying.state.repository.pending_outbox()) == 2
    for _ in range(max_attempts):
        asyncio.run(retrying.state.broker.dispatch_pending())
    assert retrying.state.repository.pending_outbox() == []
    asyncio.run(retrying.state.broker.dispatch_pending())
    retrying.state.engine.dispose()

    with engine.connect() as connection:
        exhausted = connection.execute(
            select(
                OutboxEventRow.status,
                OutboxEventRow.publish_attempt_count,
                OutboxEventRow.last_publish_error,
            ).where(OutboxEventRow.id == broken_row.id)
        ).one()
        assert exhausted.status == "failed"
        assert exhausted.publish_attempt_count == max_attempts
        assert exhausted.last_publish_error
        delivered = connection.execute(
            select(
                OutboxEventRow.status,
                OutboxEventRow.publish_attempt_count,
            ).where(OutboxEventRow.id == healthy_row.id)
        ).one()
        assert delivered == ("published", 1)

    recreated = create_app(delay_ms=1, database_url=url)
    with TestClient(recreated) as api:
        assert recreated.state.repository.pending_outbox() == []
        health = api.get("/api/health").json()["data"]
        assert health["status"] == "degraded"
        assert health["outboxExhaustedCount"] == 1
        system = api.get("/api/system/status").json()["data"]
        assert system["status"] == "degraded"
        assert system["outboxPendingCount"] == 1
        assert system["outboxExhaustedCount"] == 1
    with engine.connect() as connection:
        assert (
            connection.scalar(
                select(OutboxEventRow.publish_attempt_count).where(
                    OutboxEventRow.id == broken_row.id
                )
            )
            == max_attempts
        )
    engine.dispose()


def test_invalid_outbox_attempt_limits_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    for value in ("0", "-1", "not-a-number"):
        monkeypatch.setenv("JARVIS_OUTBOX_MAX_ATTEMPTS", value)
        with pytest.raises(ValidationError):
            Settings()


@pytest.mark.asyncio
async def test_immediate_publish_and_dispatcher_do_not_republish_same_row(
    tmp_path: Path,
) -> None:
    url = database_url(tmp_path / "outbox-dispatch-race.db")
    app = create_app(delay_ms=1, database_url=url)
    entered = asyncio.Event()
    release = asyncio.Event()

    class BlockingClient:
        def __init__(self) -> None:
            self.calls = 0

        async def send_json(self, _payload: object) -> None:
            self.calls += 1
            entered.set()
            await release.wait()

    client = BlockingClient()
    app.state.broker.clients.add(client)  # type: ignore[arg-type]
    emit = asyncio.create_task(app.state.broker.emit("system.dispatch.race", {"active": True}))
    await entered.wait()
    dispatch = asyncio.create_task(app.state.broker.dispatch_pending())
    await asyncio.sleep(0)
    assert client.calls == 1
    assert not dispatch.done()
    release.set()
    await asyncio.gather(emit, dispatch)

    with create_engine(url).connect() as connection:
        row = connection.execute(
            select(OutboxEventRow.status, OutboxEventRow.publish_attempt_count).where(
                OutboxEventRow.event_type == "system.dispatch.race"
            )
        ).one()
        assert row == ("published", 1)
    app.state.engine.dispose()


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
        assert interrupted["status"] == "degraded"
        assert interrupted["simulator"]["state"] == "recovery_required"
        health = second.get("/api/health").json()["data"]
        assert health["status"] == "degraded"
        assert health["recoveryRequired"] is True
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


def test_command_commit_is_atomic_and_failed_cache_state_is_reloaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = database_url(tmp_path / "command-rollback.db")
    app = create_app(delay_ms=1, database_url=url)
    body = {"title": "Must roll back", "description": "Fail immediately before commit"}
    headers = {"Idempotency-Key": "rollback-command"}
    original_commit = UnitOfWork.commit

    def fail_before_commit(_: UnitOfWork) -> None:
        raise RuntimeError("simulated failure immediately before commit")

    with TestClient(app, raise_server_exceptions=False) as api:
        monkeypatch.setattr(UnitOfWork, "commit", fail_before_commit)
        failed = api.post("/api/tasks", json=body, headers=headers)
        monkeypatch.setattr(UnitOfWork, "commit", original_commit)
        assert failed.status_code == 500
        assert all(item["title"] != body["title"] for item in api.get("/api/tasks").json()["data"])
        assert (
            api.post(
                "/api/tasks",
                json={"title": "Unrelated success", "description": "Must not persist failed state"},
            ).status_code
            == 201
        )

    with create_engine(url).connect() as connection:
        assert connection.scalar(select(TaskRow.id).where(TaskRow.title == body["title"])) is None
        assert (
            len(
                connection.execute(
                    select(AuditEventRow.id).where(AuditEventRow.event_type == "task.created")
                ).all()
            )
            == 1
        )
        assert (
            connection.scalar(
                select(IdempotencyRecordRow.id).where(
                    IdempotencyRecordRow.idempotency_key == headers["Idempotency-Key"]
                )
            )
            is None
        )
        failed_outbox = connection.execute(
            select(OutboxEventRow.envelope).where(OutboxEventRow.event_type == "task.created")
        ).scalars()
        assert all(row["payload"]["task"]["title"] != body["title"] for row in failed_outbox)


def test_successful_command_commits_domain_audit_outbox_and_idempotency_together(
    tmp_path: Path,
) -> None:
    url = database_url(tmp_path / "command-commit.db")
    headers = {"Idempotency-Key": "atomic-success"}
    body = {"title": "Atomic success", "description": "Commit one durable command"}
    with TestClient(create_app(delay_ms=1, database_url=url)) as api:
        response = api.post("/api/tasks", json=body, headers=headers)
        assert response.status_code == 201
        task_id = response.json()["data"]["id"]
        retry = api.post("/api/tasks", json=body, headers=headers)
        assert retry.json() == response.json()

    with create_engine(url).connect() as connection:
        assert connection.scalar(select(TaskRow.id).where(TaskRow.id == task_id)) == task_id
        assert (
            connection.scalar(select(AuditEventRow.id).where(AuditEventRow.task_id == task_id))
            is not None
        )
        assert (
            connection.scalar(
                select(OutboxEventRow.id).where(OutboxEventRow.event_type == "task.created")
            )
            is not None
        )
        record = connection.execute(
            select(
                IdempotencyRecordRow.response_status,
                IdempotencyRecordRow.created_resource_id,
            ).where(IdempotencyRecordRow.idempotency_key == headers["Idempotency-Key"])
        ).one()
        assert record == (201, task_id)


def test_keyed_workflow_start_commits_run_checkpoint_outbox_and_response_together(
    tmp_path: Path,
) -> None:
    url = database_url(tmp_path / "workflow-command-commit.db")
    headers = {"Idempotency-Key": "atomic-workflow-start"}
    with TestClient(create_app(delay_ms=100, database_url=url)) as api:
        response = api.post("/api/simulator/start", headers=headers)
        assert response.status_code == 200
        run_id = api.get("/api/system/status").json()["data"]["activeWorkflowRunId"]

    with create_engine(url).connect() as connection:
        run = connection.execute(
            select(WorkflowRunRow.id, WorkflowRunRow.checkpoint_id).where(
                WorkflowRunRow.id == run_id
            )
        ).one()
        assert run.checkpoint_id is not None
        assert (
            connection.scalar(
                select(WorkflowCheckpointRow.id).where(
                    WorkflowCheckpointRow.id == run.checkpoint_id
                )
            )
            == run.checkpoint_id
        )
        assert (
            connection.scalar(
                select(AuditEventRow.id).where(
                    AuditEventRow.event_type == "system.simulator.started"
                )
            )
            is not None
        )
        assert (
            connection.scalar(
                select(OutboxEventRow.id).where(
                    OutboxEventRow.event_type == "system.simulator.started"
                )
            )
            is not None
        )
        assert (
            connection.scalar(
                select(IdempotencyRecordRow.response_status).where(
                    IdempotencyRecordRow.idempotency_key == headers["Idempotency-Key"]
                )
            )
            == 200
        )


def test_failed_unkeyed_mutations_reload_cached_and_directly_persisted_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = database_url(tmp_path / "unkeyed-rollback.db")
    app = create_app(delay_ms=1, database_url=url)
    original_commit = UnitOfWork.commit

    def fail_before_commit(_: UnitOfWork) -> None:
        raise RuntimeError("simulated failure immediately before commit")

    with TestClient(app, raise_server_exceptions=False) as api:
        monkeypatch.setattr(UnitOfWork, "commit", fail_before_commit)
        assert api.post("/api/tasks/task-demo/pause").status_code == 500
        monkeypatch.setattr(UnitOfWork, "commit", original_commit)
        assert api.get("/api/tasks/task-demo").json()["data"]["status"] == "in_progress"

        monkeypatch.setattr(UnitOfWork, "commit", fail_before_commit)
        assert api.post("/api/notifications/notification-1/read").status_code == 500
        monkeypatch.setattr(UnitOfWork, "commit", original_commit)
        notification = next(
            item
            for item in api.get("/api/notifications").json()["data"]
            if item["id"] == "notification-1"
        )
        assert notification["isRead"] is False

    with TestClient(create_app(delay_ms=1, database_url=url)) as verifier:
        assert verifier.get("/api/tasks/task-demo").json()["data"]["status"] == "in_progress"
        notification = next(
            item
            for item in verifier.get("/api/notifications").json()["data"]
            if item["id"] == "notification-1"
        )
        assert notification["isRead"] is False


def test_seeded_audit_history_survives_blank_startup_and_recreation(tmp_path: Path) -> None:
    url = database_url(tmp_path / "seed-audit.db")
    with TestClient(create_app(delay_ms=1, database_url=url)) as first:
        first_audit = first.get("/api/audit-events").json()["data"]
        assert any(item["id"] == "audit-1" for item in first_audit)
        assert any(
            item["id"] == "audit-1" for item in first.app.state.repository.snapshot()["auditEvents"]
        )
        with first.websocket_connect("/ws/events") as socket:
            snapshot = socket.receive_json()
            assert any(
                item["id"] == "audit-1" for item in snapshot["payload"]["snapshot"]["auditEvents"]
            )
    with TestClient(create_app(delay_ms=1, database_url=url)) as second:
        assert any(
            item["id"] == "audit-1" for item in second.get("/api/audit-events").json()["data"]
        )
