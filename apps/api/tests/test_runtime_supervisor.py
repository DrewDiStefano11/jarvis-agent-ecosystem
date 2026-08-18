from __future__ import annotations

import io
import json
import logging
import os
import sqlite3
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.runtime_supervisor.autostart import status as autostart_status
from app.runtime_supervisor.autostart import task_arguments, task_xml
from app.runtime_supervisor.backup import BackupError, create_backup, prune_backups
from app.runtime_supervisor.cli import start, stop
from app.runtime_supervisor.config import SupervisorConfig, SupervisorConfigurationError
from app.runtime_supervisor.doctor import run_doctor
from app.runtime_supervisor.health import HealthResult, probe_http
from app.runtime_supervisor.io import atomic_write_json, ensure_runtime_home, read_json
from app.runtime_supervisor.logging_utils import child_output_logger
from app.runtime_supervisor.ownership import SingletonLock, process_identity, state_ownership
from app.runtime_supervisor.status import load_status
from app.runtime_supervisor.supervisor import (
    RuntimeSupervisor,
    build_process_registry,
    shutdown_wait_seconds,
)
from app.runtime_supervisor.windows_console import ensure_hidden_console


def make_config(tmp_path: Path, **environment: str) -> SupervisorConfig:
    repository = tmp_path / "Repository With Spaces"
    (repository / "apps" / "api" / "app").mkdir(parents=True, exist_ok=True)
    (repository / "apps" / "api" / "app" / "main.py").write_text("", encoding="utf-8")
    (repository / "apps" / "web" / "node_modules" / "vite" / "bin").mkdir(
        parents=True, exist_ok=True
    )
    (repository / "apps" / "web" / "node_modules" / "vite" / "bin" / "vite.js").write_text(
        "", encoding="utf-8"
    )
    (repository / "apps" / "web" / "dist").mkdir(exist_ok=True)
    (repository / "apps" / "web" / "dist" / "index.html").write_text("ok", encoding="utf-8")
    values = {
        "JARVIS_SUPERVISOR_RUNTIME_HOME": str(tmp_path / "runtime home"),
        "JARVIS_SUPERVISOR_NODE_EXECUTABLE": sys.executable,
        "JARVIS_SUPERVISOR_PYTHON_EXECUTABLE": sys.executable,
        "JARVIS_DATABASE_URL": "sqlite:///./data/jarvis.db",
    }
    values.update(environment)
    api_host = values.get("API_HOST", "127.0.0.1")
    api_port = values.get("API_PORT", "8000")
    (repository / "apps" / "web" / "dist" / "runtime-supervisor.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "apiBaseUrl": values.get("VITE_API_BASE_URL", f"http://{api_host}:{api_port}"),
                "webSocketUrl": values.get("VITE_WS_URL", f"ws://{api_host}:{api_port}/ws/events"),
            }
        ),
        encoding="utf-8",
    )
    return SupervisorConfig.load(repository, values)


def create_database(config: SupervisorConfig, value: str = "value") -> None:
    config.database_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(config.database_path) as connection:
        connection.execute("CREATE TABLE sample (value TEXT)")
        connection.execute("INSERT INTO sample VALUES (?)", (value,))
        connection.execute("CREATE TABLE alembic_version (version_num TEXT)")
        connection.execute("INSERT INTO alembic_version VALUES ('revision-test')")


class FakeProcess:
    def __init__(self, argv: list[str], **_: object) -> None:
        self.argv = argv
        self.pid = os.getpid()
        self.stdout = io.StringIO("")
        self.stderr = io.StringIO("")
        self.returncode: int | None = None
        self.signals: list[int] = []

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def send_signal(self, value: int) -> None:
        self.signals.append(value)
        self.returncode = 0

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9


def attach_logger(supervisor: RuntimeSupervisor) -> None:
    logger = logging.getLogger(f"test-supervisor-{id(supervisor)}")
    logger.handlers = [logging.NullHandler()]
    supervisor.logger = logger


