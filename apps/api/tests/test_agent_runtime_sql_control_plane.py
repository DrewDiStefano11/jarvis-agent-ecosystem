from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from app.main import create_app
from app.models.agent_runtime import AgentRunState, CreateAgentRunCommand, QueueAgentRunCommand
from tests.agent_runtime_testkit import make_spec, ts
from tests.test_persistence import database_url


def test_runtime_api_persists_events_audit_outbox_and_restarts(tmp_path) -> None:
    url = database_url(tmp_path / "runtime-api.db")
    create_body = {
        "command_type": "create",
        "specification": make_spec(run_id="run-sql-1").model_dump(mode="json"),
        "command_id": "cmd-create-sql",
        "expected_run_version": 0,
        "timestamp": ts(0).isoformat(),
        "actor_reference": "operator-1",
        "source_metadata": {"source": "api-test"},
    }
    with TestClient(create_app(delay_ms=1, database_url=url)) as client:
        created = client.post("/api/agent-runtime/commands", json=create_body)
        assert created.status_code == 200
        assert created.json()["data"]["snapshot"]["state"] == AgentRunState.CREATED.value
        replayed = client.post("/api/agent-runtime/commands", json=create_body)
        assert replayed.status_code == 200
        assert replayed.json()["data"]["idempotent_replay"] is True
        changed = create_body | {"actor_reference": "operator-2"}
        conflict = client.post("/api/agent-runtime/commands", json=changed)
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "command_conflict"

        listed = client.get("/api/agent-runtime/runs", params={"limit": 10})
        assert listed.status_code == 200
        assert listed.json()["data"]["total_count"] == 1
        events = client.get("/api/agent-runtime/runs/run-sql-1/events")
        assert events.status_code == 200
        assert events.json()["data"][0]["event_type"] == "run_created"
        health = client.get("/api/health").json()["data"]
        assert health["runtimePersistence"]["status"] == "healthy"
        assert client.get("/api/audit-events").status_code == 200

    with TestClient(create_app(delay_ms=1, database_url=url)) as restarted:
        snapshot = restarted.get("/api/agent-runtime/runs/run-sql-1")
        assert snapshot.status_code == 200
        assert snapshot.json()["data"]["version"] == 1
        assert (
            restarted.get("/api/agent-runtime/runs/run-sql-1/events").json()["data"][0][
                "command_id"
            ]
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
