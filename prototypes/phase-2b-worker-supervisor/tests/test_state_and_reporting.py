from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import psutil
import pytest

from jarvis_simulated_worker.heartbeat import emit_heartbeat
from jarvis_simulated_worker.scenarios import WorkerScenario
from jarvis_simulated_worker.state import mark_ready
from jarvis_worker_supervisor.config import SupervisorConfig
from jarvis_worker_supervisor.enums import SupervisorState, WorkerState
from jarvis_worker_supervisor.heartbeat import check_heartbeat
from jarvis_worker_supervisor.process_identity import (
    ProcessIdentityStatus,
    inspect_process_identity,
)
from jarvis_worker_supervisor.readiness import check_readiness
from jarvis_worker_supervisor.reporting import generate_status_report
from jarvis_worker_supervisor.retention import enforce_log_retention


def test_readiness_rejects_wrong_token_stale_and_malformed(runtime_dir: Path) -> None:
    mark_ready(str(runtime_dir), "worker", "token", "healthy")
    assert check_readiness(str(runtime_dir), "worker", "token")
    assert not check_readiness(str(runtime_dir), "worker", "wrong")
    assert not check_readiness(str(runtime_dir), "worker", "token", not_before=10**20)
    ready = runtime_dir / "state" / "ready_worker.json"
    ready.write_text("{", encoding="utf-8")
    assert not check_readiness(str(runtime_dir), "worker", "token")


def test_heartbeat_requires_monotonic_sequence(runtime_dir: Path) -> None:
    emit_heartbeat(str(runtime_dir), "worker", "token", 2)
    assert check_heartbeat(str(runtime_dir), "worker", "token", minimum_sequence=1)
    assert not check_heartbeat(str(runtime_dir), "worker", "token", minimum_sequence=2)


def test_log_retention_preserves_newest_bytes(runtime_dir: Path) -> None:
    log = runtime_dir / "logs" / "worker.log"
    log.write_bytes(b"A" * 100 + b"B" * 100)
    config = SupervisorConfig(
        runtime_dir=str(runtime_dir),
        scenario=WorkerScenario.LOG_FLOOD,
        max_log_bytes=100,
    )
    assert enforce_log_retention(str(runtime_dir), config) == 1
    assert log.read_bytes() == b"B" * 50


def _insert_identity(database, create_time: float = 1000.0) -> None:
    database.insert_worker_instance(
        instance_id="worker",
        pid=1234,
        token="token",
        create_time=create_time,
        executable="fake",
        command_line="fake",
        scenario=WorkerScenario.HEALTHY,
        status=WorkerState.STARTING,
        stdout_path=Path("out"),
        stderr_path=Path("err"),
    )


@patch("jarvis_worker_supervisor.process_identity.psutil.Process")
def test_process_identity_verifies_creation_time(process_class: MagicMock, database) -> None:
    process = process_class.return_value
    process.is_running.return_value = True
    process.status.return_value = psutil.STATUS_RUNNING
    process.create_time.return_value = 1000.0
    process.exe.return_value = "fake"
    process.cmdline.return_value = ["fake"]
    _insert_identity(database)
    assert (
        inspect_process_identity(database, "worker", 1234, "token")
        is ProcessIdentityStatus.VERIFIED
    )
    process.create_time.return_value = 2000.0
    assert (
        inspect_process_identity(database, "worker", 1234, "token")
        is ProcessIdentityStatus.MISMATCH
    )


@pytest.mark.parametrize(
    ("exception", "expected"),
    [
        (psutil.NoSuchProcess(1234), ProcessIdentityStatus.NOT_RUNNING),
        (psutil.AccessDenied(1234), ProcessIdentityStatus.INACCESSIBLE),
    ],
)
@patch("jarvis_worker_supervisor.process_identity.psutil.Process")
def test_process_identity_fails_closed(
    process_class: MagicMock,
    exception: Exception,
    expected: ProcessIdentityStatus,
    database,
) -> None:
    process_class.side_effect = exception
    _insert_identity(database)
    assert inspect_process_identity(database, "worker", 1234, "token") is expected


@pytest.mark.parametrize(
    ("state", "health"),
    [
        (SupervisorState.STARTING, "starting"),
        (SupervisorState.RECOVERING, "recovering"),
        (SupervisorState.IDLE, "healthy"),
        (SupervisorState.DEGRADED, "degraded"),
        (SupervisorState.PAUSED, "degraded"),
        (SupervisorState.STOPPED, "stopped"),
        (SupervisorState.FAILED, "failed"),
    ],
)
def test_health_report_maps_runtime_state(database, state, health) -> None:
    database.ensure_supervisor("main", now=10)
    database.update_supervisor("main", status=state)
    report = generate_status_report(database, "main", now=20)
    assert report["health"] == health
    assert report["ownership"]["eventPublication"] == "phase-2a-transactional-outbox"
    assert report["supervisor"]["uptimeSeconds"] == 10