def test_configuration_resolves_repository_with_spaces_and_external_runtime(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    assert "Repository With Spaces" in str(config.repository)
    assert config.runtime_home == (tmp_path / "runtime home").resolve()
    assert config.database_path == (config.api_directory / "data" / "jarvis.db").resolve()


def test_configuration_parses_sqlite_query_parameters(tmp_path: Path) -> None:
    config = make_config(
        tmp_path,
        JARVIS_DATABASE_URL="sqlite:///./data/jarvis.db?timeout=30",
    )
    assert config.database_path == (config.api_directory / "data" / "jarvis.db").resolve()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("JARVIS_SUPERVISOR_API_URL", "http://127.0.0.1:9000"),
        ("JARVIS_SUPERVISOR_API_URL", "http://127.0.0.1:8000/base"),
        ("JARVIS_SUPERVISOR_WEB_URL", "http://127.0.0.1:9000"),
        ("JARVIS_SUPERVISOR_WEB_URL", "http://127.0.0.1:5173/base"),
    ],
)
def test_configuration_rejects_probe_endpoint_not_owned_by_supervisor(
    tmp_path: Path, name: str, value: str
) -> None:
    with pytest.raises(SupervisorConfigurationError, match="launched host and port"):
        make_config(tmp_path, **{name: value})


@pytest.mark.parametrize("name", ["API_HOST", "JARVIS_SUPERVISOR_WEB_HOST"])
def test_configuration_rejects_public_bind(tmp_path: Path, name: str) -> None:
    with pytest.raises(SupervisorConfigurationError, match="loopback"):
        make_config(tmp_path, **{name: "0.0.0.0"})


def test_configuration_rejects_remote_ollama(tmp_path: Path) -> None:
    with pytest.raises(SupervisorConfigurationError, match="loopback"):
        make_config(
            tmp_path,
            JARVIS_MODEL_OLLAMA_ENABLED="true",
            JARVIS_MODEL_OLLAMA_BASE_URL="https://models.example.com",
        )


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("WEB_ORIGIN", "http://192.168.1.5:5173"),
        ("VITE_API_BASE_URL", "https://api.example.com"),
        ("VITE_WS_URL", "wss://events.example.com/ws"),
    ],
)
def test_configuration_rejects_remote_frontend_endpoints(
    tmp_path: Path, name: str, value: str
) -> None:
    with pytest.raises(SupervisorConfigurationError, match="loopback"):
        make_config(tmp_path, **{name: value})


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("WEB_ORIGIN", "http://localhost:5173"),
        ("WEB_ORIGIN", "http://127.0.0.1:5174"),
        ("VITE_API_BASE_URL", "http://127.0.0.1:8001"),
        ("VITE_API_BASE_URL", "http://127.0.0.1:8000/api"),
        ("VITE_WS_URL", "ws://127.0.0.1:8001/ws/events"),
        ("VITE_WS_URL", "ws://127.0.0.1:8000/ws/other"),
    ],
)
def test_configuration_rejects_frontend_endpoint_not_owned_by_supervisor(
    tmp_path: Path, name: str, value: str
) -> None:
    with pytest.raises(SupervisorConfigurationError, match="owned endpoint"):
        make_config(tmp_path, **{name: value})


def test_configuration_derives_frontend_endpoints_from_owned_binds(tmp_path: Path) -> None:
    config = make_config(
        tmp_path,
        API_HOST="localhost",
        API_PORT="8123",
        JARVIS_SUPERVISOR_WEB_HOST="localhost",
        JARVIS_SUPERVISOR_WEB_PORT="5234",
    )
    assert config.environment["WEB_ORIGIN"] == "http://localhost:5234"
    assert config.environment["VITE_API_BASE_URL"] == "http://localhost:8123"
    assert config.environment["VITE_WS_URL"] == "ws://localhost:8123/ws/events"


def test_start_refuses_frontend_built_for_different_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(tmp_path)
    (config.web_directory / "dist" / "runtime-supervisor.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "apiBaseUrl": "http://127.0.0.1:9000",
                "webSocketUrl": "ws://127.0.0.1:9000/ws/events",
            }
        ),
        encoding="utf-8",
    )
    spawned = False

    def spawn(_config: SupervisorConfig) -> int:
        nonlocal spawned
        spawned = True
        return 123

    monkeypatch.setattr("app.runtime_supervisor.cli._spawn_daemon", spawn)
    with pytest.raises(SupervisorConfigurationError, match="frontend build endpoint mismatch"):
        start(config)
    assert spawned is False
    assert not config.runtime_home.exists()


def test_windows_supervisor_allocates_and_hides_console_for_child_signals() -> None:
    class Kernel32:
        def __init__(self) -> None:
            self.window = 0
            self.allocated = False

        def GetConsoleCP(self) -> int:
            return 65001 if self.allocated else 0

        def GetConsoleWindow(self) -> int:
            return self.window

        def AllocConsole(self) -> int:
            self.allocated = True
            self.window = 123
            return 1

    class User32:
        hidden: list[tuple[int, int]] = []

        def ShowWindow(self, window: int, command: int) -> None:
            self.hidden.append((window, command))

    kernel32 = Kernel32()
    user32 = User32()
    ensure_hidden_console(platform="nt", kernel32=kernel32, user32=user32)
    assert kernel32.allocated is True
    assert user32.hidden == [(123, 0)]


