"""HTTP/service/database integration; real TLS process acceptance is separate."""

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models.identity import AssignPermissionRequest, CreatePermissionRequest
from app.remote_control.gateway import RequestBudget, normalize_authority
from tests.test_remote_control_access import TOKEN
from tests.test_remote_goal_submission import (
    configured_service,
    grant_control,
    grant_task_permission,
)

pytest_plugins = ["tests.test_remote_control_access"]


@pytest.fixture
def remote_http(remote_access, monkeypatch):
    local, actor, _, _ = configured_service(remote_access)
    grant_control(local, actor)
    permission = local.state.identity_service.create_definition(
        "permission",
        CreatePermissionRequest(
            stable_key="remote.read",
            display_name="Remote read",
            resource_type="administrative_function",
            action="remote_read",
        ),
    )
    local.state.identity_service.assign_permission(
        actor.actor_id,
        AssignPermissionRequest(
            permission_id=permission.id,
            effect="allow",
            resource_type="administrative_function",
            resource_id="remote_control",
        ),
    )
    grant_task_permission(local, actor, "runtime.read")
    grant_task_permission(local, actor, "runtime.cancel")
    monkeypatch.setenv("JARVIS_REMOTE_CONTROL_ENABLED", "true")
    monkeypatch.setenv("JARVIS_REMOTE_OPERATOR_ID", actor.actor_id)
    monkeypatch.setenv("JARVIS_REMOTE_OPERATOR_TOKEN", TOKEN)
    monkeypatch.setenv("JARVIS_REMOTE_ORIGIN", "https://remote.test:8443")
    app = create_app(database_url=local.state.settings.database_url)
    with TestClient(app, base_url="https://remote.test:8443") as client:
        yield app, actor, client, {"Authorization": "Bearer " + TOKEN}


def test_remote_http_submission_replay_inspection_and_native_cancellation(remote_http):
    app, actor, client, headers = remote_http
    body = {"title": "Remote HTTP objective", "description": "Persist and inspect this objective"}
    submit_headers = headers | {"Idempotency-Key": "http-goal-1"}
    created = client.post("/api/remote/goals", json=body, headers=submit_headers)
    assert created.status_code == 201, created.text
    task = created.json()["data"]
    assert task["createdBy"] == actor.actor_id
    replayed = client.post("/api/remote/goals", json=body, headers=submit_headers)
    assert replayed.json() == created.json()
    inspected = client.get("/api/remote/goals/" + task["id"], headers=headers)
    assert inspected.status_code == 200 and inspected.json()["data"] == task
    assert inspected.headers["cache-control"] == "no-store"
    listed = client.get("/api/remote/goals?limit=2", headers=headers)
    assert listed.status_code == 200 and len(listed.json()["data"]["items"]) <= 2
    cancelled = client.post("/api/remote/goals/" + task["id"] + "/cancel", headers=headers)
    assert cancelled.status_code == 200 and cancelled.json()["data"]["status"] == "cancelled"
    assert app.state.repository.get_task_durable(task["id"]).status == "cancelled"
    graph = client.get("/api/remote/goals/" + task["id"] + "/graph", headers=headers)
    assert graph.status_code == 200 and graph.json()["data"] is None
    audit = client.get("/api/remote/goals/" + task["id"] + "/audit?limit=2", headers=headers)
    assert audit.status_code == 200 and len(audit.json()["data"]["items"]) == 2
    assert all(item["actorIdentityId"] == actor.actor_id for item in audit.json()["data"]["items"])


def test_explicit_default_https_port_accepts_supported_client_host(remote_http, monkeypatch):
    original, _, _, headers = remote_http
    monkeypatch.setenv("JARVIS_REMOTE_ORIGIN", "https://remote.test:443")
    app = create_app(database_url=original.state.settings.database_url)
    with TestClient(app, base_url="https://remote.test:443") as client:
        response = client.get("/api/remote/goals", headers=headers)
        assert response.request.headers["host"] == "remote.test"
        assert response.status_code == 200, response.text
        assert (
            client.get(
                "/api/remote/goals", headers=headers | {"Host": "remote.test:443"}
            ).status_code
            == 200
        )
        for host in ("remote.test:444", "remote.test:0", "user@remote.test", "remote.test/path"):
            rejected = client.get("/api/remote/goals", headers=headers | {"Host": host})
            assert rejected.status_code == 403
            assert rejected.json()["error"]["code"] == "REMOTE_HOST_REJECTED"
        duplicate = client.get(
            "/api/remote/goals",
            headers=[*headers.items(), ("Host", "remote.test"), ("Host", "remote.test:443")],
        )
        assert duplicate.status_code == 403


