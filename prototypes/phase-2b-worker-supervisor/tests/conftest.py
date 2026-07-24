from __future__ import annotations

from pathlib import Path

import psutil
import pytest

from jarvis_worker_supervisor.database import Database
from jarvis_worker_supervisor.enums import TERMINAL_WORKER_STATES


@pytest.fixture
def runtime_dir(tmp_path: Path) -> Path:
    runtime = tmp_path / "runtime"
    (runtime / "state").mkdir(parents=True)
    (runtime / "logs").mkdir()
    return runtime


@pytest.fixture
def database(runtime_dir: Path):
    db = Database(runtime_dir / "state" / "supervisor.db")
    db.initialize()
    yield db
    with db._get_connection() as connection:
        workers = connection.execute("SELECT pid, status FROM worker_instances").fetchall()
    for worker in workers:
        if worker["status"] in TERMINAL_WORKER_STATES:
            continue
        try:
            process = psutil.Process(worker["pid"])
            process.kill()
            process.wait(timeout=2)
        except (psutil.Error, OSError):
            pass
