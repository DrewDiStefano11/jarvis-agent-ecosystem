from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from sqlalchemy import update

from app.db.models import OutboxEventRow
from app.main import create_app
from app.models.agent_runtime import AgentRunState, CreateAgentRunCommand, QueueAgentRunCommand
from app.models.identity import AssignPermissionRequest, CreateAgentRequest, CreatePermissionRequest
from tests.agent_runtime_testkit import make_spec, ts
from tests.test_persistence import database_url

RUNTIME_PERMISSION_KEYS = (
    "runtime.read",
    "runtime.create",
    "runtime.queue",
    "runtime.execute",
    "runtime.pause",
    "runtime.cancel",
    "runtime.checkpoint",
    "runtime.complete",
    "runtime.recover",
)


def grant_runtime_permissions(app, actor_key: str, task_id: str = "task-1") -> str:
    identity = app.state.identity_service
    actor = identity.create_agent(
        CreateAgentRequest(
            stable_key=actor_key,
            display_name=actor_key,
            agent_type="coordinator",
        )
    )
    identity.transition(actor.id, "active")
    for index, key in enumerate(RUNTIME_PERMISSION_KEYS):
        permission = identity.create_definition(
            "permission",
            CreatePermissionRequest(
                stable_key=key,
                display_name=key,
                resource_type="task",
                action=f"runtime_{index}",
            ),
        )
        identity.assign_permission(
            actor.id,
            AssignPermissionRequest(
                permission_id=permission.id,
                effect="allow",
                resource_type="task",
                resource_id=task_id,
            ),
        )
    return actor.id


def test_runtime_api_persists_events_audit_outbox_and_restarts(tmp_path) -> None:
    url = database_url(tmp_path / "runtime-api.db")
    app = create_app(delay_ms=1, database_url=url)
    with TestClient(app) as client:
        actor_id = grant_runtime_permissions(app, "runtime-actor")
        headers = {"X-Jarvis-Actor-Id": actor_id}
        create_body = {
            "command_type": "create",
            "specification": make_spec(run_id="run-sql-1").model_dump(mode="json"),
            "command_id": "cmd-create-sql",
            "expected_run_version": 0,
            "timestamp": ts(0).isoformat(),
            "actor_reference": actor_id,
            "source_metadata": {"source": "api-test"},
        }
        created = client.post("/api/agent-runtime/commands", json=create_body, headers=headers)
        assert created.status_code == 200
        assert created.json()["data"]["snapshot"]["state"] == AgentRunState.CREATED.value
        replayed = client.post("/api/agent-runtime/commands", json=create_body, headers=headers)
        assert replayed.status_code == 200
        assert replayed.json()["data"]["idempotent_replay"] is True
        changed = create_body | {"actor_reference": "operator-2"}
        conflict = client.post("/api/agent-runtime/commands", json=changed, headers=headers)
        assert conflict.status_code == 400
        assert conflict.json()["error"]["code"] == "runtime_actor_mismatch"

        queue_body = {
            "command_type": "queue",
            "run_id": "run-sql-1",
            "command_id": "cmd-queue-sql",
            "expected_run_version": 1,
            "timestamp": ts(1).isoformat(),
            "actor_reference": actor_id,
            "detail": "Queued by API test",
        }
        queued = client.post("/api/agent-runtime/commands", json=queue_body, headers=headers)
        assert queued.status_code == 200
        assert queued.json()["data"]["snapshot"]["state"] == AgentRunState.QUEUED.value
        replayed_queue = client.post(
            "/api/agent-runtime/commands", json=queue_body, headers=headers
        )
        assert replayed_queue.status_code == 200
        assert replayed_queue.json()["data"]["idempotent_replay"] is True
        changed_queue = queue_body | {"detail": "Changed"}
        command_conflict = client.post(
            "/api/agent-runtime/commands", json=changed_queue, headers=headers
        )
        assert command_conflict.status_code == 409
        assert command_conflict.json()["error"]["code"] == "command_conflict"

        listed = client.get("/api/agent-runtime/runs", params={"limit": 10}, headers=headers)
        assert listed.status_code == 200
        assert listed.json()["data"]["total_count"] == 1
        events = client.get("/api/agent-runtime/runs/run-sql-1/events", headers=headers)
        assert events.status_code == 200
        assert events.json()["data"][0]["event_type"] == "run_created"
        health = client.get("/api/health").json()["data"]
        assert health["runtimePersistence"]["status"] == "healthy"
        with app.state.repository.session_factory() as session, session.begin():
            session.execute(
                update(OutboxEventRow)
                .where(OutboxEventRow.event_type.like("agent_runtime.%"))
                .values(
                    status="failed", publish_attempt_count=app.state.repository.outbox_max_attempts
                )
            )
        degraded = client.get("/api/health").json()["data"]
        assert degraded["status"] == "degraded"
        assert degraded["runtimePersistence"]["reasonCode"] == "runtime_outbox_exhausted"
        audit_rows = [
            row
            for row in client.get("/api/audit-events").json()["data"]
            if row["eventType"] == "agent_runtime.command"
        ]
        assert len(audit_rows) == 2
        create_audit = next(row for row in audit_rows if row["payload"]["commandType"] == "create")
        queue_audit = next(row for row in audit_rows if row["payload"]["commandType"] == "queue")
        assert create_audit["actorAgentId"] == actor_id
        assert create_audit["payload"]["verifiedActorId"] == actor_id
        assert create_audit["payload"]["targetAgentId"] == "agent-1"
        assert queue_audit["previousState"] == "created"
        assert queue_audit["newState"] == "queued"

    with TestClient(create_app(delay_ms=1, database_url=url)) as restarted:
        snapshot = restarted.get("/api/agent-runtime/runs/run-sql-1", headers=headers)
        assert snapshot.status_code == 200
        assert snapshot.json()["data"]["version"] == 2
        assert (
            restarted.get("/api/agent-runtime/runs/run-sql-1/events", headers=headers).json()[
                "data"
            ][0]["command_id"]
            == "cmd-create-sql"
        )


def test_runtime_sql_concurrent_identical_command_replays_once(tmp_path) -> None:
    url = database_url(tmp_path / "runtime-concurrent.db")
    app = create_app(delay_ms=1, database_url=url)
    with TestClient(app):
        service = app.state.agent_runtime_service
        spec = make_spec(run_id="run-sql-2")
        service.create_run(
            CreateAgentRunCommand(
                specification=spec,
                command_id="cmd-create-sql-2",
                expected_run_version=0,
                timestamp=ts(0),
                actor_reference="operator-1",
            )
        )
        command = QueueAgentRunCommand(
            run_id="run-sql-2",
            command_id="cmd-queue-once",
            expected_run_version=1,
            timestamp=ts(1),
            actor_reference="scheduler-1",
        )

        def submit() -> bool:
            return service.queue_run(command).idempotent_replay

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = sorted(executor.map(lambda _: submit(), range(2)))

        assert results == [False, True]
        assert app.state.agent_runtime_repository.load_run("run-sql-2").version == 2
        assert app.state.agent_runtime_repository.integrity_check("run-sql-2") is True
