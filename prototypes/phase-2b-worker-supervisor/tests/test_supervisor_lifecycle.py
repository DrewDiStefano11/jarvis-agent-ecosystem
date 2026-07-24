from __future__ import annotations

import time

import pytest

from jarvis_simulated_worker.scenarios import WorkerScenario
from jarvis_worker_supervisor.config import SupervisorConfig
from jarvis_worker_supervisor.enums import TERMINAL_WORKER_STATES, SupervisorState, WorkerState
from jarvis_worker_supervisor.supervisor import Supervisor
from jarvis_worker_supervisor.watchdog import Watchdog


def config(runtime_dir, scenario: WorkerScenario, **overrides) -> SupervisorConfig:
    values = {
        "runtime_dir": str(runtime_dir),
        "scenario": scenario,
        "readiness_timeout_seconds": 2,
        "heartbeat_timeout_seconds": 1,
        "graceful_shutdown_seconds": 0.2,
        "forced_shutdown_seconds": 1,
        "watchdog_interval_seconds": 0.05,
        "lease_ttl_seconds": 1,
        "restart_window_seconds": 5,
        "initial_backoff_seconds": 0.05,
        "maximum_backoff_seconds": 0.1,
        "stable_runtime_seconds": 0.2,
    }
    values.update(overrides)
    return SupervisorConfig(**values)


def pump(supervisor: Supervisor, predicate, timeout: float = 4) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        supervisor.tick()
        if predicate():
            return
        time.sleep(0.025)
    raise AssertionError(f"Condition not reached; state={dict(supervisor.state)}")


def stop(supervisor: Supervisor) -> None:
    supervisor.request_shutdown()
    pump(
        supervisor,
        lambda: supervisor.state["status"] == SupervisorState.STOPPED.value,
    )
    supervisor.release_lease()


def test_idle_start_ready_healthy_and_graceful_shutdown(database, runtime_dir) -> None:
    supervisor = Supervisor(database, config(runtime_dir, WorkerScenario.HEALTHY))
    supervisor.recover()
    pump(
        supervisor,
        lambda: (
            database.get_worker_instance(supervisor.state["current_worker_instance_id"])["status"]
            == WorkerState.HEALTHY.value
        ),
    )
    assert supervisor.state["status"] == SupervisorState.RUNNING.value
    stop(supervisor)
    assert supervisor.state["status"] == SupervisorState.STOPPED.value
    assert database.metrics()["completed_workers"] == 1


def test_complete_and_exit_is_recorded(database, runtime_dir) -> None:
    supervisor = Supervisor(database, config(runtime_dir, WorkerScenario.COMPLETE_AND_EXIT))
    supervisor.recover()
    supervisor.tick()
    worker_id = supervisor.state["current_worker_instance_id"]
    pump(
        supervisor,
        lambda: database.get_worker_instance(worker_id)["status"] in TERMINAL_WORKER_STATES,
    )
    worker = database.get_worker_instance(worker_id)
    assert worker["status"] == WorkerState.COMPLETED.value
    supervisor.request_shutdown()
    supervisor.tick()
    assert supervisor.state["status"] == SupervisorState.STOPPED.value


def test_crash_loop_stops_duplicate_restarts(database, runtime_dir) -> None:
    supervisor = Supervisor(
        database,
        config(
            runtime_dir,
            WorkerScenario.CRASH_IMMEDIATELY,
            maximum_restarts=2,
        ),
    )
    supervisor.recover()
    pump(
        supervisor,
        lambda: supervisor.state["status"] == SupervisorState.CRASH_LOOP.value,
        timeout=5,
    )
    assert supervisor.state["crash_loop_detected"] == 1
    assert database.metrics()["failed_workers"] >= 2
    supervisor.release_lease()


def test_readiness_timeout_forces_hung_worker_down(database, runtime_dir) -> None:
    supervisor = Supervisor(
        database,
        config(
            runtime_dir,
            WorkerScenario.HANG_BEFORE_READY,
            readiness_timeout_seconds=0.3,
        ),
    )
    supervisor.recover()
    pump(
        supervisor,
        lambda: database.metrics()["failed_workers"] == 1,
        timeout=4,
    )
    assert database.metrics()["failed_workers"] == 1
    assert database.metrics()["forced_terminations"] == 0
    supervisor.request_shutdown()
    supervisor.tick()


def test_heartbeat_timeout_stops_unhealthy_worker(database, runtime_dir) -> None:
    supervisor = Supervisor(
        database,
        config(
            runtime_dir,
            WorkerScenario.STOP_HEARTBEATS,
            heartbeat_timeout_seconds=0.3,
        ),
    )
    supervisor.recover()
    pump(
        supervisor,
        lambda: database.metrics()["failed_workers"] == 1,
        timeout=4,
    )
    assert database.metrics()["failed_workers"] == 1
    assert database.metrics()["forced_terminations"] == 0
    supervisor.request_shutdown()
    supervisor.tick()


