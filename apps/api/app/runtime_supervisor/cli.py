from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.runtime_supervisor import autostart
from app.runtime_supervisor.backup import create_backup
from app.runtime_supervisor.config import (
    SupervisorConfig,
    SupervisorConfigurationError,
    SupervisorCoordination,
)
from app.runtime_supervisor.doctor import run_doctor
from app.runtime_supervisor.frontend_build import validate_frontend_build
from app.runtime_supervisor.io import (
    atomic_write_json,
    ensure_runtime_home,
    utc_now,
)
from app.runtime_supervisor.status import load_recorded_status, load_status
from app.runtime_supervisor.supervisor import RuntimeSupervisor, shutdown_wait_seconds


def _default_repository() -> Path:
    return Path(__file__).resolve().parents[4]


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Operate the local Jarvis runtime supervisor")
    result.add_argument("--repository", type=Path, default=_default_repository())
    result.add_argument("--json", action="store_true", dest="json_output")
    subcommands = result.add_subparsers(dest="command", required=True)
    subcommands.add_parser("daemon", help=argparse.SUPPRESS)
    subcommands.add_parser("start", help="Start the supervisor")
    subcommands.add_parser("stop", help="Gracefully stop the supervisor and managed processes")
    subcommands.add_parser("restart", help="Gracefully restart the supervisor")
    subcommands.add_parser("status", help="Show supervisor and process health")
    subcommands.add_parser("doctor", help="Validate prerequisites without changing the application")
    subcommands.add_parser("backup", help="Create a consistent SQLite backup")
    auto = subcommands.add_parser("autostart", help="Manage current-user logon startup")
    auto.add_argument("operation", choices=("install", "uninstall", "status"))
    return result


def _emit(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))
        return
    if "checks" in payload:
        print(f"Doctor: {payload['status']}")
        for item in payload["checks"]:
            print(f"  {item['status'].upper():7} {item['name']}: {item['detail']}")
        print(f"Runtime home: {payload['runtimeHome']}")
        return
    if "processes" in payload:
        print(f"Supervisor: {payload.get('supervisorState')} ({payload.get('ownership')})")
        print(
            f"Supervisor PID: {payload.get('pid') or '-'} instance={payload.get('instanceId') or '-'} "
            f"uptime={payload.get('uptimeSeconds', 0):.1f}s"
        )
        print(f"Repository: {payload.get('repository')}")
        print(f"Supervised Git SHA: {payload.get('gitSha') or 'unknown'}")
        print(
            f"Current Git SHA: {payload.get('currentGitSha') or payload.get('gitSha') or 'unknown'}"
        )
        print(f"Runtime home: {payload.get('runtimeHome')}")
        print(f"Coordination home: {payload.get('coordinationHome')}")
        for name, process in payload.get("processes", {}).items():
            print(
                f"  {name}: {process.get('processState')} health={process.get('healthState', 'unknown')} "
                f"pid={process.get('pid') or '-'} restarts={process.get('restartCount', 0)}"
            )
            if process.get("lastFailure"):
                print(f"    last failure: {process['lastFailure']}")
        api_health = payload.get("apiHealth") or {}
        web_health = payload.get("webHealth") or {}
        worker = payload.get("worker") or {}
        ollama = payload.get("ollama") or {}
        print(
            f"API: {api_health.get('status', 'unknown')} "
            f"application={api_health.get('applicationStatus', 'unknown')}"
        )
        print(f"Web: {web_health.get('status', 'unknown')}")
        print(f"Worker: enabled={worker.get('enabled')} status={worker.get('status', 'unknown')}")
        print(f"Ollama: required={ollama.get('required')} status={ollama.get('status', 'unknown')}")
        print(f"Emergency stop: {payload.get('emergencyStop')}")
        disk = payload.get("disk") or {}
        print(
            f"Disk: free={disk.get('freeBytes', 'unknown')} warning={disk.get('warning')} "
            f"critical={disk.get('critical')}"
        )
        backup = payload.get("backup") or {}
        backup_success = backup.get("lastSuccess") if isinstance(backup, dict) else None
        print(
            f"Backup: last={backup_success.get('backupFile') if isinstance(backup_success, dict) else '-'} "
            f"failure={backup.get('lastFailure') if isinstance(backup, dict) else None}"
        )
        known_good = payload.get("knownGood") or {}
        print(
            f"Known good: {known_good.get('lastKnownHealthySha', '-')} at "
            f"{known_good.get('successfulHealthTimestamp', '-')}"
        )
        print(f"Last application clean shutdown: {payload.get('lastApplicationCleanShutdown')}")
        print(f"Last supervisor clean shutdown: {payload.get('lastCleanSupervisorShutdown')}")
        print(f"Logs: {payload.get('logsDirectory')}")
        print(f"Backups: {payload.get('backupsDirectory')}")
        return
    for key, value in payload.items():
        print(f"{key}: {value}")


