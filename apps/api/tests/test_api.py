from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select

from app.core.config import Settings
from app.db.models import OutboxEventRow, SystemStateRow, TaskRow
from app.main import create_app
from app.models.domain import SystemStatus
from app.models.manifest import load_manifest


@pytest.fixture(autouse=True)
def isolated_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = (tmp_path / "jarvis-test.db").as_posix()
    monkeypatch.setenv("JARVIS_DATABASE_URL", f"sqlite:///{database}")
    monkeypatch.setenv("JARVIS_AUTONOMOUS_WORKER_ENABLED", "false")
    monkeypatch.setenv("JARVIS_WEB_ORIGIN", "http://localhost:5173")


def client() -> TestClient:
    return TestClient(create_app(delay_ms=1))


def test_health_status_and_lists() -> None:
    with client() as api:
        assert api.get("/api/health").status_code == 200
        assert api.get("/api/system/status").json()["data"]["status"] == "healthy"
        assert len(api.get("/api/agents").json()["data"]) == 5
        assert len(api.get("/api/departments").json()["data"]) == 4
        assert len(api.get("/api/tasks").json()["data"]) == 4


def test_system_status_contract_advertises_current_database_revision() -> None:
    revision = SystemStatus.model_json_schema()["properties"]["databaseRevision"]
    assert revision["default"] == "20260905_08"


