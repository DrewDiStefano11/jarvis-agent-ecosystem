from __future__ import annotations

from pathlib import Path

import psutil


def request_graceful_shutdown(runtime_dir: str, instance_id: str) -> Path:
    state_dir = Path(runtime_dir) / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    stop_file = state_dir / f"stop_{instance_id}"
    stop_file.touch(exist_ok=True)
    return stop_file


def force_terminate_process(pid: int, timeout_seconds: float) -> bool:
    try:
        process = psutil.Process(pid)
        process.kill()
        process.wait(timeout=timeout_seconds)
    except psutil.NoSuchProcess:
        return True
    except (psutil.AccessDenied, psutil.TimeoutExpired):
        return False
    return True