def test_ignore_shutdown_requires_forced_termination(database, runtime_dir) -> None:
    supervisor = Supervisor(
        database,
        config(runtime_dir, WorkerScenario.IGNORE_SHUTDOWN),
    )
    supervisor.recover()
    pump(supervisor, lambda: bool(supervisor.state["current_worker_instance_id"]))
    supervisor.request_shutdown()
    pump(
        supervisor,
        lambda: database.metrics()["forced_terminations"] == 1,
        timeout=4,
    )
    assert supervisor.state["status"] == SupervisorState.STOPPED.value


def test_emergency_stop_prevents_restart(database, runtime_dir) -> None:
    supervisor = Supervisor(database, config(runtime_dir, WorkerScenario.HEALTHY))
    supervisor.recover()
    pump(supervisor, lambda: bool(supervisor.state["current_worker_instance_id"]))
    supervisor.set_emergency_stop(True)
    pump(
        supervisor,
        lambda: supervisor.state["current_worker_instance_id"] is None,
    )
    assert supervisor.state["status"] == SupervisorState.EMERGENCY_STOPPED.value
    supervisor.tick()
    assert supervisor.state["current_worker_instance_id"] is None
    supervisor.release_lease()


def test_duplicate_supervisor_is_rejected(database, runtime_dir) -> None:
    first = Supervisor(
        database,
        config(runtime_dir, WorkerScenario.HEALTHY),
        owner_id="first",
    )
    second = Supervisor(
        database,
        config(runtime_dir, WorkerScenario.HEALTHY),
        owner_id="second",
    )
    assert first.acquire_lease()
    assert not second.acquire_lease()
    first.release_lease()


def test_launch_failure_is_visible_and_recoverable(database, runtime_dir) -> None:
    def fail_launch(*_args):
        raise OSError("injected launch failure")

    supervisor = Supervisor(
        database,
        config(runtime_dir, WorkerScenario.HEALTHY),
        launcher=fail_launch,
    )
    supervisor.recover()
    supervisor.tick()
    assert supervisor.state["status"] == SupervisorState.DEGRADED.value
    assert supervisor.state["current_worker_instance_id"] is None
    assert database.metrics()["failed_workers"] == 1
    assert database.event_count() >= 4
    supervisor.release_lease()


def test_live_worker_is_recovered_without_duplicate_launch(database, runtime_dir) -> None:
    first = Supervisor(
        database,
        config(runtime_dir, WorkerScenario.HEALTHY),
        owner_id="before-restart",
    )
    first.recover()
    pump(
        first,
        lambda: (
            database.get_worker_instance(first.state["current_worker_instance_id"])["status"]
            == WorkerState.HEALTHY.value
        ),
    )
    worker_id = first.state["current_worker_instance_id"]
    first.release_lease()

    recovered = Supervisor(
        database,
        config(runtime_dir, WorkerScenario.HEALTHY),
        owner_id="after-restart",
    )
    recovered.recover()
    assert recovered.state["status"] == SupervisorState.RUNNING.value
    assert recovered.state["current_worker_instance_id"] == worker_id
    assert database.worker_counts()[WorkerState.HEALTHY.value] == 1
    stop(recovered)


def test_unexpected_watchdog_exception_marks_runtime_failed(database, runtime_dir) -> None:
    supervisor = Supervisor(database, config(runtime_dir, WorkerScenario.HEALTHY))

    def fail_tick() -> None:
        raise RuntimeError("injected watchdog failure")

    supervisor.tick = fail_tick
    with pytest.raises(RuntimeError, match="injected watchdog failure"):
        Watchdog(supervisor).start()
    assert supervisor.state["status"] == SupervisorState.FAILED.value
    assert database.metrics()["unexpected_error_count"] == 1


def test_recovery_reconciles_missing_process(database, runtime_dir) -> None:
    first = Supervisor(database, config(runtime_dir, WorkerScenario.HEALTHY))
    database.update_supervisor(
        "main",
        status=SupervisorState.RUNNING,
        current_worker_instance_id="missing",
        current_worker_pid=999999,
        current_worker_start_token="token",
    )
    database.insert_worker_instance(
        instance_id="missing",
        pid=999999,
        token="token",
        create_time=1,
        scenario=WorkerScenario.HEALTHY,
        status=WorkerState.HEALTHY,
        stdout_path=runtime_dir / "logs" / "out",
        stderr_path=runtime_dir / "logs" / "err",
    )
    first.recover()
    assert database.get_worker_instance("missing")["status"] == WorkerState.CRASHED.value
    assert first.state["status"] == SupervisorState.IDLE.value
    assert database.metrics()["recovery_count"] == 1
    first.release_lease()


def test_multiple_start_stop_cycles(database, runtime_dir) -> None:
    for index in range(2):
        supervisor = Supervisor(
            database,
            config(runtime_dir, WorkerScenario.HEALTHY),
            owner_id=f"owner-{index}",
        )
        supervisor.prepare_startup()
        supervisor.recover()
        pump(
            supervisor,
            lambda current=supervisor: (
                database.get_worker_instance(current.state["current_worker_instance_id"])["status"]
                == WorkerState.HEALTHY.value
            ),
        )
        stop(supervisor)
    assert database.metrics()["completed_workers"] == 2