@pytest.mark.parametrize("configured_actor", ["", "local-worker-actor"])
def test_status_exposes_configured_worker_identity_across_health_states(
    configured_actor: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JARVIS_AUTONOMOUS_WORKER_ACTOR_ID", configured_actor)
    app = create_app(delay_ms=1)
    with TestClient(app) as api:
        for health_state in [(True, True), (True, False), (False, False)]:
            app.state.repository.health_probe = lambda _revision, state=health_state: state
            for endpoint in ["/api/health", "/api/system/status"]:
                worker = api.get(endpoint).json()["data"]["autonomousWorker"]
                assert worker["workerActorId"] == (configured_actor or None)
        schema = api.get("/openapi.json").json()["components"]["schemas"]
        assert "workerActorId" in schema["AutonomousWorkerStatus"]["properties"]


def test_domain_event_sequence_refreshes_the_shared_committed_cursor() -> None:
    app = create_app(delay_ms=1)
    with TestClient(app) as api:
        with app.state.repository.session_factory.begin() as session:
            state = session.get(SystemStateRow, 1)
            assert state is not None
            state.current_sequence_number = 40

        created = api.post(
            "/api/tasks",
            json={"title": "Sequence task", "description": "Use the committed cursor"},
        )
        assert created.status_code == 201
        assert app.state.repository.current_event_cursor()[1] == 41


def test_concurrent_processes_allocate_event_sequences_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = (tmp_path / "shared-events.db").as_posix()
    url = f"sqlite:///{database}"
    monkeypatch.setenv("JARVIS_DATABASE_URL", url)
    first = create_app(delay_ms=1)
    second = create_app(delay_ms=1)
    barrier = Barrier(2)

    def emit(app, suffix: str):
        barrier.wait()
        return asyncio.run(
            app.state.broker.emit(
                f"test.concurrent.{suffix}",
                {"source": suffix},
                correlation_id=f"concurrent-{suffix}",
            )
        )

    starting_sequence = first.state.repository.current_event_cursor()[1]
    with ThreadPoolExecutor(max_workers=2) as pool:
        emitted = list(pool.map(lambda pair: emit(*pair), [(first, "first"), (second, "second")]))

    sequences = sorted(event.sequenceNumber for event in emitted)
    assert sequences == [starting_sequence + 1, starting_sequence + 2]
    with first.state.repository.session_factory() as session:
        stored = list(
            session.scalars(
                select(OutboxEventRow.sequence_number)
                .where(OutboxEventRow.event_type.like("test.concurrent.%"))
                .order_by(OutboxEventRow.sequence_number)
            )
        )
    assert stored == sequences


def test_task_reads_and_snapshots_observe_sidecar_database_updates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = (tmp_path / "sidecar-visibility.db").as_posix()
    url = f"sqlite:///{database}"
    monkeypatch.setenv("JARVIS_DATABASE_URL", url)
    api_app = create_app(delay_ms=1)
    sidecar_app = create_app(
        delay_ms=1,
        recover_interrupted_workflow=False,
    )

    with TestClient(api_app) as api:
        task_id = api.get("/api/tasks").json()["data"][0]["id"]
        with sidecar_app.state.repository.session_factory.begin() as session:
            row = session.get(TaskRow, task_id)
            assert row is not None
            payload = dict(row.payload)
            payload.update({"status": "completed", "statusMessage": "Completed by sidecar"})
            row.status = "completed"
            row.payload = payload

        assert api.get(f"/api/tasks/{task_id}").json()["data"]["status"] == "completed"
        listed = api.get("/api/tasks").json()["data"]
        assert next(item for item in listed if item["id"] == task_id)["status"] == "completed"
        snapshot = api_app.state.repository.snapshot()
        assert next(item for item in snapshot["tasks"] if item.id == task_id).status == "completed"


def test_unknown_ids_are_structured() -> None:
    with client() as api:
        response = api.get("/api/agents/unknown")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "AGENT_NOT_FOUND"
        assert api.get("/api/tasks/unknown").status_code == 404
        assert api.post("/api/approvals/unknown/approve", json={}).status_code == 404


def test_task_create_and_retry() -> None:
    with client() as api:
        created = api.post(
            "/api/tasks",
            json={
                "title": "Fixture task",
                "description": "Use only fixture data",
                "priority": "high",
            },
        )
        assert created.status_code == 201
        assert created.json()["data"]["status"] == "queued"
        retried = api.post("/api/tasks/task-failed/retry")
        assert retried.status_code == 200
        assert retried.json()["data"]["retryCount"] == 1


def test_mutation_contracts_reject_unknown_fields_and_oversized_idempotency_keys() -> None:
    with client() as api:
        unknown = api.post(
            "/api/tasks",
            json={
                "title": "Fixture task",
                "description": "Use only fixture data",
                "unexpected": "ignored-before-hardening",
            },
        )
        assert unknown.status_code == 422
        assert unknown.json()["error"]["code"] == "REQUEST_VALIDATION_ERROR"

        oversized = api.post(
            "/api/tasks",
            headers={"Idempotency-Key": "x" * 201},
            json={"title": "Fixture task", "description": "Use only fixture data"},
        )
        assert oversized.status_code == 422
        body = oversized.json()
        assert body["error"]["code"] == "REQUEST_VALIDATION_ERROR"
        assert "x" * 201 not in str(body)


def test_approval_rules_and_duplicate() -> None:
    with client() as api:
        approved = api.post(
            "/api/approvals/approval-pending/approve", json={"decisionNote": "Reviewed"}
        )
        assert approved.status_code == 200
        assert approved.json()["data"]["status"] == "approved"
        duplicate = api.post("/api/approvals/approval-pending/approve", json={})
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["code"] == "APPROVAL_ALREADY_PROCESSED"
        expired = api.post("/api/approvals/approval-expired/approve", json={})
        assert expired.status_code == 409
        black = api.post("/api/approvals/approval-black/approve", json={})
        assert black.status_code == 403


def test_rejection_and_emergency_stop_rules() -> None:
    with client() as api:
        rejected = api.post(
            "/api/approvals/approval-pending/reject", json={"decisionNote": "Needs work"}
        )
        assert rejected.json()["data"]["status"] == "rejected"
    with client() as api:
        assert api.post("/api/system/emergency-stop").json()["data"]["emergencyStop"] is True
        blocked = api.post("/api/approvals/approval-pending/approve", json={})
        assert blocked.status_code == 423
        assert api.post("/api/system/resume").json()["data"]["emergencyStop"] is False


def test_temporary_agent_and_invalid_transition() -> None:
    with client() as api:
        created = api.post(
            "/api/agents/temporary",
            json={"name": "Compass", "role": "Route analyst", "departmentId": "research"},
        )
        assert created.status_code == 201
        assert created.json()["data"]["isTemporary"] is True
        invalid = api.post("/api/simulator/failure", json={"scenario": "invalid_transition"})
        assert invalid.status_code == 409
        assert invalid.json()["error"]["code"] == "INVALID_STATE_TRANSITION"


def test_simulator_start_pause_resume_reset_and_duplicate_start() -> None:
    with client() as api:
        assert api.post("/api/simulator/start").status_code == 200
        assert api.post("/api/simulator/start").status_code == 409
        assert api.post("/api/simulator/pause").status_code == 200
        paused_start = api.post("/api/simulator/start")
        assert paused_start.status_code == 409
        assert paused_start.json()["error"]["code"] == "SIMULATOR_RESUME_OR_RESET_REQUIRED"
        assert api.post("/api/simulator/resume").status_code == 200
        reset = api.post("/api/simulator/reset")
        assert reset.json()["data"]["currentStep"] == 0
        assert len(api.get("/api/tasks").json()["data"]) == 4


def test_failure_and_websocket_sequence() -> None:
    with client() as api:
        with api.websocket_connect("/ws/events") as socket:
            snapshot = socket.receive_json()
            assert snapshot["eventType"] == "system.snapshot"
            assert snapshot["sequenceNumber"] == 0
            api.post("/api/simulator/failure", json={"scenario": "scout_research_failure"})
            failure = socket.receive_json()
            assert failure["eventType"] == "error.simulated"
            assert failure["sequenceNumber"] == 1


def test_websocket_resync_is_requester_only_and_does_not_create_outbox_state() -> None:
    app = create_app(delay_ms=1)
    with TestClient(app) as api:
        with api.websocket_connect("/ws/events") as socket:
            initial = socket.receive_json()
            session_id, sequence = app.state.repository.current_event_cursor()
            outbox_before = app.state.repository.outbox_pending_count()
            assert initial["eventSessionId"] == session_id
            assert initial["sequenceNumber"] == sequence

            socket.send_text("resync")
            resync = socket.receive_json()

            assert resync["eventType"] == "system.snapshot"
            assert resync["eventSessionId"] == session_id
            assert resync["sequenceNumber"] == sequence
            assert app.state.repository.current_event_cursor() == (session_id, sequence)
            assert app.state.repository.outbox_pending_count() == outbox_before


def test_websocket_snapshot_captures_cursor_before_reading_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(delay_ms=1)
    with TestClient(app) as api:
        cursor_captured = False
        original_cursor = app.state.repository.current_event_cursor
        original_snapshot = app.state.repository.snapshot

        def current_event_cursor():
            nonlocal cursor_captured
            cursor_captured = True
            return original_cursor()

        def snapshot():
            assert cursor_captured
            return original_snapshot()

        monkeypatch.setattr(app.state.repository, "current_event_cursor", current_event_cursor)
        monkeypatch.setattr(app.state.repository, "snapshot", snapshot)

        with api.websocket_connect("/ws/events") as socket:
            assert socket.receive_json()["eventType"] == "system.snapshot"


def test_demo_completes_deterministically() -> None:
    app = create_app(delay_ms=1)
    with TestClient(app) as api:
        api.post("/api/simulator/start")
        for _ in range(100):
            if api.get("/api/system/status").json()["data"]["simulator"]["state"] == "completed":
                break
            asyncio.run(asyncio.sleep(0.005))
        task = api.get("/api/tasks/task-demo").json()["data"]
        assert task["status"] == "completed"
        assert len(task["childTaskIds"]) == 2
        assert "revised" in task["result"]
        assert len(api.get("/api/audit-events").json()["data"]) >= 26


def test_all_manifests_validate() -> None:
    root = Path(__file__).resolve().parents[3]
    manifests = sorted((root / "agents" / "manifests").glob("*.yaml"))
    assert len(manifests) == 5
    assert {load_manifest(path).metadata.id for path in manifests} == {
        "jarvis",
        "atlas",
        "scout",
        "archive",
        "sentinel",
    }


def test_runtime_actor_header_is_allowed_by_cors_preflight() -> None:
    app = create_app(delay_ms=1)
    with TestClient(app) as client:
        response = client.options(
            "/api/agent-runtime/commands",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "X-Jarvis-Actor-Id, Content-Type, Idempotency-Key",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
        assert response.headers["access-control-allow-credentials"] == "true"
        assert "POST" in response.headers["access-control-allow-methods"]
        allowed_headers = response.headers["access-control-allow-headers"].lower()
        assert "x-jarvis-actor-id" in allowed_headers
        assert "content-type" in allowed_headers
        assert "idempotency-key" in allowed_headers

        get_response = client.options(
            "/api/agent-runtime/runs",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "X-Jarvis-Actor-Id",
            },
        )
        assert get_response.status_code == 200
        assert "GET" in get_response.headers["access-control-allow-methods"]

        disallowed = client.options(
            "/api/agent-runtime/commands",
            headers={
                "Origin": "http://evil.example",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "X-Jarvis-Actor-Id",
            },
        )
        assert "access-control-allow-origin" not in disallowed.headers

        unrelated = client.options(
            "/api/agent-runtime/commands",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "X-Unrelated-Header",
            },
        )
        assert unrelated.status_code == 400


