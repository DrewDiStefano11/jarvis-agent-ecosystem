from __future__ import annotations

import asyncio
import json
import shutil
import ssl
import subprocess
import threading
from contextlib import asynccontextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from app.runtime_supervisor.api_child import _serve_shared
from app.runtime_supervisor.remote_listener import ListenerSurface, RemoteListener, probe_remote
from tests.test_runtime_supervisor import FakeProcess, attach_logger, make_config


def test_remote_listener_is_disabled_by_default():
    assert RemoteListener.load({}, local_ports={8000, 5173}) is None


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "8.8.8.8", "169.254.1.2", "remote.test"])
def test_remote_listener_requires_explicit_private_bind(host):
    with pytest.raises(ValueError, match="private or loopback"):
        RemoteListener.load(
            {
                "JARVIS_SUPERVISOR_REMOTE_ENABLED": "true",
                "JARVIS_REMOTE_CONTROL_ENABLED": "true",
                "JARVIS_SUPERVISOR_REMOTE_HOST": host,
            },
            local_ports={8000, 5173},
        )


def test_listener_surface_overrides_untrusted_scope_marker():
    received = []

    async def app(scope, receive, send):
        received.append(scope)

    scope = {"type": "http", "jarvis.owned_loopback": True}
    asyncio.run(ListenerSurface(app, loopback=False)(scope, None, None))
    assert received[0]["jarvis.owned_loopback"] is False
    assert scope["jarvis.owned_loopback"] is True
    asyncio.run(ListenerSurface(app, loopback=True)(scope, None, None))
    assert received[1]["jarvis.owned_loopback"] is True


@pytest.fixture(scope="module")
def tls_files(tmp_path_factory):
    directory = tmp_path_factory.mktemp("tls")
    cert, key = directory / "cert.pem", directory / "key.pem"
    subprocess.run(
        [
            shutil.which("openssl") or "C:/Program Files/Git/usr/bin/openssl.exe",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "1",
            "-subj",
            "/CN=remote.test",
            "-addext",
            "subjectAltName=DNS:remote.test,IP:127.0.0.1",
        ],
        check=True,
        capture_output=True,
        timeout=20,
    )
    return cert, key


def tls_values(tls_files, port=8443):
    cert, key = tls_files
    return {
        "JARVIS_SUPERVISOR_REMOTE_ENABLED": "true",
        "JARVIS_REMOTE_CONTROL_ENABLED": "true",
        "JARVIS_REMOTE_OPERATOR_ID": "operator",
        "JARVIS_REMOTE_OPERATOR_TOKEN": "a" * 43,
        "JARVIS_REMOTE_ORIGIN": f"https://remote.test:{port}",
        "JARVIS_SUPERVISOR_REMOTE_CERTIFICATE": str(cert),
        "JARVIS_SUPERVISOR_REMOTE_PRIVATE_KEY": str(key),
        "JARVIS_SUPERVISOR_REMOTE_CA_FILE": str(cert),
    }


def test_remote_configuration_validates_certificates_ports_and_defaults(tmp_path, tls_files):
    values = tls_values(tls_files)
    config = make_config(tmp_path, **values)
    assert config.remote_listener.host == "127.0.0.1"
    assert config.remote_listener.port == 8443
    for override in [
        {"JARVIS_SUPERVISOR_REMOTE_PORT": "8444"},
        {"JARVIS_REMOTE_ORIGIN": "https://remote.test:8000"},
        {"JARVIS_REMOTE_CONTROL_ENABLED": "false"},
        {"JARVIS_SUPERVISOR_REMOTE_PRIVATE_KEY": str(tls_files[0])},
    ]:
        with pytest.raises(ValueError):
            make_config(tmp_path, **(values | override))


