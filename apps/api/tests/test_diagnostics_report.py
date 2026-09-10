"""Report, CLI, redaction, idempotence, and live end-to-end doctor coverage.

The live tests start a real in-process uvicorn API and a real loopback web
server, then run the doctor with its real probes (no scripting) so the exact
operator path is exercised on every platform, including Linux CI.
"""

from __future__ import annotations

import json
import socket
import subprocess
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import uvicorn

from app.diagnostics.cli import main as doctor_main
from app.diagnostics.models import CheckStatus
from app.diagnostics.probes import HealthResult
from app.diagnostics.runner import (
    REPORT_JSON_NAME,
    REPORT_MARKDOWN_NAME,
    render_json,
    render_markdown,
    render_text,
    run_diagnostics,
    write_report_files,
)
from app.main import DATABASE_REVISION, create_app
from tests.diagnostics_fixtures import (
    REPOSITORY_ROOT,
    ScriptedProbe,
    api_health_payload,
    base_environment,
    build_repository,
    build_seeded_database,
    write_supervisor_state,
)

HEALTH_URL = "http://127.0.0.1:8000/api/health"
SECRET_VALUE = "super-secret-value-4242"
FIXED_CLOCK = lambda: datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)  # noqa: E731 - test constant


@pytest.fixture()
def repository(tmp_path: Path) -> Path:
    return build_repository(tmp_path)


@pytest.fixture()
def seeded_database(tmp_path: Path) -> Path:
    return build_seeded_database(tmp_path)


def environment(
    tmp_path: Path, repository: Path, database: Path, **overrides: str
) -> dict[str, str]:
    values = base_environment(tmp_path, repository, database)
    values.update(overrides)
    return values


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


def test_diagnostic_report_redacts_secrets(
    repository: Path, seeded_database: Path, tmp_path: Path
) -> None:
    values = environment(tmp_path, repository, seeded_database)
    values["JARVIS_MODEL_OPENAI_COMPATIBLE_API_KEY"] = SECRET_VALUE
    # Simulate a leak vector: the endpoint answers with the secret as identity.
    probe = ScriptedProbe(
        {
            HEALTH_URL: HealthResult(
                True, "healthy", payload=api_health_payload(service=SECRET_VALUE)
            )
        }
    )
    run_ = run_diagnostics(repository, environ=values, http_probe=probe, clock=FIXED_CLOCK)
    report = run_.report
    leaked = report.check("api")
    assert leaked is not None and SECRET_VALUE in leaked.reason

    rendered = render_json(report, run_.context.secret_values)
    assert SECRET_VALUE not in rendered
    assert "[redacted]" in rendered

    markdown = render_markdown(report, run_.context.secret_values)
    text = render_text(report, run_.context.secret_values)
    assert SECRET_VALUE not in markdown
    assert SECRET_VALUE not in text

    json_path, markdown_path = write_report_files(
        report, tmp_path / "bundle", run_.context.secret_values
    )
    assert SECRET_VALUE not in json_path.read_text(encoding="utf-8")
    assert SECRET_VALUE not in markdown_path.read_text(encoding="utf-8")
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["schemaVersion"] == 1
    assert payload["overall"] in {"healthy", "degraded", "blocked"}
    assert any(item["name"] == "api" for item in payload["checks"])


def test_collect_secret_values_targets_secret_named_environment_only() -> None:
    from app.diagnostics.redaction import collect_secret_values

    values = {
        "JARVIS_MODEL_OPENAI_COMPATIBLE_API_KEY": "key-value-1",
        "SOME_PASSWORD": "password-value-1",
        "API_PORT": "8000",
        "APP_ENV": "development",
    }
    secrets = collect_secret_values(values)
    assert "key-value-1" in secrets
    assert "password-value-1" in secrets
    assert "8000" not in secrets
    assert "development" not in secrets


def test_redact_value_redacts_secret_named_keys_recursively() -> None:
    from app.diagnostics.redaction import REDACTED, redact_value

    value = {
        "authorization": "Bearer abc",
        "nested": {"apiToken": "tok", "safe": "ok", "items": [{"password": "x", "note": "hi"}]},
    }
    result = redact_value(value)
    assert result["authorization"] == REDACTED
    assert result["nested"]["apiToken"] == REDACTED
    assert result["nested"]["safe"] == "ok"
    assert result["nested"]["items"][0]["password"] == REDACTED
    assert result["nested"]["items"][0]["note"] == "hi"


# ---------------------------------------------------------------------------
# Idempotence and report files
# ---------------------------------------------------------------------------


