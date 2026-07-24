from __future__ import annotations

import logging
from pathlib import Path

from .config import SupervisorConfig

LOGGER = logging.getLogger(__name__)


def enforce_log_retention(runtime_dir: str, config: SupervisorConfig) -> int:
    logs_dir = Path(runtime_dir) / "logs"
    if not logs_dir.exists():
        return 0
    retained_bytes = max(1, config.max_log_bytes // 2)
    truncated = 0
    for log_file in logs_dir.glob("*.log"):
        try:
            size = log_file.stat().st_size
            if size <= config.max_log_bytes:
                continue
            with log_file.open("r+b") as stream:
                stream.seek(-retained_bytes, 2)
                tail = stream.read()
                stream.seek(0)
                stream.write(tail)
                stream.truncate()
            truncated += 1
        except OSError as exc:
            LOGGER.warning("log_retention_failed path=%s error=%s", log_file, exc)
    return truncated
