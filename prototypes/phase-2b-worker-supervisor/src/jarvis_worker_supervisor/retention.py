from pathlib import Path
from .config import SupervisorConfig
import os

def enforce_log_retention(runtime_dir: str, config: SupervisorConfig):
    logs_dir = Path(runtime_dir) / "logs"
    if not logs_dir.exists():
        return

    for log_file in logs_dir.glob("*.log"):
        try:
            if log_file.stat().st_size > config.max_log_bytes:
                # Simple truncation to preserve bounded limits.
                # Avoid deleting active file lock via truncation
                with open(log_file, "r+", encoding="utf-8") as f:
                    f.truncate(config.max_log_bytes // 2)
        except Exception:
            pass
