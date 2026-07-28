"""Regressions proving every /api/agent-runtime route uses the standard envelope.

The repository-wide successful-response contract is
``{"data": ..., "meta": {"schemaVersion": "1.0"}}``. These tests cover every
runtime read and command, confirm errors are never wrapped in a success
envelope, and confirm the generated OpenAPI documents the enveloped schema.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

RUNTIME = "/api/agent-runtime"
ENVELOPE_KEYS = {"data", "meta"}


def _database_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def _specification(run_id: str = "run-envelope") -> dict[str, Any]:
    return {
        "run_id": run_id,
        "task_id": "task-1",
        "agent_id": "agent-1",
        "requested_operation": "summarize quarterly planning",
        "created_at": "2026-01-01T00:00:00Z",
        "deadline": "2026-01-01T01:00:00Z",
        "correlation_id": "corr-envelope",
        "causation_id": "cause-envelope",
        "idempotency_key": f"idem-{run_id}",
        "maximum_permitted_attempts": 3,
        "metadata": {"scope": "test"},
        "requested_capabilities": ["planning", "reporting"],
    }


def _create_command(run_id: str = "run-envelope", command_id: str = "cmd-create") -> dict[str, Any]:
    return {
        "command_type": "create",
        "command_id": command_id,
        "expected_run_version": 0,
        "timestamp": "2026-01-01T00:00:00Z",
        "actor_reference": "operator-1",
        "source_metadata": {"source": "test"},
        "specification": _specification(run_id),
    }


def _assert_envelope(payload: dict[str, Any]) -> Any:
    """Assert the exact standard successful-response shape and return ``data``."""
    assert set(payload) == ENVELOPE_KEYS
    assert payload["meta"] == {"schemaVersion": "1.0"}
    return payload["data"]


@pytest.fixture
def api(tmp_path: Path):
    app = create_app(delay_ms=1, database_url=_database_url(tmp_path / "runtime-envelope.db"))
    with TestClient(app) as client:
        response = client.post(f"{RUNTIME}/commands", json=_create_command())
        assert response.status_code == 200
        yield client


def test_command_success_is_wrapped_in_the_standard_envelope(tmp_path: Path) -> None:
    app = create_app(delay_ms=1, database_url=_database_url(tmp_path / "cmd-envelope.db"))
    with TestClient(app) as client:
        response = client.post(f"{RUNTIME}/commands", json=_create_command())
    assert response.status_code == 200
    data = _assert_envelope(response.json())
    assert data["run_id"] == "run-envelope"
    assert data["idempotent_replay"] is False
    assert data["snapshot"]["specification"]["run_id"] == "run-envelope"
    assert len(data["events"]) == 1
    assert data["events"][0]["event_type"] == "run_created"


def test_command_exact_replay_is_wrapped_and_preserves_idempotent_replay(api) -> None:
    response = api.post(f"{RUNTIME}/commands", json=_create_command())
    assert response.status_code == 200
    data = _assert_envelope(response.json())
    assert data["idempotent_replay"] is True
    assert data["run_id"] == "run-envelope"
    assert data["snapshot"]["version"] == 1


def test_command_response_has_no_domain_fields_at_the_top_level(api) -> None:
    payload = api.post(f"{RUNTIME}/commands", json=_create_command()).json()
    assert set(payload) == ENVELOPE_KEYS
    for leaked in ("run_id", "snapshot", "events", "idempotent_replay", "recovery_plan"):
        assert leaked not in payload


def test_list_runs_is_wrapped_and_keeps_pagination_inside_data(api) -> None:
    response = api.get(f"{RUNTIME}/runs")
    assert response.status_code == 200
    data = _assert_envelope(response.json())
    assert set(data) == {"items", "offset", "limit", "next_offset", "total_count"}
    assert data["total_count"] == 1
    assert data["items"][0]["specification"]["run_id"] == "run-envelope"
    payload = response.json()
    for leaked in ("items", "offset", "limit", "next_offset", "total_count"):
        assert leaked not in payload


def test_get_run_is_wrapped_and_returns_the_snapshot(api) -> None:
    response = api.get(f"{RUNTIME}/runs/run-envelope")
    assert response.status_code == 200
    data = _assert_envelope(response.json())
    assert data["specification"]["run_id"] == "run-envelope"
    assert data["specification"]["correlation_id"] == "corr-envelope"
    assert data["state"] == "created"


def test_events_history_is_wrapped_and_correctly_serialized(api) -> None:
    response = api.get(f"{RUNTIME}/runs/run-envelope/events")
    assert response.status_code == 200
    data = _assert_envelope(response.json())
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["run_id"] == "run-envelope"
    assert data[0]["event_type"] == "run_created"
    assert data[0]["sequence_number"] == 1
    assert data[0]["correlation_id"] == "corr-envelope"


def test_attempt_history_is_wrapped_and_correctly_serialized(api) -> None:
    response = api.get(f"{RUNTIME}/runs/run-envelope/attempts")
    assert response.status_code == 200
    data = _assert_envelope(response.json())
    assert data == []


def test_checkpoint_history_is_wrapped_and_correctly_serialized(api) -> None:
    response = api.get(f"{RUNTIME}/runs/run-envelope/checkpoints")
    assert response.status_code == 200
    data = _assert_envelope(response.json())
    assert data == []


def test_lineage_resolution_is_wrapped_and_correctly_serialized(api) -> None:
    response = api.get(f"{RUNTIME}/runs/run-envelope/lineage")
    assert response.status_code == 200
    data = _assert_envelope(response.json())
    assert data["run_id"] == "run-envelope"
    assert set(data) == {"run_id", "entries", "missing_parent_id", "truncated", "depth_limit"}
    # A run without a parent resolves to an empty ancestor chain.
    assert data["entries"] == []
    assert data["missing_parent_id"] is None
    assert data["truncated"] is False


def test_populated_attempt_and_checkpoint_histories_stay_inside_data(tmp_path: Path) -> None:
    app = create_app(delay_ms=1, database_url=_database_url(tmp_path / "runtime-populated.db"))
    with TestClient(app) as client:
        assert (
            client.post(f"{RUNTIME}/commands", json=_create_command(run_id="run-full")).status_code
            == 200
        )
        base = {
            "run_id": "run-full",
            "timestamp": "2026-01-01T00:00:10Z",
            "actor_reference": "worker-1",
            "source_metadata": {"source": "test"},
        }
        assert (
            client.post(
                f"{RUNTIME}/commands",
                json={
                    **base,
                    "command_type": "queue",
                    "command_id": "c-q",
                    "expected_run_version": 1,
                },
            ).status_code
            == 200
        )
        assert (
            client.post(
                f"{RUNTIME}/commands",
                json={
                    **base,
                    "command_type": "claim",
                    "command_id": "c-c",
                    "expected_run_version": 2,
                    "executor_reference": "worker-1",
                },
            ).status_code
            == 200
        )
        assert (
            client.post(
                f"{RUNTIME}/commands",
                json={
                    **base,
                    "command_type": "begin_attempt",
                    "command_id": "c-b",
                    "expected_run_version": 3,
                    "executor_reference": "worker-1",
                },
            ).status_code
            == 200
        )
        attempts = _assert_envelope(client.get(f"{RUNTIME}/runs/run-full/attempts").json())
        assert len(attempts) == 1
        assert attempts[0]["attempt_number"] == 1
        events = _assert_envelope(client.get(f"{RUNTIME}/runs/run-full/events").json())
        assert [event["sequence_number"] for event in events] == [1, 2, 3, 4, 5]


@pytest.mark.parametrize(
    "path",
    [
        "/runs/missing-run",
        "/runs/missing-run/events",
        "/runs/missing-run/attempts",
        "/runs/missing-run/checkpoints",
        "/runs/missing-run/lineage",
    ],
)
def test_not_found_errors_are_not_returned_inside_a_success_envelope(api, path: str) -> None:
    response = api.get(f"{RUNTIME}{path}")
    assert response.status_code == 404
    payload = response.json()
    assert set(payload) == {"error"}
    assert payload["error"]["code"] == "run_not_found"
    assert "data" not in payload
    assert "meta" not in payload


def test_runtime_domain_conflicts_remain_errors_with_stable_codes(api) -> None:
    response = api.post(
        f"{RUNTIME}/commands",
        json=_create_command(command_id="cmd-duplicate"),
    )
    assert response.status_code == 409
    payload = response.json()
    assert set(payload) == {"error"}
    assert payload["error"]["code"] == "run_already_exists"


def test_validation_errors_retain_the_established_error_format(api) -> None:
    response = api.post(f"{RUNTIME}/commands", json={"command_type": "create"})
    assert response.status_code == 422
    payload = response.json()
    assert "detail" in payload
    assert "meta" not in payload


def test_openapi_declares_the_enveloped_response_schema(api) -> None:
    schema = api.get("/openapi.json").json()
    components = schema["components"]["schemas"]
    for path, method in (
        (f"{RUNTIME}/runs", "get"),
        (f"{RUNTIME}/runs/{{run_id}}", "get"),
        (f"{RUNTIME}/runs/{{run_id}}/events", "get"),
        (f"{RUNTIME}/runs/{{run_id}}/attempts", "get"),
        (f"{RUNTIME}/runs/{{run_id}}/checkpoints", "get"),
        (f"{RUNTIME}/runs/{{run_id}}/lineage", "get"),
        (f"{RUNTIME}/commands", "post"),
    ):
        operation = schema["paths"][path][method]
        ref = operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        model = components[ref.rsplit("/", 1)[-1]]
        assert set(model["properties"]) == ENVELOPE_KEYS, path
        assert "data" in model["properties"], path
        assert "meta" in model["properties"], path


def test_openapi_envelope_data_still_references_the_inner_domain_models(api) -> None:
    schema = api.get("/openapi.json").json()
    components = schema["components"]["schemas"]
    ref = schema["paths"][f"{RUNTIME}/runs/{{run_id}}"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["$ref"]
    model = components[ref.rsplit("/", 1)[-1]]
    assert "AgentRunSnapshot" in str(model["properties"]["data"])


def test_existing_non_runtime_endpoints_remain_unchanged(api) -> None:
    for path in ("/api/health", "/api/system/status", "/api/agents"):
        payload = api.get(path).json()
        assert set(payload) == ENVELOPE_KEYS, path
        assert payload["meta"] == {"schemaVersion": "1.0"}, path