def test_repeated_doctor_invocation_is_idempotent(
    repository: Path, seeded_database: Path, tmp_path: Path
) -> None:
    probe = ScriptedProbe({HEALTH_URL: HealthResult(True, "healthy", payload=api_health_payload())})
    values = environment(tmp_path, repository, seeded_database)
    first = run_diagnostics(repository, environ=values, http_probe=probe, clock=FIXED_CLOCK)
    second = run_diagnostics(repository, environ=values, http_probe=probe, clock=FIXED_CLOCK)
    assert first.report.overall == second.report.overall
    assert [(c.name, c.status) for c in first.report.checks] == [
        (c.name, c.status) for c in second.report.checks
    ]
    assert first.report.blocked_reasons == second.report.blocked_reasons
    assert first.report.ready_to_run == second.report.ready_to_run
    assert render_json(first.report) == render_json(second.report)

    target = tmp_path / "reports"
    first_files = write_report_files(first.report, target)
    first_json = first_files[0].read_text(encoding="utf-8")
    first_markdown = first_files[1].read_text(encoding="utf-8")
    second_files = write_report_files(second.report, target)
    assert second_files == first_files
    assert second_files[0].read_text(encoding="utf-8") == first_json
    assert second_files[1].read_text(encoding="utf-8") == first_markdown
    payload = json.loads(first_json)
    assert payload["overall"] == first.report.overall.value
    assert payload["generatedAt"] == "2026-09-08T12:00:00Z"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_exit_codes_json_and_report(
    repository: Path,
    seeded_database: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for key, value in environment(tmp_path, repository, tmp_path / "missing.db").items():
        monkeypatch.setenv(key, value)
    for leaked in ("JARVIS_SUPERVISOR_API_URL", "JARVIS_SUPERVISOR_WEB_URL", "API_HOST"):
        monkeypatch.delenv(leaked, raising=False)

    exit_code = doctor_main(["--repository", str(repository), "--json"])
    captured = capsys.readouterr()
    assert exit_code == 2  # blocked: API down, database missing
    payload = json.loads(captured.out)
    assert payload["overall"] == "blocked"
    assert payload["readyToRun"] is False
    assert {item["status"] for item in payload["checks"]} <= {
        "healthy",
        "degraded",
        "blocked",
        "disabled",
        "unknown",
    }
    assert all(
        set(item) == {"name", "group", "status", "reason", "remediation", "identifiers"}
        for item in payload["checks"]
    )

    exit_code = doctor_main(
        ["--repository", str(repository), "--json", "--report", str(tmp_path / "cli-report")]
    )
    assert exit_code == 2
    assert (tmp_path / "cli-report" / REPORT_JSON_NAME).is_file()
    assert (tmp_path / "cli-report" / REPORT_MARKDOWN_NAME).is_file()

    # The concise text output names every check and a remediation hint.
    exit_code = doctor_main(["--repository", str(repository)])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "Overall: blocked" in captured.out
    assert "BLOCKED" in captured.out
    assert "fix:" in captured.out


def test_cli_report_at_repository_root_is_git_ignored(
    repository: Path,
    seeded_database: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (repository / ".gitignore").write_text(
        (REPOSITORY_ROOT / ".gitignore").read_text(encoding="utf-8"), encoding="utf-8"
    )
    for key, value in environment(tmp_path, repository, seeded_database).items():
        monkeypatch.setenv(key, value)
    exit_code = doctor_main(["--repository", str(repository), "--report"])
    assert exit_code in {0, 1, 2}
    assert (repository / REPORT_JSON_NAME).is_file()
    assert (repository / REPORT_MARKDOWN_NAME).is_file()
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", REPORT_JSON_NAME],
        cwd=repository,
        capture_output=True,
        timeout=10,
    )
    assert ignored.returncode == 0, "generated diagnostics must be ignored by git"


def test_cli_doctor_error_exit_code(
    repository: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr("app.diagnostics.cli.run_diagnostics", explode)
    exit_code = doctor_main(["--repository", str(repository)])
    captured = capsys.readouterr()
    assert exit_code == 3
    assert "failed unexpectedly" in captured.err


# ---------------------------------------------------------------------------
# Live end-to-end (real HTTP probes, real database, real supervisor state)
# ---------------------------------------------------------------------------


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _WebHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - http.server API
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<html><body>Jarvis web</body></html>")

    def log_message(self, *_args: object) -> None:
        return


@pytest.fixture()
def live_runtime(tmp_path: Path, repository: Path):
    database = tmp_path / "live-runtime.db"
    app = create_app(
        database_url=f"sqlite:///{database.as_posix()}", recover_interrupted_workflow=False
    )
    api_port = free_port()
    web_port = free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=api_port, log_level="warning")
    )
    api_thread = threading.Thread(target=server.run, name="jarvis-doctor-live-api", daemon=True)
    api_thread.start()
    web_server = ThreadingHTTPServer(("127.0.0.1", web_port), _WebHandler)
    web_thread = threading.Thread(
        target=web_server.serve_forever, name="jarvis-doctor-live-web", daemon=True
    )
    web_thread.start()
    deadline = time.monotonic() + 30
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started, "live API did not start"
    try:
        yield {
            "database": database,
            "api_port": api_port,
            "web_port": web_port,
        }
    finally:
        server.should_exit = True
        api_thread.join(timeout=15)
        web_server.shutdown()
        web_server.server_close()
        web_thread.join(timeout=15)
        app.state.engine.dispose()