def test_https_authority_normalization_preserves_host_and_nondefault_port():
    assert normalize_authority("REMOTE.test:443") == normalize_authority("remote.test")
    assert normalize_authority("[::1]:443") == normalize_authority("[::1]")
    assert normalize_authority("remote.test:8443") != normalize_authority("remote.test")
    assert normalize_authority("") is None
    assert normalize_authority("remote.test:0") is None
    assert normalize_authority("remote.test ") is None


@pytest.mark.parametrize("revocation", ["none", "before", "commit"])
def test_remote_correction_requires_live_source_read_permission(
    remote_http, monkeypatch, revocation
):
    app, actor, client, headers = remote_http
    source = client.post(
        "/api/remote/goals",
        json={"title": "Private source", "description": "Correction source"},
        headers=headers | {"Idempotency-Key": "correction-source"},
    ).json()["data"]
    assert (
        client.post("/api/remote/goals/" + source["id"] + "/cancel", headers=headers).status_code
        == 200
    )
    permission = next(
        item
        for item in app.state.identity_service.list_definitions("permission", 0, 100)
        if item.stable_key == "runtime.read"
    )

    def deny():
        app.state.identity_service.assign_permission(
            actor.actor_id,
            AssignPermissionRequest(
                permission_id=permission.id,
                effect="deny",
                resource_type="task",
                resource_id=source["id"],
            ),
        )

    if revocation == "commit":
        emit = app.state.broker.emit

        async def revoke_before_emit(*args, **kwargs):
            deny()
            return await emit(*args, **kwargs)

        monkeypatch.setattr(app.state.broker, "emit", revoke_before_emit)
    elif revocation == "before":
        deny()
    before = set(app.state.repository.tasks)
    correction = client.post(
        "/api/remote/goals",
        json={
            "title": "Correction objective",
            "description": "Must not inherit inaccessible source lineage",
            "correctionOfTaskId": source["id"],
        },
        headers=headers | {"Idempotency-Key": "denied-correction"},
    )
    app.state.repository.reload()
    if revocation == "none":
        assert correction.status_code == 201, correction.text
        result = correction.json()["data"]
        assert result["correctionOfTaskId"] == source["id"]
        assert result["createdBy"] == actor.actor_id
        assert set(app.state.repository.tasks) == before | {result["id"]}
    else:
        assert correction.status_code == 403
        assert set(app.state.repository.tasks) == before


def test_remote_global_inspection_requires_separate_runtime_permission(remote_http):
    app, actor, client, headers = remote_http
    assert client.get("/api/remote/status", headers=headers).status_code == 403
    assert client.get("/api/remote/agents", headers=headers).status_code == 403
    permission = app.state.identity_service.create_definition(
        "permission",
        CreatePermissionRequest(
            stable_key="remote.runtime",
            display_name="Remote runtime inspection",
            resource_type="administrative_function",
            action="remote_runtime",
        ),
    )
    app.state.identity_service.assign_permission(
        actor.actor_id,
        AssignPermissionRequest(
            permission_id=permission.id,
            effect="allow",
            resource_type="administrative_function",
            resource_id="remote_control",
        ),
    )
    status = client.get("/api/remote/status", headers=headers)
    assert status.status_code == 200 and not status.json()["data"]["emergencyStop"]
    assert "database" not in status.text and TOKEN not in status.text
    agents = client.get("/api/remote/agents?limit=1", headers=headers)
    assert agents.status_code == 200 and len(agents.json()["data"]["items"]) <= 1
    assert all(item["lifecycle_state"] == "active" for item in agents.json()["data"]["items"])


