from __future__ import annotations

import json
from pathlib import Path

from app.runtime_supervisor.config import SupervisorConfig, SupervisorConfigurationError

METADATA_FILE = "runtime-supervisor.json"


def metadata_path(config: SupervisorConfig) -> Path:
    return config.web_directory / "dist" / METADATA_FILE


def inspect_frontend_build(config: SupervisorConfig) -> tuple[bool, str]:
    path = metadata_path(config)
    try:
        metadata = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, f"missing or invalid build metadata: {path}"
    if not isinstance(metadata, dict) or metadata.get("schemaVersion") != 1:
        return False, f"unsupported build metadata: {path}"
    expected = {
        "apiBaseUrl": config.environment["VITE_API_BASE_URL"],
        "webSocketUrl": config.environment["VITE_WS_URL"],
    }
    mismatches = [name for name, value in expected.items() if metadata.get(name) != value]
    if mismatches:
        return False, f"frontend build endpoint mismatch ({', '.join(mismatches)}); run pnpm build"
    return True, f"build endpoints match supervised API: {path}"


def validate_frontend_build(config: SupervisorConfig) -> None:
    valid, detail = inspect_frontend_build(config)
    if not valid:
        raise SupervisorConfigurationError(detail)
