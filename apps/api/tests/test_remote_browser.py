"""Browser transport retains the existing dedicated remote/native authority."""

import pytest

from tests.test_remote_control_access import TOKEN

pytest_plugins = ["tests.test_remote_http"]
ORIGIN = "https://remote.test:8443"


@pytest.fixture
def browser_http(remote_http, tmp_path, monkeypatch):
    assets = tmp_path / "web"
    (assets / "assets").mkdir(parents=True)
    (assets / "remote.html").write_text(
        '<!doctype html><html><body><div id="root"></div></body></html>', encoding="utf-8"
    )
    (assets / "assets" / "remote-abcdef.js").write_text("/* trusted build */", encoding="utf-8")
    monkeypatch.setenv("JARVIS_REMOTE_BROWSER_ENABLED", "true")
    monkeypatch.setenv("JARVIS_REMOTE_BROWSER_ASSETS", str(assets))
    from fastapi.testclient import TestClient

    from app.main import create_app

    previous, actor, _, headers = remote_http
    app = create_app(database_url=previous.state.settings.database_url)
    with TestClient(app, base_url=ORIGIN) as client:
        yield app, actor, client, headers


def login(client):
    response = client.post(
        "/api/remote/session", headers={"Origin": ORIGIN, "Authorization": "Bearer " + TOKEN}
    )
    assert response.status_code == 200, response.text
    return response


def test_shared_loopback_retains_local_peer_and_office_reconciliation(browser_http, monkeypatch):
    from fastapi.testclient import TestClient

    from app.runtime_supervisor.remote_listener import ListenerSurface

    app, actor, _, _ = browser_http
    reconciled = []
    monkeypatch.setattr(app.state.office_service, "reconcile", lambda: reconciled.append(True))
    local = TestClient(
        ListenerSurface(app, loopback=True),
        base_url="http://127.0.0.1",
        client=("127.0.0.1", 50001),
    )
    response = local.patch(
        f"/api/identity/agents/{actor.actor_id}", json={"display_name": "Supervised operator"}
    )
    assert response.status_code == 200, response.text
    assert reconciled == [True]
    untrusted = TestClient(
        ListenerSurface(app, loopback=True),
        base_url="http://127.0.0.1",
        client=("192.168.1.2", 50001),
    )
    assert untrusted.get("/api/health").status_code == 403


def test_browser_login_cookie_and_native_submission_replay(browser_http):
    app, actor, client, _ = browser_http
    signed = login(client)
    cookie = signed.headers["set-cookie"]
    assert "__Host-jarvis-session=" in cookie
    for attribute in ("HttpOnly", "Secure", "SameSite=strict", "Path=/", "Max-Age=900"):
        assert attribute in cookie
    assert "Domain=" not in cookie and TOKEN not in signed.text and TOKEN not in cookie
    headers = {
        "Origin": ORIGIN,
        "X-Jarvis-CSRF": signed.json()["data"]["csrfToken"],
        "Idempotency-Key": "browser-goal-1",
    }
    body = {"title": "Browser objective", "description": "An ordinary authorized goal"}
    first = client.post("/api/remote/goals", json=body, headers=headers)
    assert first.status_code == 201, first.text
    replay = client.post("/api/remote/goals", json=body, headers=headers)
    assert replay.json() == first.json()
    task = first.json()["data"]
    assert app.state.repository.get_task_durable(task["id"]).createdBy == actor.actor_id
    assert client.get("/api/remote/goals", headers=headers).status_code == 200
    assert client.get("/api/tasks", headers=headers).status_code == 403


def test_remote_browser_cannot_reach_local_provider_configuration(browser_http):
    _, _, client, _ = browser_http
    session = login(client)
    headers = {"Origin": ORIGIN, "X-Jarvis-CSRF": session.json()["data"]["csrfToken"]}
    operations = [
        ("GET", "/api/model-configuration/profiles", None),
        ("POST", "/api/model-configuration/setup", {"confirmation": "configure_model_operator"}),
        ("POST", "/api/model-configuration/profiles/groq/metadata", {"expected_revision": 1}),
    ]
    for method, path, body in operations:
        response = client.request(method, path, headers=headers, json=body)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "REMOTE_ROUTE_UNAVAILABLE"


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Origin": "https://attacker.test"},
        {"Origin": "null"},
        {"Origin": ORIGIN, "Sec-Fetch-Site": "cross-site"},
    ],
)
def test_login_requires_exact_same_origin_context(browser_http, headers):
    _, _, client, bearer = browser_http
    response = client.post("/api/remote/session", headers=bearer | headers)
    assert response.status_code == 403
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize(
    "headers", [{}, {"Origin": ORIGIN}, {"Origin": ORIGIN, "X-Jarvis-CSRF": "wrong"}]
)
def test_cookie_requests_require_csrf_even_for_reads(browser_http, headers):
    _, _, client, _ = browser_http
    login(client)
    assert client.get("/api/remote/goals", headers=headers).status_code == 403