def test_worker_remains_disabled_by_default(tmp_path: Path) -> None:
    definitions = {item.name: item for item in build_process_registry(make_config(tmp_path))}
    assert definitions["autonomous_worker"].enabled is False


def test_enabled_worker_requires_existing_local_only_configuration(tmp_path: Path) -> None:
    with pytest.raises(SupervisorConfigurationError, match="local_only"):
        make_config(tmp_path, JARVIS_AUTONOMOUS_WORKER_ENABLED="true")
    config = make_config(
        tmp_path,
        JARVIS_AUTONOMOUS_WORKER_ENABLED="true",
        JARVIS_MODEL_EXECUTION_MODE="local_only",
        JARVIS_AUTONOMOUS_WORKER_ACTOR_ID="worker-actor",
        JARVIS_AUTONOMOUS_WORKER_INSTANCE_ID="worker-instance",
    )
    assert config.worker_enabled is True


def test_runtime_home_rejects_repository_and_filesystem_root(tmp_path: Path) -> None:
    with pytest.raises(SupervisorConfigurationError, match="RUNTIME_HOME"):
        make_config(
            tmp_path, JARVIS_SUPERVISOR_RUNTIME_HOME=str(tmp_path / "Repository With Spaces")
        )
    with pytest.raises(SupervisorConfigurationError, match="filesystem root"):
        make_config(tmp_path, JARVIS_SUPERVISOR_RUNTIME_HOME=Path(tmp_path.anchor).as_posix())


