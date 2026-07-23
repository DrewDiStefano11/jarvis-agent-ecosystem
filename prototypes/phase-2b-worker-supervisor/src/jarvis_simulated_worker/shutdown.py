import time
import sys
import os
import signal
from pathlib import Path

shutdown_requested = False

def handle_shutdown(signum, frame):
    global shutdown_requested
    shutdown_requested = True
    print("Received shutdown signal.", flush=True)

def setup_signals(ignore: bool = False):
    if ignore:
        if sys.platform != "win32":
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            signal.signal(signal.SIGINT, signal.SIG_IGN)
        return

    if sys.platform == "win32":
        try:
            signal.signal(signal.SIGBREAK, handle_shutdown)
        except AttributeError:
            signal.signal(signal.SIGINT, handle_shutdown)
    else:
        signal.signal(signal.SIGTERM, handle_shutdown)
        signal.signal(signal.SIGINT, handle_shutdown)

def check_shutdown_file(runtime_dir: str, instance_id: str) -> bool:
    global shutdown_requested
    if shutdown_requested:
        return True

    stop_file = Path(runtime_dir) / "state" / f"stop_{instance_id}"
    if stop_file.exists():
        shutdown_requested = True
        print("Received stop file.", flush=True)
        return True
    return False
