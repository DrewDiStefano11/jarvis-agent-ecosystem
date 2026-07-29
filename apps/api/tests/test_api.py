from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models.domain import SystemStatus
from app.models.manifest import load_manifest


@pytest.fixture(autouse=True)
def isolated_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = (tmp_path / "jarvis-test.db").as_posix()
    monkeypatch.setenv("JARVIS_DATABASE_URL", f"sqlite:///{database}")


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
    assert revision["default"] == "20260729_04"


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
            assert snapshot["sequenceNumber"] == 1
            api.post("/api/simulator/failure", json={"scenario": "scout_research_failure"})
            failure = socket.receive_json()
            assert failure["eventType"] == "error.simulated"
            assert failure["sequenceNumber"] == 2


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