def test_live_end_to_end_healthy_and_exit_codes(
    repository: Path,
    tmp_path: Path,
    live_runtime,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    values = {
        "LOCALAPPDATA": str(tmp_path / "localappdata"),
        "JARVIS_DATABASE_URL": f"sqlite:///{live_runtime['database'].as_posix()}",
        "JARVIS_AUTO_MIGRATE": "false",
        "API_PORT": str(live_runtime["api_port"]),
        "JARVIS_SUPERVISOR_WEB_PORT": str(live_runtime["web_port"]),
    }
    write_supervisor_state(repository, values)
    run_ = run_diagnostics(repository, environ=values, deep=True)
    report = run_.report
    assert report.overall == CheckStatus.HEALTHY, [
        (item.name, item.status.value, item.reason) for item in report.checks
    ]
    assert report.ready_to_run is True
    api = report.check("api")
    assert api is not None and api.identifiers["service"] == "jarvis-simulator-api"
    database = report.check("database")
    assert database is not None and database.identifiers["databaseRevision"] == DATABASE_REVISION
    for name in (
        "api_system_status_contract",
        "office_runtime_path",
        "supervisor",
        "frontend",
        "emergency_stop",
        "recent_failures",
        "configuration",
        "repository_identity",
    ):
        result = report.check(name)
        assert result is not None and result.status == CheckStatus.HEALTHY, (
            f"{name}: {result.status} {result.reason}"
        )

    for key, value in values.items():
        monkeypatch.setenv(key, value)
    for leaked in ("JARVIS_SUPERVISOR_API_URL", "JARVIS_SUPERVISOR_WEB_URL", "API_HOST"):
        monkeypatch.delenv(leaked, raising=False)
    exit_code = doctor_main(["--repository", str(repository)])
    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Overall: healthy" in output

    # Without supervisor coordination state the same system is degraded (exit 1).
    coordination_root = Path(values["LOCALAPPDATA"]) / "Jarvis" / "Supervisor"
    for state_file in coordination_root.rglob("state.json"):
        state_file.unlink()
    degraded = run_diagnostics(repository, environ=values, deep=True)
    assert degraded.report.overall == CheckStatus.DEGRADED
    supervisor = degraded.report.check("supervisor")
    assert supervisor is not None and supervisor.status == CheckStatus.DEGRADED


def test_live_provider_probe_with_fake_ollama_service(
    repository: Path, seeded_database: Path, tmp_path: Path
) -> None:
    class OllamaHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - http.server API
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"models": [{"name": "llama-test"}]}).encode())

        def log_message(self, *_args: object) -> None:
            return

    def provider_environment(port: int) -> dict[str, str]:
        return environment(
            tmp_path,
            repository,
            seeded_database,
            **{
                "JARVIS_MODEL_EXECUTION_MODE": "local_only",
                "JARVIS_MODEL_OLLAMA_ENABLED": "true",
                "JARVIS_MODEL_OLLAMA_BASE_URL": f"http://127.0.0.1:{port}",
                "JARVIS_MODEL_OLLAMA_MODEL": "llama-test",
            },
        )

    port = free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), OllamaHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        run_ = run_diagnostics(repository, environ=provider_environment(port))
        provider = run_.report.check("model_provider")
        assert provider is not None
        assert provider.status == CheckStatus.HEALTHY, provider.reason
        assert provider.identifiers["providers"]["ollama"]["modelAvailable"] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)

    class MalformedHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - http.server API
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"models": "not-a-list"}')

        def log_message(self, *_args: object) -> None:
            return

    port = free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), MalformedHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        run_ = run_diagnostics(repository, environ=provider_environment(port))
        provider = run_.report.check("model_provider")
        assert provider is not None
        assert provider.status == CheckStatus.BLOCKED, provider.reason
        assert "malformed" in provider.reason
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)
