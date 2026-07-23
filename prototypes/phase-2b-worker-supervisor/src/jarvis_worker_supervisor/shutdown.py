import sys
import subprocess
import signal
from pathlib import Path
import psutil

def request_graceful_shutdown(runtime_dir: str, instance_id: str):
    stop_file = Path(runtime_dir) / "state" / f"stop_{instance_id}"
    stop_file.touch(exist_ok=True)

def force_terminate_process(pid: int):
    try:
        proc = psutil.Process(pid)
        proc.kill()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
