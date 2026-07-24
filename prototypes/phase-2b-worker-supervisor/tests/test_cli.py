from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def run_cli(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "jarvis_worker_supervisor", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


def test_cli_help() -> None:
    result = run_cli("--help", cwd=Path.cwd())
    assert result.returncode == 0
    assert "Jarvis Worker Supervisor" in result.stdout


def test_cli_requires_init_and_reports_status(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    missing = run_cli("status", "--runtime-dir", str(runtime), cwd=Path.cwd())
    assert missing.returncode == 2
    initialized = run_cli("init", "--runtime-dir", str(runtime), cwd=Path.cwd())
    assert initialized.returncode == 0
    status = run_cli("status", "--runtime-dir", str(runtime), cwd=Path.cwd())
    assert status.returncode == 0
    report = json.loads(status.stdout)
    assert report["health"] == "stopped"
    assert report["ownership"]["tasks"] == "phase-2a-control-plane"


def test_cli_rejects_unknown_config_and_unsafe_clean(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    assert run_cli("init", "--runtime-dir", str(runtime), cwd=Path.cwd()).returncode == 0
    config = tmp_path / "bad.json"
    config.write_text('{"unknown": true}', encoding="utf-8")
    bad = run_cli(
        "simulate",
        "--runtime-dir",
        str(runtime),
        "--config",
        str(config),
        cwd=Path.cwd(),
    )
    assert bad.returncode == 2
    refused = run_cli("clean", "--runtime-dir", str(runtime), cwd=Path.cwd())
    assert refused.returncode == 2
    cleaned = run_cli("clean", "--runtime-dir", str(runtime), "--yes", cwd=Path.cwd())
    assert cleaned.returncode == 0


def test_cli_simulation_completes(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    assert run_cli("init", "--runtime-dir", str(runtime), cwd=Path.cwd()).returncode == 0
    result = run_cli(
        "simulate",
        "--runtime-dir",
        str(runtime),
        "--scenario",
        "complete-and-exit",
        "--duration-seconds",
        "4",
        cwd=Path.cwd(),
    )
    assert result.returncode == 0, result.stderr
