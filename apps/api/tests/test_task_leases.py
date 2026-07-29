from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.core.errors import DomainError
from app.db.models import (
    AuditEventRow,
    OutboxEventRow,
    TaskAttemptRow,
    TaskLeaseRow,
    WorkflowCheckpointRow,
    WorkflowRunRow,
)
from app.main import create_app
from app.models.domain import TaskDependency


def database_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def prepare_empty_queue(app: object) -> None:
    repository = app.state.repository
    now = datetime.now(UTC)
    for task in repository.tasks.values():
        task.status = "completed"
        task.progress = 100
        task.statusMessage = "Test queue fixture completed"
        task.completedAt = now
        task.updatedAt = now
    repository.persist()


def create_task(client: TestClient, title: str, priority: str = "high") -> str:
    response = client.post(
        "/api/tasks",
        json={"title": title, "description": f"Execute {title}", "priority": priority},
    )
    assert response.status_code == 201
    return str(response.json()["data"]["id"])


def register_worker(client: TestClient, instance_id: str, lease_seconds: int = 30) -> dict:
    response = client.post(
        "/api/workers",
        json={
            "name": instance_id,
            "instanceId": instance_id,
            "leaseSeconds": lease_seconds,
        },
    )
    assert response.status_code == 201
    return response.json()["data"]


def acquire(client: TestClient, worker_id: str, lease_seconds: int = 30) -> dict | None:
    response = client.post(
        f"/api/workers/{worker_id}/tasks/acquire",
        json={"leaseSeconds": lease_seconds},
    )
    assert response.status_code == 200
    return response.json()["data"]