def _spawn_daemon(config: SupervisorConfig) -> int:
    command = [
        str(config.python_executable),
        "-m",
        "app.runtime_supervisor",
        "--repository",
        str(config.repository),
        "daemon",
    ]
    kwargs: dict[str, Any] = {
        "cwd": config.api_directory,
        "env": dict(config.environment),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen(command, **kwargs)
    return process.pid


def start(config: SupervisorConfig) -> dict[str, Any]:
    validate_frontend_build(config)
    ensure_runtime_home(config.coordination_home, config.repository)
    current = load_status(config)
    if current.get("ownership") == "running":
        return {"result": "already_running", **current}
    if config.runtime_home != config.coordination_home:
        ensure_runtime_home(config.runtime_home, config.repository)
    launcher_pid = _spawn_daemon(config)
    deadline = time.monotonic() + min(config.startup_timeout_seconds, 20)
    while time.monotonic() < deadline:
        time.sleep(0.1)
        current = load_status(config)
        if current.get("ownership") == "running":
            return {"result": "started", "launcherPid": launcher_pid, **current}
    return {
        "result": "start_pending",
        "launcherPid": launcher_pid,
        "runtimeHome": str(config.runtime_home),
        "detail": "supervisor did not publish running state before the operator timeout",
    }


def stop(config: SupervisorConfig | SupervisorCoordination) -> dict[str, Any]:
    current = load_recorded_status(config)
    if current.get("ownership") != "running":
        return {"result": "already_stopped", **current}
    instance_id = current.get("instanceId")
    if not isinstance(instance_id, str):
        return {"result": "refused", "detail": "running state lacks an instance identity"}
    atomic_write_json(
        config.stop_request_path,
        {"kind": "jarvis-supervisor-stop", "instanceId": instance_id, "requestedAt": utc_now()},
    )
    configured_wait = current.get("shutdownWaitSeconds")
    if isinstance(configured_wait, (int, float)) and 1 <= configured_wait <= 3600:
        wait_seconds = float(configured_wait)
    elif isinstance(config, SupervisorConfig):
        wait_seconds = shutdown_wait_seconds(config)
    else:
        wait_seconds = 1225
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        time.sleep(0.2)
        current = load_recorded_status(config)
        if current.get("ownership") != "running":
            return {"result": "stopped", **current}
    return {
        "result": "stop_pending",
        "detail": "supervisor did not confirm shutdown; no unrelated process was terminated",
        **load_recorded_status(config),
    }


def restart(config: SupervisorConfig) -> dict[str, Any]:
    validate_frontend_build(config)
    stopped = stop(config)
    if stopped.get("result") == "stop_pending":
        return stopped
    return start(config)


def _autostart_payload(value: autostart.AutostartStatus) -> dict[str, Any]:
    return {
        "supported": value.supported,
        "installed": value.installed,
        "taskName": value.task_name,
        "detail": value.detail,
        "trigger": "current-user logon",
        "storesPassword": False,
    }


def _autostart(config: SupervisorConfig, operation: str) -> dict[str, Any]:
    if operation == "install":
        value = autostart.install(config)
    elif operation == "uninstall":
        value = autostart.uninstall(config)
    else:
        value = autostart.status(config)
    return _autostart_payload(value)


def main(argv: Sequence[str] | None = None) -> int:
    raw_arguments = list(sys.argv[1:] if argv is None else argv)
    json_requested = "--json" in raw_arguments
    raw_arguments = [item for item in raw_arguments if item != "--json"]
    args = parser().parse_args(raw_arguments)
    args.json_output = args.json_output or json_requested
    try:
        if args.command == "stop":
            payload = stop(SupervisorCoordination.load(args.repository))
            _emit(payload, as_json=args.json_output)
            return 1 if payload.get("result") in {"refused", "stop_pending"} else 0
        if args.command == "status":
            payload = load_recorded_status(SupervisorCoordination.load(args.repository))
            _emit(payload, as_json=args.json_output)
            return 0
        if args.command == "autostart" and args.operation in {"status", "uninstall"}:
            coordination = SupervisorCoordination.load(args.repository)
            value = (
                autostart.uninstall(coordination)
                if args.operation == "uninstall"
                else autostart.status(coordination)
            )
            payload = _autostart_payload(value)
            _emit(payload, as_json=args.json_output)
            return 0
        config = SupervisorConfig.load(args.repository)
        if args.command == "daemon":
            return RuntimeSupervisor(config).run()
        if args.command == "start":
            payload = start(config)
        elif args.command == "restart":
            payload = restart(config)
        elif args.command == "doctor":
            payload = run_doctor(config)
        elif args.command == "backup":
            ensure_runtime_home(config.runtime_home, config.repository)
            payload = {"result": "created", "backup": create_backup(config)}
        else:
            payload = _autostart(config, args.operation)
        _emit(payload, as_json=args.json_output)
        if payload.get("status") == "fail" or payload.get("result") in {
            "refused",
            "start_pending",
            "stop_pending",
        }:
            return 1
        return 0
    except (SupervisorConfigurationError, OSError, RuntimeError) as exc:
        payload = {"error": exc.__class__.__name__, "detail": str(exc)}
        _emit(payload, as_json=args.json_output)
        return 2


if __name__ == "__main__":
    sys.exit(main())