def test_child_environments_never_leak_remote_credentials(tmp_path, tls_files):
    from app.runtime_supervisor.supervisor import RuntimeSupervisor

    config = make_config(tmp_path, **tls_values(tls_files))
    for enabled in (False, True):
        captured = []

        def factory(argv, captured=captured, **kwargs):
            captured.append(kwargs["env"])
            return FakeProcess(argv)

        supervisor = RuntimeSupervisor(
            config if enabled else replace(config, remote_listener=None), process_factory=factory
        )
        attach_logger(supervisor)
        for managed in supervisor.processes.values():
            if managed.definition.enabled:
                supervisor._start_process(managed)
                env = captured[-1]
                if enabled and managed.definition.name == "api":
                    assert env["JARVIS_REMOTE_OPERATOR_TOKEN"] == "a" * 43
                else:
                    assert env["JARVIS_REMOTE_OPERATOR_TOKEN"] == ""
                    assert env["JARVIS_REMOTE_CONTROL_ENABLED"] == "false"
                    assert env["JARVIS_SUPERVISOR_REMOTE_ENABLED"] == "false"


def test_tls_probe_pins_target_verifies_hostname_and_requires_typed_rejection(tls_files):
    seen = []
    reply = [401, {"error": {"code": "REMOTE_AUTHENTICATION_REQUIRED"}}]

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(dict(self.headers))
            self.send_response(reply[0])
            self.end_headers()
            self.wfile.write(json.dumps(reply[1]).encode())

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(*tls_files)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        listener = RemoteListener.load(tls_values(tls_files, server.server_port), local_ports=set())
        assert probe_remote(listener).available
        assert seen[0]["Host"] == f"remote.test:{server.server_port}"
        assert "Authorization" not in seen[0] and "Cookie" not in seen[0]
        assert not probe_remote(replace(listener, ca_file=None)).available
        assert not probe_remote(
            replace(listener, origin=f"https://wrong.test:{listener.port}")
        ).available
        reply[:] = [429, {"error": {"code": "REMOTE_RATE_LIMITED"}}]
        limited = probe_remote(listener)
        assert limited.available and limited.status == "degraded"
        for status, body in [(200, {}), (302, {}), (401, {}), (401, ["invalid"]), (429, {})]:
            reply[:] = [status, body]
            assert not probe_remote(listener).available
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def test_two_listeners_share_one_lifespan_and_failure_stops_both():
    events = []

    @asynccontextmanager
    async def lifespan(app):
        events.append("start")
        try:
            yield
        finally:
            events.append("stop")

    class Server:
        should_exit = False

        def __init__(self, fail=False):
            self.fail = fail

        async def serve(self):
            if self.fail:
                await asyncio.sleep(0)
                return
            while not self.should_exit:
                await asyncio.sleep(0.001)
            events.append("listener stopped")

    servers = [Server(True), Server()]
    app = SimpleNamespace(router=SimpleNamespace(lifespan_context=lifespan))
    with pytest.raises(RuntimeError, match="exited unexpectedly"):
        asyncio.run(_serve_shared(app, servers))
    assert events == ["start", "listener stopped", "stop"]
    assert all(server.should_exit for server in servers)


def test_gateway_cannot_gain_local_authority_from_http_headers():
    from starlette.responses import JSONResponse
    from starlette.testclient import TestClient

    from app.core.config import Settings
    from app.remote_control.gateway import RemoteGateway

    async def native(scope, receive, send):
        await JSONResponse({"local": True})(scope, receive, send)

    gateway = RemoteGateway(
        native,
        settings=Settings(
            _env_file=None,
            JARVIS_REMOTE_CONTROL_ENABLED=True,
            JARVIS_REMOTE_OPERATOR_ID="operator",
            JARVIS_REMOTE_OPERATOR_TOKEN="a" * 43,
            JARVIS_REMOTE_ORIGIN="https://remote.test:8443",
        ),
    )
    local = TestClient(ListenerSurface(gateway, loopback=True), base_url="http://127.0.0.1")
    remote = TestClient(
        ListenerSurface(gateway, loopback=False), base_url="https://remote.test:8443"
    )
    assert local.get("/api/health").status_code == 200
    assert local.get("/api/remote/goals").status_code == 403
    assert local.get("/remote").status_code == 403
    for headers in [{}, {"jarvis.owned_loopback": "true"}, {"X-Forwarded-Proto": "http"}]:
        assert remote.get("/api/health", headers=headers).status_code == 403