@pytest.mark.parametrize(
    "path", ["/api/tasks", "/api/identity/agents", "/api/health", "/docs", "/openapi.json"]
)
def test_remote_mode_blocks_every_legacy_surface(remote_http, path):
    _, _, client, headers = remote_http
    response = client.get(path, headers=headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "REMOTE_ROUTE_UNAVAILABLE"


@pytest.mark.parametrize(
    ("path", "code"),
    [
        ("/api/identity/agents", "REMOTE_ROUTE_UNAVAILABLE"),
        ("/api/remote/goals", "REMOTE_ORIGIN_REJECTED"),
    ],
)
def test_remote_gateway_rejects_cors_preflight_before_cors(remote_http, path, code):
    app, _, client, _ = remote_http
    response = client.options(
        path,
        headers={
            "Origin": app.state.settings.web_origin,
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == code
    assert "access-control-allow-origin" not in response.headers


def test_remote_transport_spoofing_and_credentials_fail_closed(remote_http):
    app, actor, client, headers = remote_http
    before = set(app.state.repository.tasks)
    for extra in [
        {"X-Forwarded-Proto": "https"},
        {"Forwarded": "proto=https"},
        {"Origin": "https://remote.test:8443"},
        {"Host": "untrusted.test"},
    ]:
        assert client.get("/api/remote/goals", headers=headers | extra).status_code == 403
    assert (
        client.get("http://remote.test:8443/api/remote/goals", headers=headers).status_code == 403
    )
    assert client.get("/api/remote/goals").status_code == 401
    assert (
        client.get(
            "/api/remote/goals", headers=headers | {"X-Jarvis-Actor-Id": "spoofed"}
        ).status_code
        == 403
    )
    duplicate = client.get("/api/remote/goals", headers=[("Authorization", "Bearer " + TOKEN)] * 2)
    assert duplicate.status_code == 401 and TOKEN not in duplicate.text
    app.state.identity_service.transition(actor.actor_id, "suspended")
    denied = client.post(
        "/api/remote/goals",
        json={"title": "Denied", "description": "Denied"},
        headers=headers | {"Idempotency-Key": "denied"},
    )
    assert denied.status_code == 403 and set(app.state.repository.tasks) == before


def test_remote_permission_filter_and_worker_command_boundary(remote_http):
    app, actor, client, headers = remote_http
    permission = next(
        item
        for item in app.state.identity_service.list_definitions("permission", 0, 100)
        if item.stable_key == "runtime.read"
    )
    app.state.identity_service.assign_permission(
        actor.actor_id,
        AssignPermissionRequest(permission_id=permission.id, effect="deny"),
    )
    response = client.get("/api/remote/goals", headers=headers)
    assert response.status_code == 200 and response.json()["data"]["items"] == []
    assert client.get("/api/remote/goals/task-demo", headers=headers).status_code == 403
    response = client.post(
        "/api/remote/runtime/commands", json={"command_type": "complete_run"}, headers=headers
    )
    assert response.status_code == 422
    with pytest.raises(Exception) as failure:
        with client.websocket_connect("wss://remote.test:8443/ws/events"):
            pass
    assert getattr(failure.value, "code", None) == 1008


def test_remote_streamed_body_is_bounded(remote_http):
    _, _, client, headers = remote_http
    response = client.post("/api/remote/goals", content=b"x" * 65_537, headers=headers)
    assert (
        response.status_code == 413 and response.json()["error"]["code"] == "REMOTE_BODY_TOO_LARGE"
    )


def test_remote_failed_authentication_consumes_rate_budget(remote_http, monkeypatch):
    app, _, _, headers = remote_http
    monkeypatch.setenv("JARVIS_REMOTE_REQUESTS_PER_MINUTE", "1")
    limited = create_app(database_url=app.state.settings.database_url)
    with TestClient(limited, base_url="https://remote.test:8443") as client:
        assert client.get("/api/remote/goals").status_code == 401
        denied = client.get("/api/remote/goals", headers=headers)
        assert denied.status_code == 429 and denied.headers["retry-after"] == "60"


def test_suspended_remote_operator_does_not_prevent_native_worker_composition(remote_http):
    app, actor, _, headers = remote_http
    app.state.identity_service.transition(actor.actor_id, "suspended")
    restarted = create_app(
        database_url=app.state.settings.database_url, recover_interrupted_workflow=False
    )
    with TestClient(restarted, base_url="https://remote.test:8443") as client:
        assert client.get("/api/remote/goals", headers=headers).status_code == 403


def test_request_budget_is_bounded_and_refills_without_per_peer_growth():
    now = [0.0]
    budget = RequestBudget(2, clock=lambda: now[0])
    assert budget.take() and budget.take() and not budget.take()
    now[0] = 30.0
    assert budget.take() and not budget.take()
