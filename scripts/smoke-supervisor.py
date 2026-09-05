"""Windows supervisor acceptance with an isolated database and owned child processes.

Run with the repository Python environment after installing/building the frontend.
Evidence stays in the printed temporary directory. This test uses no real model.
It rebuilds frontend output for its isolated ports; run pnpm build afterward to
restore the default operator bundle. It never registers an autostart task.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import psutil
from app.runtime_supervisor.ownership import process_identity

ROOT = Path(__file__).resolve().parents[1]
if os.name != "nt":
    raise SystemExit("This acceptance script requires Windows.")
API = ROOT / "apps" / "api"
WEB = ROOT / "apps" / "web"
RUN = Path(tempfile.mkdtemp(prefix="jarvis-supervisor-acceptance-"))
ENV = {
    key: value
    for key, value in os.environ.items()
    if not key.startswith(("JARVIS_", "VITE_"))
    and key not in {"API_HOST", "API_PORT", "WEB_ORIGIN"}
}
ENV.update(
    PYTHONPATH=str(API),
    LOCALAPPDATA=str(RUN / "local-app-data"),
    JARVIS_DATABASE_URL=f"sqlite:///{(RUN / 'runtime.db').as_posix()}",
    JARVIS_SUPERVISOR_RUNTIME_HOME=str(RUN / "runtime"),
    JARVIS_SUPERVISOR_PYTHON_EXECUTABLE=sys.executable,
    JARVIS_SUPERVISOR_NODE_EXECUTABLE=shutil.which("node") or "node",
    API_HOST="127.0.0.1",
    API_PORT="18763",
    JARVIS_SUPERVISOR_WEB_PORT="18764",
    WEB_ORIGIN="http://127.0.0.1:18764",
    JARVIS_AUTONOMOUS_WORKER_ENABLED="false",
    JARVIS_MODEL_EXECUTION_MODE="disabled",
    JARVIS_SUPERVISOR_BACKUP_INTERVAL_HOURS="0",
    JARVIS_SUPERVISOR_HEALTH_INTERVAL_SECONDS="1",
    JARVIS_SUPERVISOR_STARTUP_TIMEOUT_SECONDS="30",
    JARVIS_SUPERVISOR_GRACEFUL_SHUTDOWN_SECONDS="5",
)
(RUN / "local-app-data").mkdir()
REPORT = {"runtime": str(RUN), "events": []}


def record(event, payload):
    REPORT["events"].append({"event": event, "payload": payload})
    (RUN / "supervisor-smoke-report.json").write_text(json.dumps(REPORT, indent=2))
    print(event, json.dumps(payload), flush=True)


def command(args, cwd=API):
    result = subprocess.run(
        args, cwd=cwd, env=ENV, capture_output=True, text=True, timeout=55, check=False
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed {args}: {result.returncode}\n{result.stdout}\n{result.stderr}"
        )
    return result.stdout


def cli(*args):
    return json.loads(
        command(
            [
                sys.executable,
                "-m",
                "app.runtime_supervisor",
                "--repository",
                str(ROOT),
                "--json",
                *args,
            ]
        )
    )


def healthy():
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        value = cli("status")
        if (
            value.get("apiHealth", {}).get("available")
            and value.get("webHealth", {}).get("status") == "healthy"
        ):
            return value
        time.sleep(1)
    raise RuntimeError(f"Runtime did not become healthy: {value}")


try:
    command([sys.executable, "-m", "alembic", "upgrade", "head"])
    command([sys.executable, "-m", "alembic", "downgrade", "20260729_04"])
    command([sys.executable, "-m", "alembic", "upgrade", "head"])
    record("migrations", "blank upgrade, preceding downgrade, re-upgrade passed")
    command(
        [
            ENV["JARVIS_SUPERVISOR_NODE_EXECUTABLE"],
            str(WEB / "node_modules/vite/bin/vite.js"),
            "build",
        ],
        WEB,
    )
    metadata_path = WEB / "dist/runtime-supervisor.json"
    metadata = metadata_path.read_text()
    assert json.loads(metadata)["apiBaseUrl"] == "http://127.0.0.1:18763"
    doctor = cli("doctor")
    assert doctor["status"] != "fail", doctor
    record("doctor", {"status": doctor["status"], "checks": doctor["checks"]})
    started = cli("start")
    assert started["result"] == "started", started
    state = healthy()
    assert state["ownership"] == "running"
    assert state["worker"]["enabled"] is False
    record(
        "healthy",
        {
            "api": state["apiHealth"],
            "web": state["webHealth"],
            "worker": state["worker"],
        },
    )
    managed_api = state["processes"]["api"]
    assert process_identity(managed_api["pid"]) == managed_api["processIdentity"]
    launcher = psutil.Process(managed_api["pid"])
    listeners = [
        child
        for child in launcher.children()
        if child.parent() == launcher
        and child.is_running()
        and any(
            connection.status == psutil.CONN_LISTEN and connection.laddr.port == 18763
            for connection in child.net_connections(kind="tcp")
        )
    ]
    assert len(listeners) == 1, listeners
    assert process_identity(managed_api["pid"]) == managed_api["processIdentity"]
    listeners[0].kill()
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        recovered = cli("status")
        if (
            recovered["processes"]["api"]["restartCount"] > managed_api["restartCount"]
            and recovered["apiHealth"]["available"]
        ):
            assert (
                recovered["processes"]["api"]["processIdentity"]
                != managed_api["processIdentity"]
            )
            record(
                "api-crash-recovery",
                {
                    "restartCount": recovered["processes"]["api"]["restartCount"],
                    "api": recovered["apiHealth"],
                },
            )
            break
        time.sleep(1)
    else:
        raise RuntimeError(f"Owned API did not recover after child crash: {recovered}")
    metadata_path.unlink()
    try:
        repeated = cli("start")
        assert repeated["result"] == "already_running"
        assert repeated["instanceId"] == state["instanceId"]
        record("repeated-start-with-missing-build", repeated["result"])
    finally:
        metadata_path.write_text(metadata)
    with urllib.request.urlopen("http://127.0.0.1:18764", timeout=10) as response:
        assert response.status == 200
    backup = cli("backup")
    assert backup["result"] == "created"
    record("backup", backup)
    with sqlite3.connect(RUN / "runtime.db") as connection:
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    record("autostart-status", cli("autostart", "status"))
    restarted = cli("restart")
    assert restarted["result"] == "started", restarted
    after_restart = healthy()
    assert after_restart["instanceId"] != state["instanceId"]
    record("restart", {"result": restarted["result"], "instanceChanged": True})
finally:
    stopped = cli("stop")
    record("stop", {"result": stopped["result"], "ownership": stopped.get("ownership")})
    final = cli("status")
    assert final["ownership"] == "not_running", final
    record("final", final)