def test_control_plane_rejects_non_loopback_peers() -> None:
    app = create_app(delay_ms=1)
    with TestClient(app, client=("203.0.113.20", 55000)) as remote:
        response = remote.post(
            "/api/identity/agents",
            json={
                "stable_key": "remote-admin",
                "display_name": "Remote admin",
                "agent_type": "system",
            },
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "LOCAL_CONTROL_PLANE_ONLY"
        assert remote.get("/api/health").status_code == 403


def test_web_origin_must_remain_structurally_loopback() -> None:
    with pytest.raises(ValidationError, match="WEB_ORIGIN"):
        Settings(WEB_ORIGIN="https://office.example.com")
    with pytest.raises(ValidationError, match="WEB_ORIGIN"):
        Settings(WEB_ORIGIN="http://user:password@localhost:5173")
    assert str(Settings(WEB_ORIGIN="https://[::1]:5173").web_origin) == "https://[::1]:5173"


def test_system_status_matches_health_for_runtime_component_states() -> None:
    app = create_app(delay_ms=1)
    scenarios = [
        {"configured": True, "nonterminalRunCount": 0},
        {
            "configured": True,
            "nonterminalRunCount": 1,
            "status": "degraded",
            "reasonCode": "runtime_projection_corrupt",
        },
        {
            "configured": False,
            "nonterminalRunCount": 0,
            "status": "unavailable",
            "reasonCode": "runtime_persistence_unavailable",
        },
        {"not": "a valid response"},
    ]
    with TestClient(app) as client:
        for result in scenarios:
            app.state.agent_runtime_repository.health_status = lambda result=result: result
            health = client.get("/api/health").json()["data"]
            system_status = client.get("/api/system/status").json()["data"]
            assert system_status["status"] == health["status"]
            if result == scenarios[0]:
                assert system_status["status"] == "healthy"
            else:
                assert system_status["status"] == "degraded"


def test_system_status_runtime_query_failure_is_bounded_and_matches_health() -> None:
    app = create_app(delay_ms=1)

    def fail_runtime_health() -> dict:
        raise RuntimeError("SELECT secret from /tmp/runtime.db")

    app.state.agent_runtime_repository.health_status = fail_runtime_health
    with TestClient(app) as client:
        health = client.get("/api/health").json()["data"]
        system_status = client.get("/api/system/status").json()["data"]
        assert health["status"] == system_status["status"] == "degraded"
        encoded = str(system_status) + str(health)
        assert "SELECT" not in encoded
        assert "/tmp/runtime.db" not in encoded
        assert "secret" not in encoded
        assert "Traceback" not in encoded


def test_system_status_does_not_query_runtime_when_database_unreachable_or_schema_stale() -> None:
    app = create_app(delay_ms=1)
    calls = {"count": 0}

    def fail_if_touched() -> dict:
        calls["count"] += 1
        raise AssertionError("runtime health must not be queried")

    app.state.agent_runtime_repository.health_status = fail_if_touched
    with TestClient(app) as client:
        app.state.repository.health_probe = lambda _revision: (False, False)
        health = client.get("/api/health").json()["data"]
        system_status = client.get("/api/system/status").json()["data"]
        assert health["status"] == system_status["status"] == "degraded"
        app.state.repository.health_probe = lambda _revision: (True, False)
        health = client.get("/api/health").json()["data"]
        system_status = client.get("/api/system/status").json()["data"]
        assert health["status"] == system_status["status"] == "degraded"
    assert calls["count"] == 0


def test_autonomous_worker_max_concurrency_config(monkeypatch: pytest.MonkeyPatch) -> None:
    import pydantic

    from app.core.config import Settings

    monkeypatch.setenv("JARVIS_AUTONOMOUS_WORKER_MAX_CONCURRENCY", "1")
    s = Settings()
    assert s.autonomous_worker_max_concurrency == 1

    monkeypatch.setenv("JARVIS_AUTONOMOUS_WORKER_MAX_CONCURRENCY", "2")
    with pytest.raises(pydantic.ValidationError):
        Settings()