def test_runtime_home_refuses_nonempty_or_corrupt_unowned_directory(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    config.runtime_home.mkdir()
    (config.runtime_home / "user-file.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(RuntimeError, match="nonempty"):
        ensure_runtime_home(config.runtime_home, config.repository)
    (config.runtime_home / "user-file.txt").unlink()
    (config.runtime_home / ".jarvis-supervisor-runtime.json").write_text(
        "not json", encoding="utf-8"
    )
    with pytest.raises(RuntimeError, match="unreadable"):
        ensure_runtime_home(config.runtime_home, config.repository)


def test_health_probe_does_not_follow_redirects() -> None:
    class RedirectHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(302)
            self.send_header("Location", "https://example.com/")
            self.end_headers()

        def log_message(self, *_args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = probe_http(f"http://127.0.0.1:{server.server_port}", timeout=1)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert result.available is False


def test_process_registry_is_shell_free_loopback_and_stable(tmp_path: Path) -> None:
    definitions = build_process_registry(make_config(tmp_path))
    api = definitions[0]
    web = definitions[1]
    assert api.name == "api" and web.name == "web"
    assert "--reload" not in api.argv
    assert api.argv[api.argv.index("--host") + 1] == "127.0.0.1"
    assert web.argv[web.argv.index("--host") + 1] == "127.0.0.1"
    assert all("0.0.0.0" not in value for item in definitions for value in item.argv)


def test_singleton_lock_allows_only_one_owner(tmp_path: Path) -> None:
    first = SingletonLock(tmp_path / "owner.lock")
    second = SingletonLock(tmp_path / "owner.lock")
    assert first.acquire() is True
    try:
        assert second.acquire() is False
    finally:
        first.release()
    assert second.acquire() is True
    second.release()


def test_state_ownership_rejects_pid_reuse(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.runtime_supervisor.ownership.process_identity", lambda _pid: "new")
    assert state_ownership({"pid": 123, "processIdentity": "old"}) == "stale"
    assert state_ownership({"pid": 123, "processIdentity": "new"}) == "running"


@pytest.mark.skipif(os.name != "nt", reason="Windows process identity behavior")
def test_windows_process_identity_rejects_exited_process_with_open_handle() -> None:
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait(timeout=10)
    assert process_identity(process.pid) is None


def test_stop_does_not_signal_process_from_stale_state(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    ensure_runtime_home(config.runtime_home, config.repository)
    atomic_write_json(
        config.state_path,
        {
            "supervisorState": "running",
            "pid": os.getpid(),
            "processIdentity": "wrong-creation-time",
            "instanceId": "stale",
        },
    )
    result = stop(config)
    assert result["result"] == "already_stopped"
    assert not config.stop_request_path.exists()


def test_stop_wait_budget_covers_every_enabled_child(tmp_path: Path) -> None:
    config = make_config(
        tmp_path,
        JARVIS_SUPERVISOR_GRACEFUL_SHUTDOWN_SECONDS="7",
        JARVIS_SUPERVISOR_HEALTH_INTERVAL_SECONDS="3",
    )
    assert shutdown_wait_seconds(config) == 3 + 2 * (7 + 5) + 10

    worker_config = make_config(
        tmp_path / "worker",
        JARVIS_SUPERVISOR_GRACEFUL_SHUTDOWN_SECONDS="7",
        JARVIS_SUPERVISOR_HEALTH_INTERVAL_SECONDS="3",
        JARVIS_AUTONOMOUS_WORKER_ENABLED="true",
        JARVIS_MODEL_EXECUTION_MODE="local_only",
        JARVIS_AUTONOMOUS_WORKER_ACTOR_ID="operator",
        JARVIS_AUTONOMOUS_WORKER_INSTANCE_ID="runtime-worker",
    )
    assert shutdown_wait_seconds(worker_config) == 3 + 3 * (7 + 5) + 10


def test_atomic_json_never_leaves_partial_target(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    atomic_write_json(target, {"value": 1})
    assert read_json(target) == {"value": 1}
    assert list(tmp_path.glob("*.tmp")) == []


def test_online_backup_is_consistent_and_records_metadata(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    ensure_runtime_home(config.runtime_home, config.repository)
    create_database(config)
    manifest = create_backup(config)
    backup_path = config.backups_directory / str(manifest["backupFile"])
    assert manifest["alembicRevision"] == "revision-test"
    assert len(str(manifest["sha256"])) == 64
    with sqlite3.connect(backup_path) as connection:
        assert connection.execute("SELECT value FROM sample").fetchone() == ("value",)
        assert connection.execute("PRAGMA quick_check").fetchone() == ("ok",)


def test_backup_singleton_prevents_overlapping_operations(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    ensure_runtime_home(config.runtime_home, config.repository)
    create_database(config)
    lock = SingletonLock(config.runtime_home / "backup.lock")
    assert lock.acquire()
    try:
        with pytest.raises(BackupError, match="already running"):
            create_backup(config)
    finally:
        lock.release()


def test_backup_retention_deletes_only_owned_artifacts(tmp_path: Path) -> None:
    config = make_config(tmp_path, JARVIS_SUPERVISOR_BACKUP_RETENTION_COUNT="2")
    ensure_runtime_home(config.runtime_home, config.repository)
    config.backups_directory.mkdir()
    for index in range(4):
        (config.backups_directory / f"jarvis-20260101T00000{index}.sqlite3").write_bytes(b"db")
    unrelated = config.backups_directory / "important-user-file.txt"
    unrelated.write_text("keep", encoding="utf-8")
    removed = prune_backups(config)
    assert len(removed) == 2
    assert unrelated.read_text(encoding="utf-8") == "keep"


def test_backup_cleanup_requires_runtime_ownership_marker(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    config.backups_directory.mkdir(parents=True)
    with pytest.raises(BackupError, match="verified"):
        prune_backups(config)


def test_backup_refuses_dangerously_low_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(tmp_path)
    ensure_runtime_home(config.runtime_home, config.repository)
    create_database(config)
    monkeypatch.setattr(
        "app.runtime_supervisor.backup.shutil.disk_usage",
        lambda _path: SimpleNamespace(total=100, used=99, free=1),
    )
    with pytest.raises(BackupError, match="insufficient"):
        create_backup(config)
    assert list(config.backups_directory.glob("*.sqlite3")) == []


def test_backup_space_check_includes_uncheckpointed_wal_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(
        tmp_path,
        JARVIS_SUPERVISOR_DISK_WARNING_BYTES=str(2 * 1024 * 1024),
        JARVIS_SUPERVISOR_DISK_CRITICAL_BYTES=str(1024 * 1024),
    )
    ensure_runtime_home(config.runtime_home, config.repository)
    config.database_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(config.database_path) as writer:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE sample (payload BLOB)")
        writer.execute("INSERT INTO sample VALUES (zeroblob(2097152))")
        writer.commit()
        logical_size = int(writer.execute("PRAGMA page_count").fetchone()[0]) * int(
            writer.execute("PRAGMA page_size").fetchone()[0]
        )
        main_file_estimate = config.database_path.stat().st_size * 2 + 16 * 1024 * 1024
        logical_estimate = logical_size * 2 + 16 * 1024 * 1024
        assert main_file_estimate < logical_estimate
        monkeypatch.setattr(
            "app.runtime_supervisor.backup.shutil.disk_usage",
            lambda _path: SimpleNamespace(
                total=logical_estimate,
                used=logical_estimate - main_file_estimate,
                free=main_file_estimate,
            ),
        )
        with pytest.raises(BackupError, match="insufficient"):
            create_backup(config)
    assert list(config.backups_directory.glob("*.sqlite3")) == []


def test_periodic_backup_failure_retries_at_bounded_hourly_cadence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(tmp_path, JARVIS_SUPERVISOR_BACKUP_INTERVAL_HOURS="1")
    ensure_runtime_home(config.runtime_home, config.repository)
    now = [100.0]
    attempts: list[float] = []

    def fail_backup(_config: SupervisorConfig) -> None:
        attempts.append(now[0])
        raise BackupError("low disk")

    monkeypatch.setattr("app.runtime_supervisor.supervisor.create_backup", fail_backup)
    supervisor = RuntimeSupervisor(config, clock=lambda: now[0])
    attach_logger(supervisor)
    supervisor._periodic_backup()
    now[0] = 200
    supervisor._periodic_backup()
    now[0] = 3700
    supervisor._periodic_backup()
    assert attempts == [100, 3700]


def test_interrupted_backup_never_publishes_valid_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(tmp_path)
    ensure_runtime_home(config.runtime_home, config.repository)
    create_database(config)
    monkeypatch.setattr(
        "app.runtime_supervisor.backup.os.replace",
        lambda _source, _target: (_ for _ in ()).throw(OSError("interrupted")),
    )
    with pytest.raises(OSError, match="interrupted"):
        create_backup(config)
    assert list(config.backups_directory.glob("*.sqlite3")) == []
    assert list(config.backups_directory.glob("*.partial")) == []
    assert list(config.backups_directory.glob("*.json")) == []


def test_task_scheduler_xml_quotes_paths_and_contains_no_password(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    xml = task_xml(config, "S-1-5-21-123")
    assert str(config.repository) in xml
    assert str(config.python_executable) in xml
    assert "InteractiveToken" in xml
    assert "LeastPrivilege" in xml
    assert "password" not in xml.lower()
    assert f'"{config.repository}"' in task_arguments(config)


@pytest.mark.skipif(os.name != "nt", reason="Windows Task Scheduler behavior")
def test_task_scheduler_query_timeout_becomes_bounded_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(tmp_path)
    monkeypatch.setattr("app.runtime_supervisor.autostart._schtasks", lambda: "schtasks.exe")
    monkeypatch.setattr(
        "app.runtime_supervisor.autostart.subprocess.run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired("schtasks", 15)),
    )
    result = autostart_status(config)
    assert result.supported is False
    assert result.installed is False
    assert result.detail == "Task Scheduler query failed"


def test_doctor_is_read_only_and_reports_prerequisites(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    assert not config.runtime_home.exists()
    report = run_doctor(config)
    assert not config.runtime_home.exists()
    names = {item["name"] for item in report["checks"]}
    assert {
        "python",
        "node",
        "frontend_build",
        "frontend_build_endpoints",
        "disk_space",
        "autostart",
    } <= names


def test_doctor_attributes_occupied_ports_to_each_managed_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(tmp_path)
    monkeypatch.setattr("app.runtime_supervisor.doctor._port_available", lambda *_args: False)
    monkeypatch.setattr(
        "app.runtime_supervisor.doctor.load_status",
        lambda _config: {
            "ownership": "running",
            "processes": {
                "api": {
                    "processState": "failed",
                    "pid": None,
                    "processIdentity": None,
                },
                "web": {
                    "processState": "running",
                    "pid": 222,
                    "processIdentity": "web-child",
                },
            },
        },
    )
    monkeypatch.setattr(
        "app.runtime_supervisor.doctor.process_identity",
        lambda pid: "web-child" if pid == 222 else None,
    )

    checks = {item["name"]: item for item in run_doctor(config)["checks"]}
    assert checks["api_port"]["status"] == "fail"
    assert checks["api_port"]["detail"].endswith("in use")
    assert checks["web_port"]["status"] == "pass"
    assert checks["web_port"]["detail"].endswith("owned by running supervisor child web")


def test_ordered_start_waits_for_api_before_web(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    calls: list[str] = []

    def factory(argv: list[str], **kwargs: object) -> FakeProcess:
        del kwargs
        calls.append("api" if "uvicorn" in argv else "web")
        return FakeProcess(argv)

    supervisor = RuntimeSupervisor(
        config,
        process_factory=factory,
        probe=lambda *_args, **_kwargs: HealthResult(True, "healthy"),
        sleep=lambda _value: None,
    )
    attach_logger(supervisor)
    supervisor._start_ordered()
    assert calls == ["api", "web"]


def test_failed_api_prevents_web_and_worker_start(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    calls: list[list[str]] = []

    def factory(argv: list[str], **kwargs: object) -> FakeProcess:
        del kwargs
        calls.append(argv)
        process = FakeProcess(argv)
        process.returncode = 1
        return process

    supervisor = RuntimeSupervisor(config, process_factory=factory, sleep=lambda _value: None)
    attach_logger(supervisor)
    supervisor._start_ordered()
    assert len(calls) == 1
    assert "uvicorn" in calls[0]


def test_initial_spawn_failure_is_counted_once(tmp_path: Path) -> None:
    config = make_config(
        tmp_path,
        JARVIS_SUPERVISOR_RESTART_INITIAL_SECONDS="2",
    )

    def failing_factory(_argv: list[str], **_kwargs: object) -> FakeProcess:
        raise OSError("spawn failed")

    supervisor = RuntimeSupervisor(
        config,
        process_factory=failing_factory,
        clock=lambda: 10,
    )
    attach_logger(supervisor)
    managed = supervisor.processes["api"]

    supervisor._start_process(managed)
    assert managed.restart_count == 1
    assert managed.consecutive_failures == 1
    assert managed.next_restart_at == 12

    assert supervisor._wait_available(managed) is False
    assert managed.restart_count == 1
    assert managed.consecutive_failures == 1
    assert managed.next_restart_at == 12


def test_explicitly_enabled_worker_starts_last_and_recovers_crash(tmp_path: Path) -> None:
    config = make_config(
        tmp_path,
        JARVIS_AUTONOMOUS_WORKER_ENABLED="true",
        JARVIS_MODEL_EXECUTION_MODE="local_only",
        JARVIS_AUTONOMOUS_WORKER_ACTOR_ID="worker-actor",
        JARVIS_AUTONOMOUS_WORKER_INSTANCE_ID="worker-instance",
        JARVIS_SUPERVISOR_RESTART_INITIAL_SECONDS="0.1",
    )
    now = [0.0]
    labels: list[str] = []

    def factory(argv: list[str], **_kwargs: object) -> FakeProcess:
        label = (
            "api" if "uvicorn" in argv else "worker" if "app.autonomous_worker" in argv else "web"
        )
        labels.append(label)
        return FakeProcess(argv)

    supervisor = RuntimeSupervisor(
        config,
        process_factory=factory,
        clock=lambda: now[0],
        probe=lambda *_args, **_kwargs: HealthResult(True, "healthy"),
        sleep=lambda value: now.__setitem__(0, now[0] + value),
    )
    attach_logger(supervisor)
    supervisor._start_ordered()
    assert labels == ["api", "web", "worker"]
    worker = supervisor.processes["autonomous_worker"]
    assert worker.process is not None
    worker.process.returncode = 1  # type: ignore[attr-defined]
    supervisor._monitor_once()
    now[0] += 0.1
    supervisor._monitor_once()
    assert labels == ["api", "web", "worker", "worker"]
    assert worker.restart_count == 1


def test_startup_health_timeout_stops_child_and_counts_once(tmp_path: Path) -> None:
    config = make_config(tmp_path, JARVIS_SUPERVISOR_STARTUP_TIMEOUT_SECONDS="1")
    now = [0.0]

    def advance(value: float) -> None:
        now[0] += value

    supervisor = RuntimeSupervisor(
        config,
        clock=lambda: now[0],
        sleep=advance,
        probe=lambda *_args, **_kwargs: HealthResult(False, "unavailable"),
    )
    attach_logger(supervisor)
    managed = supervisor.processes["api"]
    managed.process = FakeProcess([])
    assert supervisor._wait_available(managed) is False
    assert managed.process is None
    assert managed.restart_count == 1


def test_worker_dependency_requires_healthy_web(tmp_path: Path) -> None:
    supervisor = RuntimeSupervisor(make_config(tmp_path))
    api = supervisor.processes["api"]
    web = supervisor.processes["web"]
    api.process = FakeProcess([])
    api.health = "healthy"
    web.process = FakeProcess([])
    web.health = "starting"
    assert supervisor._dependencies_ready("autonomous_worker") is False
    web.health = "healthy"
    assert supervisor._dependencies_ready("autonomous_worker") is True


def test_restart_backoff_is_bounded_and_resets_after_health_window(tmp_path: Path) -> None:
    config = make_config(
        tmp_path,
        JARVIS_SUPERVISOR_RESTART_INITIAL_SECONDS="1",
        JARVIS_SUPERVISOR_RESTART_MAXIMUM_SECONDS="4",
        JARVIS_SUPERVISOR_BACKOFF_RESET_SECONDS="10",
    )
    now = [0.0]
    supervisor = RuntimeSupervisor(config, clock=lambda: now[0])
    attach_logger(supervisor)
    managed = supervisor.processes["api"]
    delays = []
    for _ in range(5):
        supervisor._record_failure(managed, "crash")
        delays.append(managed.next_restart_at - now[0])
    assert delays == [1, 2, 4, 4, 4]
    managed.process = FakeProcess([])
    managed.started_at = 0
    now[0] = 11
    supervisor._monitor_once()
    assert managed.consecutive_failures == 0


def test_graceful_shutdown_is_idempotent(tmp_path: Path) -> None:
    supervisor = RuntimeSupervisor(make_config(tmp_path))
    attach_logger(supervisor)
    process = FakeProcess([])
    managed = supervisor.processes["api"]
    managed.process = process
    supervisor._terminate(managed, reason="test")
    supervisor._terminate(managed, reason="test again")
    assert process.returncode == 0
    assert managed.state == "stopped"


def test_status_contains_disabled_worker_and_paths(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    status = load_status(config)
    assert status["supervisorState"] == "not_running"
    assert status["processes"]["autonomous_worker"]["enabled"] is False
    assert status["logsDirectory"] == str(config.logs_directory)
    assert "currentGitSha" in status


def test_persisted_status_never_contains_environment_secrets(tmp_path: Path) -> None:
    config = make_config(tmp_path, JARVIS_TEST_SECRET="do-not-leak")
    ensure_runtime_home(config.runtime_home, config.repository)
    supervisor = RuntimeSupervisor(config)
    supervisor._write_state("starting")
    raw = config.state_path.read_text(encoding="utf-8")
    assert "do-not-leak" not in raw
    assert "JARVIS_TEST_SECRET" not in raw


def test_child_output_redacts_configured_secrets_and_rotates(tmp_path: Path) -> None:
    config = make_config(tmp_path, JARVIS_TEST_SECRET="top-secret-value")
    config.logs_directory.mkdir(parents=True)
    supervisor = RuntimeSupervisor(config)
    supervisor._forward_output(
        "api", "stderr", io.StringIO("token top-secret-value\n" + "x" * 200 + "\n")
    )
    content = (config.logs_directory / "api.log").read_text(encoding="utf-8")
    assert "top-secret-value" not in content
    assert "[REDACTED]" in content
    logger = child_output_logger(config.logs_directory, "rotation", 100, 2)
    for _ in range(20):
        logger.info("bounded output line")
    assert (config.logs_directory / "rotation.log.1").is_file()


def test_child_output_redacts_overlapping_secrets_longest_first(tmp_path: Path) -> None:
    config = make_config(
        tmp_path,
        JARVIS_SHORT_SECRET="pass",
        JARVIS_LONG_SECRET="password123",
    )
    config.logs_directory.mkdir(parents=True)
    supervisor = RuntimeSupervisor(config)
    assert supervisor.secret_values[:2] == ("password123", "pass")

    supervisor._forward_output("api", "stderr", io.StringIO("password123 pass\n"))
    content = (config.logs_directory / "api.log").read_text(encoding="utf-8")
    assert "password123" not in content
    assert "word123" not in content
    assert content.count("[REDACTED]") == 2


def test_supervisor_failure_remains_terminal_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(tmp_path)
    supervisor = RuntimeSupervisor(config)

    class FakeJob:
        def close(self) -> None:
            pass

    monkeypatch.setattr("app.runtime_supervisor.supervisor.WindowsJob", FakeJob)
    monkeypatch.setattr(supervisor, "_install_signal_handlers", lambda: None)
    monkeypatch.setattr(
        supervisor,
        "_probe_dependencies",
        lambda: (_ for _ in ()).throw(RuntimeError("dependency probe failed")),
    )
    monkeypatch.setattr(supervisor, "_shutdown_all", lambda: None)

    assert supervisor.run() == 1
    state = read_json(config.state_path)
    assert state is not None
    assert state["supervisorState"] == "failed"
    assert state["supervisor_failure"] == "dependency probe failed"
    assert state["lastCleanSupervisorShutdown"] is None


def test_normal_stop_refreshes_application_shutdown_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(tmp_path)
    config.database_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(config.database_path) as connection:
        connection.execute(
            "CREATE TABLE system_state (id INTEGER PRIMARY KEY, last_clean_shutdown TEXT)"
        )
        connection.execute("INSERT INTO system_state VALUES (1, NULL)")
    supervisor = RuntimeSupervisor(config)
    supervisor.stop_requested = True

    class FakeJob:
        def close(self) -> None:
            pass

    def shutdown() -> None:
        with sqlite3.connect(config.database_path) as connection:
            connection.execute(
                "UPDATE system_state SET last_clean_shutdown = ? WHERE id = 1",
                ("2026-08-18 12:34:56.000000",),
            )

    monkeypatch.setattr("app.runtime_supervisor.supervisor.WindowsJob", FakeJob)
    monkeypatch.setattr(supervisor, "_install_signal_handlers", lambda: None)
    monkeypatch.setattr(supervisor, "_probe_dependencies", lambda: None)
    monkeypatch.setattr(supervisor, "_start_ordered", lambda: None)
    monkeypatch.setattr(supervisor, "_shutdown_all", shutdown)

    assert supervisor.run() == 0
    state = read_json(config.state_path)
    assert state is not None
    assert state["lastApplicationCleanShutdown"] == "2026-08-18T12:34:56Z"


def test_known_good_metadata_records_healthy_sha_state(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    ensure_runtime_home(config.runtime_home, config.repository)
    supervisor = RuntimeSupervisor(config)
    for name in ("api", "web"):
        managed = supervisor.processes[name]
        managed.process = FakeProcess([])
        managed.health = "healthy"
    supervisor.git_sha = "a" * 40
    supervisor._record_known_good()
    metadata = read_json(config.runtime_home / "known-good.json")
    assert metadata is not None
    assert metadata["lastKnownHealthySha"] == "a" * 40


def test_known_good_metadata_rejects_degraded_application_health(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    ensure_runtime_home(config.runtime_home, config.repository)
    supervisor = RuntimeSupervisor(config)
    for name in ("api", "web"):
        managed = supervisor.processes[name]
        managed.process = FakeProcess([])
        managed.health = "healthy"
    supervisor.processes["api"].health = "degraded"
    supervisor._record_known_good()
    assert not (config.runtime_home / "known-good.json").exists()


def test_previous_clean_supervisor_shutdown_survives_next_start_state(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    ensure_runtime_home(config.runtime_home, config.repository)
    atomic_write_json(config.state_path, {"lastCleanSupervisorShutdown": "2026-01-01T00:00:00Z"})
    supervisor = RuntimeSupervisor(config)
    supervisor._write_state("starting")
    state = read_json(config.state_path)
    assert state is not None
    assert state["lastCleanSupervisorShutdown"] == "2026-01-01T00:00:00Z"


def test_process_start_uses_argument_list_without_shell(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    captured: dict[str, object] = {}

    def factory(argv: list[str], **kwargs: object) -> FakeProcess:
        captured.update(kwargs)
        captured["argv"] = argv
        return FakeProcess(argv)

    supervisor = RuntimeSupervisor(config, process_factory=factory)
    attach_logger(supervisor)
    supervisor._start_process(supervisor.processes["api"])
    assert isinstance(captured["argv"], list)
    assert captured["shell"] is False
    assert captured["cwd"] == config.api_directory


def test_crashed_child_is_scheduled_for_restart(tmp_path: Path) -> None:
    now = [0.0]
    supervisor = RuntimeSupervisor(make_config(tmp_path), clock=lambda: now[0])
    attach_logger(supervisor)
    managed = supervisor.processes["api"]
    process = FakeProcess([])
    process.returncode = 7
    managed.process = process
    supervisor._monitor_once()
    assert managed.process is None
    assert managed.restart_count == 1
    assert managed.next_restart_at > now[0]


def test_health_failure_restarts_only_after_threshold(tmp_path: Path) -> None:
    config = make_config(tmp_path, JARVIS_SUPERVISOR_HEALTH_FAILURE_LIMIT="2")
    supervisor = RuntimeSupervisor(
        config,
        probe=lambda *_args, **_kwargs: HealthResult(False, "unavailable"),
    )
    attach_logger(supervisor)
    managed = supervisor.processes["api"]
    managed.process = FakeProcess([])
    supervisor._monitor_once()
    assert managed.process is not None
    supervisor._monitor_once()
    assert managed.process is None
    assert managed.restart_count == 1
