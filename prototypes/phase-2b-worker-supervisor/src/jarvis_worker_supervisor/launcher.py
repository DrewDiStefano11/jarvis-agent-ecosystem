import subprocess
import sys
import os
from typing import Tuple
from pathlib import Path
from jarvis_simulated_worker.scenarios import WorkerScenario

def launch_worker(runtime_dir: str, instance_id: str, start_token: str, scenario: WorkerScenario) -> Tuple[subprocess.Popen, Path, Path]:
    logs_dir = Path(runtime_dir) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    stdout_path = logs_dir / f"worker-{instance_id}.stdout.log"
    stderr_path = logs_dir / f"worker-{instance_id}.stderr.log"

    stdout_file = open(stdout_path, "a", encoding="utf-8")
    stderr_file = open(stderr_path, "a", encoding="utf-8")

    cmd = [
        sys.executable,
        "-m", "jarvis_simulated_worker",
        "--runtime-dir", runtime_dir,
        "--scenario", scenario.value,
        "--instance-id", instance_id,
        "--start-token", start_token
    ]

    process = subprocess.Popen(
        cmd,
        shell=False,
        stdout=stdout_file,
        stderr=stderr_file,
        cwd=os.path.abspath(os.path.join(runtime_dir, "..", ".."))
    )

    return process, stdout_path, stderr_path
