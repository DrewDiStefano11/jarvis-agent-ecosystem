from __future__ import annotations

from pathlib import Path

import psutil


def request_graceful_shutdown(runtime_dir: str, instance_id: str) -> Path:
    state_dir = Path(runtime_dir) / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    stop_file = state_dir / f"stop_{instance_id}"
    stop_file.touch(exist_ok=True)
    return stop_file


import os
import signal
import sys

def force_terminate_process(pid: int, timeout_seconds: float) -> bool:
    try:
        process = psutil.Process(pid)
        # Try graceful terminate first, sending to process group if possible
        if sys.platform != "win32":
            try:
                pgid = os.getpgid(pid)
                if pgid == os.getpgrp():
                    process.terminate()
                else:
                    os.killpg(pgid, signal.SIGTERM)
            except OSError:
                process.terminate()
        else:
            try:
                os.kill(pid, getattr(signal, "CTRL_BREAK_EVENT", signal.SIGTERM))
            except (AttributeError, OSError):
                process.terminate()

        try:
            process.wait(timeout=timeout_seconds / 2.0)
        except psutil.TimeoutExpired:
            pass

        if process.is_running():
            if sys.platform != "win32":
                try:
                    pgid = os.getpgid(pid)
                    if pgid == os.getpgrp():
                        process.kill()
                    else:
                        os.killpg(pgid, signal.SIGKILL)
                except OSError:
                    process.kill()
            else:
                process.kill()
            process.wait(timeout=timeout_seconds / 2.0)
    except psutil.NoSuchProcess:
        return True
    except (psutil.AccessDenied, psutil.TimeoutExpired):
        return False
    return True
