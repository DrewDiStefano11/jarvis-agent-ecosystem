from __future__ import annotations

import os
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.runtime_supervisor import autostart
from app.runtime_supervisor.config import SupervisorConfig
from app.runtime_supervisor.frontend_build import inspect_frontend_build
from app.runtime_supervisor.health import probe_http
from app.runtime_supervisor.io import read_json, verified_runtime_home
from app.runtime_supervisor.ownership import process_identity, state_ownership
from app.runtime_supervisor.status import load_status


def _check(name: str, status: str, detail: str) -> dict[str, str]:
    return {"name": name, "status": status, "detail": detail}


def _port_available(host: str, port: int) -> bool:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _git(repository: Path, *args: str) -> str | None:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=repository,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _version(executable: Path | str | None) -> str | None:
    if executable is None:
        return None
    try:
        completed = subprocess.run(
            [str(executable), "--version"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return (completed.stdout or completed.stderr).strip() or None


def run_doctor(config: SupervisorConfig) -> dict[str, Any]:
    checks: list[dict[str, str]] = []
    checks.append(
        _check(
            "operating_system",
            "pass" if os.name == "nt" else "warning",
            "Windows supported"
            if os.name == "nt"
            else "non-Windows test mode; auto-start unavailable",
        )
    )
    checks.append(_check("repository", "pass", str(config.repository)))
    python_version = _version(config.python_executable)
    checks.append(
        _check(
            "python",
            "pass" if config.python_executable.is_file() and python_version else "fail",
            f"{config.python_executable} ({python_version or 'unavailable'})",
        )
    )
    node_version = _version(config.node_executable)
    checks.append(
        _check(
            "node",
            "pass"
            if config.node_executable and config.node_executable.is_file() and node_version
            else "fail",
            f"{config.node_executable} ({node_version or 'unavailable'})"
            if config.node_executable
            else "not found",
        )
    )
    pnpm = shutil.which("pnpm.cmd") or shutil.which("pnpm")
    checks.append(_check("pnpm", "pass" if pnpm else "fail", pnpm or "not found"))
    vite = config.web_directory / "node_modules" / "vite" / "bin" / "vite.js"
    checks.append(_check("frontend_dependencies", "pass" if vite.is_file() else "fail", str(vite)))
    web_build = config.web_directory / "dist" / "index.html"
    checks.append(
        _check("frontend_build", "pass" if web_build.is_file() else "fail", str(web_build))
    )
    frontend_endpoints_valid, frontend_endpoints_detail = inspect_frontend_build(config)
    checks.append(
        _check(
            "frontend_build_endpoints",
            "pass" if frontend_endpoints_valid else "fail",
            frontend_endpoints_detail,
        )
    )
    database_parent = config.database_path.parent
    checks.append(
        _check(
            "database",
            "pass" if database_parent.exists() else "warning",
            str(config.database_path),
        )
    )
    runtime_parent = next(
        (
            parent
            for parent in [config.runtime_home, *config.runtime_home.parents]
            if parent.exists()
        ),
        None,
    )
    writable = bool(runtime_parent and os.access(runtime_parent, os.W_OK))
    if config.runtime_home.exists() and not verified_runtime_home(
        config.runtime_home, config.repository
    ):
        writable = False
    checks.append(_check("runtime_home", "pass" if writable else "fail", str(config.runtime_home)))
    current = load_status(config)
    running = current.get("ownership") == "running"
    processes = current.get("processes")
    process_states = processes if isinstance(processes, dict) else {}
    for label, process_name, host, port in (
        ("api_port", "api", config.api_host, config.api_port),
        ("web_port", "web", config.web_host, config.web_port),
    ):
        available = _port_available(host, port)
        process = process_states.get(process_name)
        child_owned = False
        if running and isinstance(process, dict):
            pid = process.get("pid")
            identity = process.get("processIdentity")
            child_owned = (
                process.get("processState") == "running"
                and isinstance(pid, int)
                and isinstance(identity, str)
                and process_identity(pid) == identity
            )
        status = "pass" if available or child_owned else "fail"
        detail = (
            f"owned by running supervisor child {process_name}"
            if child_owned and not available
            else "available"
            if available
            else "in use"
        )
        checks.append(_check(label, status, f"{host}:{port} {detail}"))
    checks.append(_check("api_bind", "pass", f"loopback-only {config.api_host}"))
    checks.append(_check("web_bind", "pass", f"loopback-only {config.web_host}"))
    try:
        Settings.model_validate(config.environment)
        application_configuration = "pass"
        application_detail = "existing application settings validate"
    except (TypeError, ValueError):
        application_configuration = "fail"
        application_detail = "existing application settings validation failed (details redacted)"
    checks.append(
        _check("application_configuration", application_configuration, application_detail)
    )
    checks.append(
        _check(
            "autonomous_worker",
            "pass",
            "explicitly enabled" if config.worker_enabled else "disabled by configuration",
        )
    )
    if config.ollama_relevant:
        ollama = probe_http(config.ollama_url, timeout=2)
        checks.append(
            _check(
                "ollama",
                "pass" if ollama.available else "warning",
                f"{config.ollama_url} {ollama.status}",
            )
        )
    else:
        checks.append(_check("ollama", "pass", "not required by current configuration"))
    sha = _git(config.repository, "rev-parse", "HEAD")
    dirty = _git(config.repository, "status", "--porcelain")
    checks.append(_check("git_sha", "pass" if sha else "warning", sha or "unavailable"))
    checks.append(
        _check(
            "worktree",
            "pass" if dirty == "" else "warning",
            "clean" if dirty == "" else "local changes present",
        )
    )
    disk_root = runtime_parent or config.repository
    free = shutil.disk_usage(disk_root).free
    disk_status = (
        "fail"
        if free < config.disk_critical_bytes
        else "warning"
        if free < config.disk_warning_bytes
        else "pass"
    )
    checks.append(_check("disk_space", disk_status, f"{free} bytes free"))
    startup = autostart.status(config)
    checks.append(
        _check(
            "autostart",
            "pass" if startup.supported else "warning",
            startup.detail,
        )
    )
    return {
        "status": "fail"
        if any(item["status"] == "fail" for item in checks)
        else "warning"
        if any(item["status"] == "warning" for item in checks)
        else "pass",
        "checks": checks,
        "runtimeHome": str(config.runtime_home),
        "logsDirectory": str(config.logs_directory),
        "backupsDirectory": str(config.backups_directory),
        "supervisorOwnership": state_ownership(read_json(config.state_path)),
    }
