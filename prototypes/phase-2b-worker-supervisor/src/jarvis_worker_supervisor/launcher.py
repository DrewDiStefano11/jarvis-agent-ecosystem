from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from jarvis_simulated_worker.scenarios import WorkerScenario


def launch_worker(
    runtime_dir: str,
    instance_id: str,
    start_token: str,
    scenario: WorkerScenario,
) -> tuple[subprocess.Popen[bytes], Path, Path]:
    """Launch only the bundled deterministic worker.

    No user-controlled executable, shell fragment, environment assignment, or
    working directory is accepted by this boundary.
    """

    logs_dir = Path(runtime_dir) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = logs_dir / f"worker-{instance_id}.stdout.log"
    stderr_path = logs_dir / f"worker-{instance_id}.stderr.log"
    command = [
        sys.executable,
        "-m",
        "jarvis_simulated_worker",
        "--runtime-dir",
        str(Path(runtime_dir).resolve()),
        "--scenario",
        scenario.value,
        "--instance-id",
        instance_id,
        "--start-token",
        start_token,
    ]
    environment = os.environ.copy()
    environment["PYTHONUNBUFFERED"] = "1"
    with (
        stdout_path.open("ab", buffering=0) as stdout_file,
        stderr_path.open("ab", buffering=0) as stderr_file,
    ):
        kwargs = {}
        if sys.platform != "win32":
            kwargs["start_new_session"] = True
        else:
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

        process = subprocess.Popen(
            command,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=stdout_file,
            stderr=stderr_file,
            env=environment,
            close_fds=True,
            **kwargs
        )
    return process, stdout_path, stderr_path