def test_restore_and_logout_revoke_cookie(browser_http):
    _, _, client, _ = browser_http
    signed = login(client)
    restored = client.post(
        "/api/remote/session/restore", headers={"Origin": ORIGIN, "X-Jarvis-Session": "restore"}
    )
    assert restored.status_code == 200
    assert restored.json()["data"]["csrfToken"] == signed.json()["data"]["csrfToken"]
    token = client.cookies.get("__Host-jarvis-session")
    headers = {"Origin": ORIGIN, "X-Jarvis-CSRF": restored.json()["data"]["csrfToken"]}
    assert client.post("/api/remote/session/logout", headers=headers).status_code == 200
    client.cookies.set("__Host-jarvis-session", token, domain="remote.test", path="/")
    assert client.get("/api/remote/goals", headers=headers).status_code == 401


def test_remote_html_and_only_trusted_hashed_assets_are_served(browser_http):
    _, _, client, _ = browser_http
    page = client.get("/remote")
    assert page.status_code == 200
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert page.headers["cache-control"] == "no-store"
    assert client.get("/assets/remote-abcdef.js").status_code == 200
    for path in ("/", "/index.html", "/sw.js", "/assets/config.json", "/assets/unknown-abcdef.js"):
        assert client.get(path).status_code in {403, 404}


def test_non_ascii_csrf_is_denied_without_server_error(browser_http):
    from app.core.errors import DomainError

    _, _, client, _ = browser_http
    login(client)
    manager = browser_http[0].state.remote_browser_sessions
    with pytest.raises(DomainError) as error:
        manager.authenticate(client.cookies.get("__Host-jarvis-session"), "\u2603")
    assert error.value.code == "REMOTE_CSRF_REQUIRED"


def test_default_https_port_uses_the_browser_canonical_origin(browser_http, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import create_app

    original, _, _, headers = browser_http
    monkeypatch.setenv("JARVIS_REMOTE_ORIGIN", "https://remote.test:443")
    app = create_app(database_url=original.state.settings.database_url)
    with TestClient(app, base_url="https://remote.test") as client:
        response = client.post(
            "/api/remote/session", headers=headers | {"Origin": "https://remote.test"}
        )
        assert response.status_code == 200, response.text


def test_session_expiry_idle_bound_and_restart_fail_closed(browser_http):
    from app.core.errors import DomainError
    from app.remote_control.browser import BrowserSessions

    app, actor, _, _ = browser_http
    clock = [100.0]
    sessions = BrowserSessions(app.state.remote_control_service.access, clock=lambda: clock[0])
    token, summary = sessions.create(actor)
    clock[0] += 299
    assert sessions.authenticate(token, summary.csrfToken)[0] == actor
    clock[0] += 299
    assert sessions.authenticate(token, summary.csrfToken)[0] == actor
    clock[0] = 1000.0
    with pytest.raises(DomainError) as expired:
        sessions.authenticate(token, summary.csrfToken)
    assert expired.value.status_code == 401
    clock[0] = 2000.0
    token, summary = sessions.create(actor)
    clock[0] += 300
    with pytest.raises(DomainError):
        sessions.authenticate(token, summary.csrfToken)
    replacement = BrowserSessions(app.state.remote_control_service.access)
    with pytest.raises(DomainError):
        replacement.authenticate(token, summary.csrfToken)


def test_credential_ambiguity_duplicate_cookies_and_restore_csrf(browser_http):
    _, _, client, bearer = browser_http
    signed = login(client)
    csrf = signed.json()["data"]["csrfToken"]
    headers = {"Origin": ORIGIN, "X-Jarvis-CSRF": csrf}
    assert client.get("/api/remote/goals", headers=headers | bearer).status_code == 401
    token = client.cookies.get("__Host-jarvis-session")
    assert (
        client.get(
            "/api/remote/goals",
            headers=headers
            | {
                "Cookie": "__Host-jarvis-session=" + token + "; __Host-jarvis-session=" + token,
            },
        ).status_code
        == 401
    )
    assert client.post("/api/remote/session/restore", headers={"Origin": ORIGIN}).status_code == 403


def test_browser_session_retains_live_native_revocation(browser_http):
    from app.models.identity import AssignPermissionRequest

    app, actor, client, _ = browser_http
    signed = login(client)
    headers = {"Origin": ORIGIN, "X-Jarvis-CSRF": signed.json()["data"]["csrfToken"]}
    permission = next(
        item
        for item in app.state.identity_service.list_definitions("permission", 0, 100)
        if item.stable_key == "remote.read"
    )
    app.state.identity_service.assign_permission(
        actor.actor_id,
        AssignPermissionRequest(
            permission_id=permission.id,
            effect="deny",
            resource_type="administrative_function",
            resource_id="remote_control",
        ),
    )
    assert client.get("/api/remote/goals", headers=headers).status_code == 403
    assert (
        client.post(
            "/api/remote/session/restore", headers={"Origin": ORIGIN, "X-Jarvis-Session": "restore"}
        ).status_code
        == 403
    )


def test_session_capacity_is_bounded_without_evicting_other_operators(browser_http):
    from app.core.errors import DomainError
    from app.remote_control.browser import MAX_SESSIONS, BrowserSessions

    app, actor, _, _ = browser_http
    sessions = BrowserSessions(app.state.remote_control_service.access)
    first = sessions.create(actor)
    for _ in range(MAX_SESSIONS - 1):
        sessions.create(actor)
    with pytest.raises(DomainError) as limited:
        sessions.create(actor)
    assert limited.value.code == "REMOTE_SESSION_LIMIT"
    assert sessions.authenticate(first[0], first[1].csrfToken)[0] == actor
