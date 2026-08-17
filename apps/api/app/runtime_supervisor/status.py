from __future__ import annotations

import shutil
import subprocess
from typing import Any

from app.runtime_supervisor.backup import last_backup
from app.runtime_supervisor.config import SupervisorConfig
from app.runtime_supervisor.io import read_json
from app.runtime_supervisor.ownership import state_ownership


def _git_sha(config: SupervisorConfig) -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=config.repository,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def load_status(config: SupervisorConfig) -> dict[str, Any]:
    state = read_json(config.state_path)
    if state is None:
        current_sha = _git_sha(config)
        disk_root = next(
            (path for path in [config.runtime_home, *config.runtime_home.parents] if path.exists()),
            config.repository,
        )
        disk = shutil.disk_usage(disk_root)
        return {
            "supervisorState": "not_running",
            "ownership": "not_running",
            "pid": None,
            "instanceId": None,
            "uptimeSeconds": 0,
            "repository": str(config.repository),
            "runtimeHome": str(config.runtime_home),
            "gitSha": current_sha,
            "currentGitSha": current_sha,
            "processes": {
                "api": {"configured": True, "enabled": True, "processState": "not_running"},
                "web": {"configured": True, "enabled": True, "processState": "not_running"},
                "autonomous_worker": {
                    "configured": True,
                    "enabled": config.worker_enabled,
                    "processState": "not_running" if config.worker_enabled else "disabled",
                },
            },
            "worker": {
                "enabled": config.worker_enabled,
                "status": "not_running" if config.worker_enabled else "disabled",
            },
            "ollama": {"required": config.ollama_relevant, "status": "unknown"},
            "apiHealth": {"available": False, "status": "unknown", "applicationStatus": None},
            "webHealth": {"status": "unknown"},
            "emergencyStop": None,
            "backup": {"lastSuccess": last_backup(config), "lastFailure": None},
            "disk": {
                "freeBytes": disk.free,
                "warning": disk.free < config.disk_warning_bytes,
                "critical": disk.free < config.disk_critical_bytes,
            },
            "knownGood": read_json(config.runtime_home / "known-good.json"),
            "lastApplicationCleanShutdown": None,
            "lastCleanSupervisorShutdown": None,
            "logsDirectory": str(config.logs_directory),
            "backupsDirectory": str(config.backups_directory),
        }
    declared = state.get("supervisorState")
    ownership = "not_running" if declared == "stopped" else state_ownership(state)
    result = dict(state)
    result["currentGitSha"] = _git_sha(config)
    result["ownership"] = ownership
    if ownership == "stale" and declared not in {"stopped", "failed"}:
        result["supervisorState"] = "stale"
    return result
