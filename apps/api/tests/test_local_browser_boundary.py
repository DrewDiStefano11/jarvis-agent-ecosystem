"""Local HTTP/WS security boundaries over ASGI; no browser or model calls."""

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import create_app


@pytest.fixture
def local_app(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_MODEL_EXECUTION_MODE", "disabled")
    monkeypatch.setenv("JARVIS_AUTONOMOUS_WORKER_ENABLED", "false")
    monkeypatch.setenv("WEB_ORIGIN", "http://localhost:5173")
    app = create_app(database_url=f"sqlite:///{(tmp_path / 'boundary.db').as_posix()}")
    calls = []

    def probe():
        calls.append(True)
        return {"ok": True}

    app.add_api_route("/api/boundary-probe", probe, methods=["GET", "POST"])
    yield app, calls
    app.state.engine.dispose()


@pytest.mark.parametrize("origin", ["http://evil.example", "null"])
def test_cross_origin_simple_post_never_reaches_handler(local_app, origin):
    app, calls = local_app
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        response = client.post("/api/boundary-probe", headers={"Origin": origin})
    assert response.status_code == 403 and calls == []


def test_nonloopback_host_cannot_read_local_api_without_an_origin(local_app):
    app, calls = local_app
    with TestClient(app, base_url="http://rebind.example:8000") as client:
        response = client.get("/api/boundary-probe", headers={"Sec-Fetch-Site": "same-origin"})
    assert response.status_code == 403 and calls == []


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "[::1]"])
@pytest.mark.parametrize("origin", [None, "http://localhost:5173"])
def test_configured_local_ui_and_native_cli_remain_available(local_app, host, origin):
    app, calls = local_app
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        headers = {"Host": f"{host}:8000"}
        if origin:
            headers["Origin"] = origin
        response = client.post("/api/boundary-probe", headers=headers)
    assert response.status_code == 200 and calls == [True]


def test_cross_origin_websocket_cannot_receive_a_snapshot(local_app):
    app, _ = local_app
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        with pytest.raises(WebSocketDisconnect) as refused:
            with client.websocket_connect("/ws/events", headers={"Origin": "http://evil.example"}) as socket:
                socket.receive_json()
    assert refused.value.code == 1008


def test_duplicate_origins_cannot_smuggle_browser_authority(local_app):
    app, calls = local_app
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        response = client.post("/api/boundary-probe", headers=[
            ("Origin", "http://localhost:5173"), ("Origin", "http://evil.example")
        ])
    assert response.status_code == 403 and calls == []
