"""Shared fixtures for runtime-doctor tests.

The fixtures build an isolated repository-shaped tree (so supervisor
configuration and migration-head discovery work), a real migrated SQLite
database, a scripted HTTP probe, and helper payloads that mirror the live API
contracts. Everything is deterministic and loopback-only.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from fastapi import FastAPI

from app.diagnostics.probes import HealthResult
from app.main import DATABASE_REVISION, create_app
from app.runtime_supervisor.config import _repository_digest
from app.runtime_supervisor.ownership import process_identity

API_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = API_ROOT.parents[1]


def build_repository(tmp_path: Path) -> Path:
    """Create a minimal repository the doctor and supervisor config accept."""

    repository = tmp_path / "fixture-repository"
    (repository / "apps" / "api" / "app").mkdir(parents=True)
    (repository / "apps" / "api" / "app" / "main.py").write_text("", encoding="utf-8")
    shutil.copytree(
        API_ROOT / "migrations",
        repository / "apps" / "api" / "migrations",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    try:
        subprocess.run(
            ["git", "init", "--quiet"],
            cwd=repository,
            check=True,
            capture_output=True,
            timeout=15,
        )
        subprocess.run(
            [
                "git",
                "-c",
                "user.email=doctor@test.local",
                "-c",
                "user.name=Doctor Test",
                "commit",
                "--quiet",
                "--allow-empty",
                "-m",
                "fixture",
            ],
            cwd=repository,
            check=True,
            capture_output=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        pass  # identity check degrades; the other checks remain deterministic
    return repository


def migrate_database(path: Path, revision: str = "head") -> Path:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{path.as_posix()}")
    command.upgrade(config, revision)
    return path


def build_seeded_database(tmp_path: Path) -> Path:
    """A database migrated and initialized exactly like the running app."""

    path = tmp_path / "seeded-runtime.db"
    app: FastAPI = create_app(
        database_url=f"sqlite:///{path.as_posix()}", recover_interrupted_workflow=False
    )
    app.state.engine.dispose()
    return path


def base_environment(tmp_path: Path, repository: Path, database: Path) -> dict[str, str]:
    return {
        "LOCALAPPDATA": str(tmp_path / "localappdata"),
        "JARVIS_DATABASE_URL": f"sqlite:///{database.as_posix()}",
        "JARVIS_AUTO_MIGRATE": "false",
    }


class ScriptedProbe:
    """Deterministic HTTP probe returning scripted HealthResults by URL."""

    def __init__(self, responses: Mapping[str, HealthResult | Exception] | None = None) -> None:
        self.responses: dict[str, HealthResult | Exception] = dict(responses or {})
        self.calls: list[str] = []

    def __call__(self, url: str, expect_json: bool, timeout: float) -> HealthResult:
        self.calls.append(url)
        response = self.responses.get(url)
        if isinstance(response, Exception):
            raise response
        if response is None:
            return HealthResult(False, "unavailable", "ConnectionRefusedError")
        return response

    def set(self, url: str, result: HealthResult | Exception) -> None:
        self.responses[url] = result


def api_health_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": "healthy",
        "service": "jarvis-simulator-api",
        "processAlive": True,
        "databaseReachable": True,
        "schemaCurrent": True,
        "outboxDispatcherRunning": True,
        "outboxExhaustedCount": 0,
        "recoveryRequired": False,
        "contextAssemblerReady": True,
        "activeWorkerCount": 0,
        "activeLeaseCount": 0,
        "expiredLeaseCount": 0,
        "staleWorkerCount": 0,
        "runtimePersistence": {"status": "healthy"},
        "autonomousWorker": {
            "enabled": False,
            "modelExecutionMode": "disabled",
            "workerActorId": None,
            "activeExecutionCount": 0,
            "queuedEligibleRuntimeCount": 0,
            "providerReady": False,
            "status": "disabled",
            "reasonCode": None,
        },
    }
    payload.update(overrides)
    return payload


def system_status_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": "healthy",
        "environment": "development",
        "emergencyStop": False,
        "databaseRevision": DATABASE_REVISION,
        "schemaCurrent": True,
        "recoveryRequired": False,
        "outboxPendingCount": 0,
        "outboxExhaustedCount": 0,
        "activeWorkerCount": 0,
        "activeLeaseCount": 0,
        "expiredLeaseCount": 0,
        "staleWorkerCount": 0,
        "lastCleanShutdown": None,
        "autonomousWorker": {
            "enabled": False,
            "modelExecutionMode": "disabled",
            "status": "disabled",
            "reasonCode": None,
        },
    }
    payload.update(overrides)
    return payload


def office_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "serverTime": "2026-09-08T12:00:00Z",
        "catalog": {"version": "1.0", "stations": [], "routes": [], "spriteIds": []},
        "placements": [],
        "placementVersions": {},
        "emergencyStop": False,
    }
    payload.update(overrides)
    return payload


def process_payload(
    name: str,
    *,
    state: str = "running",
    health: str = "healthy",
    enabled: bool | None = None,
    **extra: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "configured": True,
        "enabled": enabled if enabled is not None else name != "autonomous_worker",
        "required": name in {"api", "web"},
        "processState": state,
        "pid": os.getpid(),
        "processIdentity": process_identity(os.getpid()),
        "healthState": health,
        "healthDetail": None,
        "restartCount": 0,
        "consecutiveFailures": 0,
        "nextRestartInSeconds": 0,
        "lastFailure": None,
        "failureHistory": [],
        "lastExitCode": None,
        "startedAt": "2026-09-08T12:00:00Z",
    }
    payload.update(extra)
    return payload


def write_supervisor_state(
    repository: Path,
    environment: Mapping[str, str],
    *,
    supervisor_state: str = "running",
    pid: int | None = None,
    processes: dict[str, dict[str, Any]] | None = None,
    emergency_stop: bool | None = None,
) -> Path:
    """Write a supervisor coordination state.json exactly like the daemon."""

    digest = _repository_digest(repository)
    state_directory = Path(environment["LOCALAPPDATA"]) / "Jarvis" / "Supervisor" / digest
    state_directory.mkdir(parents=True, exist_ok=True)
    effective_pid = os.getpid() if pid is None else pid
    state: dict[str, Any] = {
        "schemaVersion": 1,
        "supervisorState": supervisor_state,
        "pid": effective_pid,
        "processIdentity": process_identity(effective_pid),
        "instanceId": "doctor-test-instance",
        "repository": str(repository),
        "gitSha": None,
        "runtimeHome": str(state_directory),
        "coordinationHome": str(state_directory),
        "startedAt": "2026-09-08T12:00:00Z",
        "uptimeSeconds": 12.0,
        "shutdownWaitSeconds": 90,
        "updatedAt": "2026-09-08T12:00:12Z",
        "processes": processes
        or {
            "api": process_payload("api"),
            "web": process_payload("web"),
            "autonomous_worker": process_payload(
                "autonomous_worker", enabled=False, state="disabled", health="disabled"
            ),
        },
        "apiHealth": {"available": True, "status": "healthy", "applicationStatus": "healthy"},
        "webHealth": {"status": "healthy"},
        "worker": {"enabled": False, "status": "disabled"},
        "ollama": {"required": False, "status": "not_required"},
        "emergencyStop": emergency_stop,
        "lastApplicationCleanShutdown": None,
        "lastCleanSupervisorShutdown": None,
        "backup": {"lastSuccess": None, "lastFailure": None},
        "disk": {"freeBytes": 1000000000, "warning": False, "critical": False},
        "logsDirectory": str(state_directory / "logs"),
        "backupsDirectory": str(state_directory / "backups"),
        "knownGood": None,
    }
    state_path = state_directory / "state.json"
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return state_path


def marked_workspace(tmp_path: Path, alias: str = "reports") -> Path:
    """Create an operator-marked workspace directory accepted by the tools."""

    workspace = tmp_path / "workspaces" / alias
    (workspace / "inputs").mkdir(parents=True, exist_ok=True)
    (workspace / "reports").mkdir(parents=True, exist_ok=True)
    (workspace / ".jarvis-workspace.json").write_text(
        json.dumps(
            {
                "schemaVersion": "1.0",
                "workspaceId": alias,
                "readPrefixes": ["inputs"],
                "writePrefixes": ["reports"],
                "allowedTools": [
                    "workspace.list",
                    "workspace.read",
                    "workspace.write",
                    "workspace.report",
                ],
            }
        ),
        encoding="utf-8",
    )
    return workspace