def test_worker_lease_lifecycle_is_fenced_audited_and_published(tmp_path: Path) -> None:
    app = create_app(delay_ms=1, database_url=database_url(tmp_path / "lifecycle.db"))
    with TestClient(app) as client:
        prepare_empty_queue(app)
        task_id = create_task(client, "Fenced lifecycle", "urgent")
        worker = register_worker(client, "worker-lifecycle")
        acquired = acquire(client, worker["id"])
        assert acquired is not None
        assert acquired["task"]["id"] == task_id
        assert acquired["task"]["status"] == "in_progress"
        lease = acquired["lease"]
        active_health = client.get("/api/health").json()["data"]
        assert active_health["activeWorkerCount"] == 1
        assert active_health["activeLeaseCount"] == 1

        renewed = client.post(
            f"/api/tasks/{task_id}/lease/renew",
            json={
                "workerId": worker["id"],
                "leaseToken": lease["leaseToken"],
                "leaseSeconds": 60,
            },
        )
        assert renewed.status_code == 200
        assert renewed.json()["data"]["version"] == 2

        stale = client.post(
            f"/api/tasks/{task_id}/lease/complete",
            json={
                "workerId": worker["id"],
                "leaseToken": "not-the-token",
                "result": "must not commit",
            },
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "TASK_LEASE_LOST"

        assert client.post("/api/system/emergency-stop").status_code == 200
        blocked_by_stop = client.post(
            f"/api/tasks/{task_id}/lease/complete",
            json={
                "workerId": worker["id"],
                "leaseToken": lease["leaseToken"],
                "result": "must wait for resume",
            },
        )
        assert blocked_by_stop.status_code == 423
        assert blocked_by_stop.json()["error"]["code"] == "EMERGENCY_STOP_ACTIVE"
        assert client.post("/api/system/resume").status_code == 200

        completed = client.post(
            f"/api/tasks/{task_id}/lease/complete",
            json={
                "workerId": worker["id"],
                "leaseToken": lease["leaseToken"],
                "result": "durable result",
            },
        )
        assert completed.status_code == 200
        assert completed.json()["data"]["status"] == "completed"

        duplicate = client.post(
            f"/api/tasks/{task_id}/lease/complete",
            json={
                "workerId": worker["id"],
                "leaseToken": lease["leaseToken"],
                "result": "durable result",
            },
        )
        assert duplicate.status_code == 200
        assert duplicate.json()["data"]["result"] == "durable result"

        health = client.get("/api/health").json()["data"]
        assert health["activeWorkerCount"] == 1
        assert health["activeLeaseCount"] == 0
        with app.state.repository.session_factory() as session:
            attempt = session.scalar(
                select(TaskAttemptRow).where(TaskAttemptRow.task_id == task_id)
            )
            assert attempt is not None
            assert attempt.outcome == "completed"
            event_types = set(
                session.scalars(
                    select(OutboxEventRow.event_type).where(
                        OutboxEventRow.correlation_id == task_id
                    )
                )
            )
            audit_types = set(
                session.scalars(
                    select(AuditEventRow.event_type).where(AuditEventRow.task_id == task_id)
                )
            )
        assert {"task.lease.acquired", "task.lease.renewed", "task.completed"} <= event_types
        assert {"task.lease.acquired", "task.lease.renewed", "task.completed"} <= audit_types


def test_release_drain_and_cancellation_revoke_ownership(tmp_path: Path) -> None:
    app = create_app(delay_ms=1, database_url=database_url(tmp_path / "release.db"))
    with TestClient(app) as client:
        prepare_empty_queue(app)
        task_id = create_task(client, "Release and cancel", "urgent")
        first = register_worker(client, "worker-release")
        acquired = acquire(client, first["id"])
        assert acquired is not None
        token = acquired["lease"]["leaseToken"]
        released = client.post(
            f"/api/tasks/{task_id}/lease/release",
            json={"workerId": first["id"], "leaseToken": token},
        )
        assert released.status_code == 200
        assert released.json()["data"]["status"] == "queued"

        second = register_worker(client, "worker-cancel")
        reacquired = acquire(client, second["id"])
        assert reacquired is not None
        cancelled = client.post(f"/api/tasks/{task_id}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["data"]["status"] == "cancelled"
        stale = client.post(
            f"/api/tasks/{task_id}/lease/complete",
            json={
                "workerId": second["id"],
                "leaseToken": reacquired["lease"]["leaseToken"],
                "result": "late result",
            },
        )
        assert stale.status_code == 409

        other_task_id = create_task(client, "Drain release", "urgent")
        drain_lease = acquire(client, first["id"])
        assert drain_lease is not None
        assert drain_lease["task"]["id"] == other_task_id
        drained = client.post(f"/api/workers/{first['id']}/drain")
        assert drained.status_code == 200
        assert drained.json()["data"]["status"] == "draining"
        assert client.get(f"/api/tasks/{other_task_id}").json()["data"]["status"] == "queued"
        rejected = client.post(
            f"/api/workers/{first['id']}/tasks/acquire",
            json={"leaseSeconds": 30},
        )
        assert rejected.status_code == 409
        assert rejected.json()["error"]["code"] == "WORKER_NOT_ACTIVE"
        stopped = client.post(f"/api/workers/{first['id']}/stop")
        assert stopped.status_code == 200
        assert stopped.json()["data"]["status"] == "stopped"
        assert client.post(f"/api/workers/{first['id']}/heartbeat").status_code == 409


def test_expired_lease_recovers_after_restart_and_rejects_stale_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JARVIS_TASK_LEASE_RECOVERY_INTERVAL_MS", "60000")
    url = database_url(tmp_path / "restart.db")
    first_app = create_app(delay_ms=1, database_url=url)
    with TestClient(first_app) as first:
        prepare_empty_queue(first_app)
        task_id = create_task(first, "Restart recovery", "urgent")
        worker = register_worker(first, "worker-before-crash", 1)
        acquired = acquire(first, worker["id"], 1)
        assert acquired is not None
        stale_token = acquired["lease"]["leaseToken"]
        with first_app.state.repository.session_factory() as session, session.begin():
            session.execute(
                update(TaskLeaseRow)
                .where(TaskLeaseRow.task_id == task_id)
                .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
        expired_health = first.get("/api/health").json()["data"]
        assert expired_health["status"] == "degraded"
        assert expired_health["expiredLeaseCount"] == 1

    second_app = create_app(delay_ms=1, database_url=url)
    with TestClient(second_app) as second:
        assert second.get(f"/api/tasks/{task_id}").json()["data"]["status"] == "retrying"
        with second_app.state.repository.session_factory() as session:
            expiration_events = list(
                session.scalars(
                    select(OutboxEventRow.event_type).where(
                        OutboxEventRow.correlation_id == task_id,
                        OutboxEventRow.event_type == "task.lease.expired",
                    )
                )
            )
        assert expiration_events == ["task.lease.expired"]
        successor = register_worker(second, "worker-after-crash")
        reassigned = acquire(second, successor["id"])
        assert reassigned is not None
        assert reassigned["task"]["id"] == task_id
        stale = second.post(
            f"/api/tasks/{task_id}/lease/complete",
            json={
                "workerId": worker["id"],
                "leaseToken": stale_token,
                "result": "stale completion",
            },
        )
        assert stale.status_code == 409
        current = second.post(
            f"/api/tasks/{task_id}/lease/complete",
            json={
                "workerId": successor["id"],
                "leaseToken": reassigned["lease"]["leaseToken"],
                "result": "successor completion",
            },
        )
        assert current.status_code == 200


def test_unexpired_lease_and_token_survive_database_recreation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JARVIS_TASK_LEASE_RECOVERY_INTERVAL_MS", "60000")
    url = database_url(tmp_path / "unexpired-restart.db")
    first_app = create_app(delay_ms=1, database_url=url)
    with TestClient(first_app) as first:
        prepare_empty_queue(first_app)
        task_id = create_task(first, "Unexpired restart", "urgent")
        worker = register_worker(first, "worker-unexpired", 60)
        acquired = acquire(first, worker["id"], 60)
        assert acquired is not None
        lease_token = acquired["lease"]["leaseToken"]

    second_app = create_app(delay_ms=1, database_url=url)
    with TestClient(second_app) as second:
        health = second.get("/api/health").json()["data"]
        assert health["activeLeaseCount"] == 1
        assert health["expiredLeaseCount"] == 0
        renewed = second.post(
            f"/api/tasks/{task_id}/lease/renew",
            json={
                "workerId": worker["id"],
                "leaseToken": lease_token,
                "leaseSeconds": 60,
            },
        )
        assert renewed.status_code == 200
        completed = second.post(
            f"/api/tasks/{task_id}/lease/complete",
            json={
                "workerId": worker["id"],
                "leaseToken": lease_token,
                "result": "continued after reconnect",
            },
        )
        assert completed.status_code == 200


def test_phase_2a_schema_reports_stale_without_startup_lease_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "phase-2a-only.db"
    url = database_url(path)
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "20260720_01")
    monkeypatch.setenv("JARVIS_AUTO_MIGRATE", "false")
    app = create_app(delay_ms=1, database_url=url)
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["data"]["status"] == "degraded"
        assert health.json()["data"]["schemaCurrent"] is False
        assert health.json()["data"]["activeLeaseCount"] == 0


def test_concurrent_workers_claim_once_and_process_high_volume(tmp_path: Path) -> None:
    app = create_app(delay_ms=1, database_url=database_url(tmp_path / "concurrency.db"))
    with TestClient(app) as client:
        prepare_empty_queue(app)
        task_ids = {create_task(client, f"Concurrent task {index}") for index in range(12)}
        workers = [register_worker(client, f"worker-{index}") for index in range(4)]
        leases = app.state.task_leases

        def process(worker_id: str) -> list[str]:
            completed: list[str] = []
            while True:
                claimed = leases.acquire_task(worker_id)
                if claimed is None:
                    return completed
                task, lease = claimed
                leases.complete_task(task.id, worker_id, lease.leaseToken, f"done:{task.id}")
                completed.append(task.id)

        with ThreadPoolExecutor(max_workers=4) as executor:
            batches = list(executor.map(process, [worker["id"] for worker in workers]))
        completed_ids = [task_id for batch in batches for task_id in batch]
        assert set(completed_ids) == task_ids
        assert len(completed_ids) == len(task_ids)
        assert leases.acquire_task(workers[0]["id"]) is None
        with app.state.repository.session_factory() as session:
            attempts = list(
                session.scalars(select(TaskAttemptRow).where(TaskAttemptRow.task_id.in_(task_ids)))
            )
            active_leases = list(
                session.scalars(select(TaskLeaseRow).where(TaskLeaseRow.task_id.in_(task_ids)))
            )
        assert len(attempts) == len(task_ids)
        assert all(attempt.outcome == "completed" for attempt in attempts)
        assert active_leases == []


def test_exact_task_selector_preserves_eligibility_and_has_one_race_winner(
    tmp_path: Path,
) -> None:
    app = create_app(delay_ms=1, database_url=database_url(tmp_path / "exact-selector.db"))
    with TestClient(app) as client:
        prepare_empty_queue(app)
        selected_id = create_task(client, "Exact selected task")
        other_id = create_task(client, "Other eligible task")
        workers = [
            register_worker(client, "exact-worker-one"),
            register_worker(client, "exact-worker-two"),
        ]

        with ThreadPoolExecutor(max_workers=2) as executor:
            claims = list(
                executor.map(
                    lambda worker: app.state.task_leases.acquire_task(
                        worker["id"], task_id=selected_id
                    ),
                    workers,
                )
            )
        winners = [claim for claim in claims if claim is not None]
        assert len(winners) == 1
        assert winners[0][0].id == selected_id
        generic = app.state.task_leases.acquire_task(workers[1]["id"])
        assert generic is not None
        assert generic[0].id == other_id


def test_retry_limit_and_invalid_lease_states(tmp_path: Path) -> None:
    app = create_app(delay_ms=1, database_url=database_url(tmp_path / "retry.db"))
    with TestClient(app) as client:
        prepare_empty_queue(app)
        task_id = create_task(client, "Retry limit", "urgent")
        app.state.repository.tasks[task_id].maxRetries = 1
        app.state.repository.persist()
        worker = register_worker(client, "worker-retry")

        first = acquire(client, worker["id"])
        assert first is not None
        retried = client.post(
            f"/api/tasks/{task_id}/lease/fail",
            json={
                "workerId": worker["id"],
                "leaseToken": first["lease"]["leaseToken"],
                "error": {"code": "TEMPORARY"},
                "retryable": True,
            },
        )
        assert retried.status_code == 200
        assert retried.json()["data"]["status"] == "retrying"

        second = acquire(client, worker["id"])
        assert second is not None
        exhausted = client.post(
            f"/api/tasks/{task_id}/lease/fail",
            json={
                "workerId": worker["id"],
                "leaseToken": second["lease"]["leaseToken"],
                "error": {"code": "TEMPORARY"},
                "retryable": True,
            },
        )
        assert exhausted.status_code == 200
        assert exhausted.json()["data"]["status"] == "failed"
        with pytest.raises(DomainError, match="task lease"):
            app.state.task_leases.release_lease(
                task_id, worker["id"], second["lease"]["leaseToken"]
            )


def test_priority_dependencies_and_checkpoint_recovery_position(tmp_path: Path) -> None:
    app = create_app(delay_ms=1, database_url=database_url(tmp_path / "checkpoint.db"))
    with TestClient(app) as client:
        prepare_empty_queue(app)
        dependency_id = create_task(client, "Incomplete dependency", "high")
        blocked_id = create_task(client, "Blocked urgent task", "urgent")
        eligible_id = create_task(client, "Eligible low task", "low")
        repository = app.state.repository
        dependency = repository.tasks[dependency_id]
        dependency.status = "paused"
        dependency.statusMessage = "Dependency intentionally incomplete"
        repository.tasks[blocked_id].dependencies = [
            TaskDependency(taskId=dependency_id, type="requires")
        ]
        repository.persist()
        worker = register_worker(client, "worker-dependencies")
        first = acquire(client, worker["id"])
        assert first is not None
        assert first["task"]["id"] == eligible_id
        client.post(
            f"/api/tasks/{eligible_id}/lease/complete",
            json={
                "workerId": worker["id"],
                "leaseToken": first["lease"]["leaseToken"],
                "result": "low priority was the only eligible task",
            },
        )
        dependency = repository.tasks[dependency_id]
        dependency.status = "completed"
        dependency.statusMessage = "Dependency completed"
        dependency.completedAt = datetime.now(UTC)
        dependency.updatedAt = dependency.completedAt
        repository.persist()
        second = acquire(client, worker["id"])
        assert second is not None
        assert second["task"]["id"] == blocked_id

        client.post(
            f"/api/tasks/{blocked_id}/lease/complete",
            json={
                "workerId": worker["id"],
                "leaseToken": second["lease"]["leaseToken"],
                "result": "dependency satisfied",
            },
        )
        demo = repository.tasks["task-demo"]
        demo.status = "queued"
        demo.statusMessage = "Checkpoint lease fixture"
        demo.completedAt = None
        demo.updatedAt = datetime.now(UTC)
        repository.persist()
        now = datetime.now(UTC)
        checkpoint_id = "checkpoint-lease-recovery"
        with repository.session_factory() as session, session.begin():
            session.add(
                WorkflowRunRow(
                    id="run-lease-recovery",
                    correlation_id="run-lease-recovery",
                    root_task_id="task-demo",
                    workflow_type="lease-test",
                    workflow_version="2.0",
                    current_step_index=3,
                    current_step_identifier="step-3",
                    status="running",
                    started_at=now,
                    updated_at=now,
                    retry_count=0,
                    resume_eligibility=True,
                )
            )
            session.flush()
            session.add(
                WorkflowCheckpointRow(
                    id=checkpoint_id,
                    workflow_run_id="run-lease-recovery",
                    workflow_version="2.0",
                    step_index=3,
                    step_identifier="step-3",
                    root_task_id="task-demo",
                    payload={"workflowVersion": "2.0", "stepIndex": 3},
                    created_at=now,
                )
            )
        demo_lease = acquire(client, worker["id"])
        assert demo_lease is not None
        assert demo_lease["task"]["id"] == "task-demo"
        renewed = client.post(
            "/api/tasks/task-demo/lease/renew",
            json={
                "workerId": worker["id"],
                "leaseToken": demo_lease["lease"]["leaseToken"],
                "checkpointId": checkpoint_id,
            },
        )
        assert renewed.status_code == 200
        with repository.session_factory() as session, session.begin():
            session.execute(
                update(TaskLeaseRow)
                .where(TaskLeaseRow.task_id == "task-demo")
                .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
        assert app.state.task_leases.recover_expired_leases() == 1
        successor = register_worker(client, "worker-checkpoint-successor")
        resumed = acquire(client, successor["id"])
        assert resumed is not None
        assert resumed["task"]["id"] == "task-demo"
        assert resumed["lease"]["recoveryCheckpointId"] == checkpoint_id
        reset = client.post("/api/simulator/reset")
        assert reset.status_code == 200
        stale_after_reset = client.post(
            "/api/tasks/task-demo/lease/complete",
            json={
                "workerId": successor["id"],
                "leaseToken": resumed["lease"]["leaseToken"],
                "result": "must not survive reset",
            },
        )
        assert stale_after_reset.status_code == 409
        with repository.session_factory() as session:
            reset_attempt = session.scalar(
                select(TaskAttemptRow).where(
                    TaskAttemptRow.lease_token == resumed["lease"]["leaseToken"]
                )
            )
            assert reset_attempt is not None
            assert reset_attempt.outcome == "simulator_reset"
