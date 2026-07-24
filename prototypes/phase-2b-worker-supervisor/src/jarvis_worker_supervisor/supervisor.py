from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

import psutil

from .config import SupervisorConfig
from .database import Database
from .enums import SupervisorState, WorkerState
from .heartbeat import check_heartbeat
from .launcher import launch_worker
from .process_identity import (
    ProcessIdentityStatus,
    generate_instance_id,
    generate_process_start_token,
    inspect_process_identity,
)
from .readiness import check_readiness
from .restart_policy import calculate_backoff
from .retention import enforce_log_retention
from .shutdown import force_terminate_process, request_graceful_shutdown

LOGGER = logging.getLogger(__name__)
Launcher = Callable[
    [str, str, str, object],
    tuple[subprocess.Popen[bytes], Path, Path],
]


class Supervisor:
    def __init__(
        self,
        db: Database,
        config: SupervisorConfig,
        supervisor_id: str = "main",
        *,
        owner_id: str | None = None,
        clock: Callable[[], float] = time.time,
        launcher: Launcher = launch_worker,
    ):
        db.verify_schema()
        self.db = db
        self.config = config
        self.supervisor_id = supervisor_id
        self.owner_id = owner_id or str(uuid4())
        self.owner_token = generate_process_start_token()
        self.clock = clock
        self.launcher = launcher
        self._children: dict[str, subprocess.Popen[bytes]] = {}
        self.db.ensure_supervisor(supervisor_id, self.clock())

    @property
    def state(self):
        state = self.db.get_supervisor_state(self.supervisor_id)
        if state is None:
            raise RuntimeError(f"Supervisor {self.supervisor_id!r} is missing.")
        return state

    def acquire_lease(self) -> bool:
        assert self.config.lease_ttl_seconds is not None
        return self.db.acquire_lease(
            self.supervisor_id,
            self.owner_id,
            os.getpid(),
            self.owner_token,
            self.config.lease_ttl_seconds,
            self.clock(),
        )

    def release_lease(self) -> bool:
        return self.db.release_lease(self.supervisor_id, self.owner_id)

    def _transition(
        self,
        new_state: SupervisorState,
        *,
        event_type: str = "supervisor.state_changed",
        worker_instance_id: str | None = None,
        severity: str = "info",
        details: dict[str, object] | None = None,
        **changes: object,
    ) -> None:
        previous = self.state["status"]
        changes["status"] = new_state
        self.db.update_supervisor(self.supervisor_id, **changes)
        if previous != new_state.value or event_type != "supervisor.state_changed":
            self.db.append_event(
                event_type,
                worker_instance_id=worker_instance_id,
                previous_state=previous,
                new_state=new_state.value,
                severity=severity,
                details=details,
                timestamp=self.clock(),
            )
        LOGGER.info(
            "runtime_transition supervisor=%s previous=%s new=%s worker=%s",
            self.supervisor_id,
            previous,
            new_state.value,
            worker_instance_id,
        )

    def prepare_startup(self) -> None:
        state = self.state
        if state["status"] in {
            SupervisorState.STOPPED.value,
            SupervisorState.FAILED.value,
            SupervisorState.OFFLINE.value,
        }:
            self._transition(
                SupervisorState.STARTING,
                event_type="supervisor.starting",
                stopped_at=None,
                last_error_json=None,
            )

    def start_worker(self) -> bool:
        if not self.acquire_lease():
            return False
        state = self.state
        if (
            state["paused"]
            or state["emergency_stop"]
            or state["crash_loop_detected"]
            or state["status"]
            in {
                SupervisorState.STOPPING.value,
                SupervisorState.STOPPED.value,
                SupervisorState.FAILED.value,
            }
            or state["current_worker_instance_id"]
        ):
            return False
        now = self.clock()
        if state["next_restart_at"] and now < state["next_restart_at"]:
            return False

        instance_id = generate_instance_id()
        start_token = generate_process_start_token()
        self._transition(
            SupervisorState.LAUNCHING,
            event_type="worker.launching",
            worker_instance_id=instance_id,
            last_start_attempt_at=now,
        )

        process = None
        try:
            process, stdout_path, stderr_path = self.launcher(
                self.config.runtime_dir,
                instance_id,
                start_token,
                self.config.scenario,
            )
            ps_process = psutil.Process(process.pid)
            create_time = ps_process.create_time()
            executable = ps_process.exe()
            command_line = " ".join(ps_process.cmdline())
            self._children[instance_id] = process
            self.db.insert_worker_instance(
                instance_id=instance_id,
                pid=process.pid,
                token=start_token,
                create_time=create_time,
                executable=executable,
                command_line=command_line,
                scenario=self.config.scenario,
                status=WorkerState.STARTING,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                started_at=now,
            )
        except BaseException as exc:
            if process:
                force_terminate_process(process.pid, 5.0)
                try:
                    process.wait(timeout=5.0)
                except BaseException:
                    pass
            error = {"type": type(exc).__name__, "message": str(exc)}
            self.db.bump_metric("failed_workers")
            self._transition(
                SupervisorState.DEGRADED,
                event_type="worker.launch_failed",
                worker_instance_id=instance_id,
                severity="error",
                details=error,
                last_error_json=json.dumps(error, sort_keys=True),
            )
            LOGGER.exception("worker_launch_failed instance=%s", instance_id)
            if not isinstance(exc, (OSError, psutil.Error)):
                raise
            return False

        restart_window = state["restart_window_started_at"] or now
        attempts = state["restart_attempt_count"] + 1
        if attempts > 1:
            self.db.bump_metric("restart_count")
        self._transition(
            SupervisorState.WAITING_FOR_READINESS,
            event_type="worker.started",
            worker_instance_id=instance_id,
            current_worker_instance_id=instance_id,
            current_worker_pid=process.pid,
            current_worker_start_token=start_token,
            restart_attempt_count=attempts,
            restart_window_started_at=restart_window,
            next_restart_at=None,
            last_error_json=None,
        )
        return True

    def request_shutdown(self) -> None:
        state = self.state
        worker = self.db.get_worker_instance(state["current_worker_instance_id"])
        if state["status"] == SupervisorState.STOPPED.value:
            return
        self._transition(
            SupervisorState.STOPPING,
            event_type="supervisor.shutdown_requested",
            worker_instance_id=worker["instance_id"] if worker else None,
        )
        if worker:
            request_graceful_shutdown(self.config.runtime_dir, worker["instance_id"])

    def set_paused(self, paused: bool) -> None:
        state = self.state
        if state["emergency_stop"] and not paused:
            raise RuntimeError("Emergency stop must be cleared before resume.")
        worker = self.db.get_worker_instance(state["current_worker_instance_id"])
        if paused:
            target = SupervisorState.PAUSED
        elif worker and worker["status"] in {WorkerState.READY.value, WorkerState.HEALTHY.value}:
            target = SupervisorState.RUNNING
        elif worker:
            target = SupervisorState.WAITING_FOR_READINESS
        else:
            target = SupervisorState.IDLE
        self._transition(
            target,
            event_type="supervisor.paused" if paused else "supervisor.resumed",
            worker_instance_id=worker["instance_id"] if worker else None,
            paused=paused,
        )

    def set_emergency_stop(self, active: bool) -> None:
        state = self.state
        worker = self.db.get_worker_instance(state["current_worker_instance_id"])
        if active:
            self._transition(
                SupervisorState.EMERGENCY_STOPPED,
                event_type="supervisor.emergency_stop",
                worker_instance_id=worker["instance_id"] if worker else None,
                severity="warning",
                emergency_stop=True,
            )
            if worker:
                request_graceful_shutdown(self.config.runtime_dir, worker["instance_id"])
            return
        target = SupervisorState.PAUSED if state["paused"] else SupervisorState.IDLE
        self._transition(
            target,
            event_type="supervisor.emergency_resumed",
            emergency_stop=False,
        )

    def recover(self) -> None:
        if not self.acquire_lease():
            raise RuntimeError("Another supervisor owns the runtime lease.")
        state = self.state
        worker = self.db.get_worker_instance(state["current_worker_instance_id"])
        self._transition(
            SupervisorState.RECOVERING,
            event_type="supervisor.recovery_started",
            worker_instance_id=worker["instance_id"] if worker else None,
        )
        self.db.bump_metric("recovery_count")
        if not worker:
            target = SupervisorState.PAUSED if state["paused"] else SupervisorState.IDLE
            if state["emergency_stop"]:
                target = SupervisorState.EMERGENCY_STOPPED
            self._transition(target, event_type="supervisor.recovery_completed")
            return

        identity = inspect_process_identity(
            self.db,
            worker["instance_id"],
            worker["pid"],
            worker["process_start_token"],
        )
        if identity is ProcessIdentityStatus.VERIFIED:
            if worker["shutdown_requested_at"]:
                target = SupervisorState.STOPPING
            elif worker["status"] in {WorkerState.READY.value, WorkerState.HEALTHY.value}:
                target = SupervisorState.RUNNING
            else:
                target = SupervisorState.WAITING_FOR_READINESS
            self._transition(
                target,
                event_type="supervisor.recovery_completed",
                worker_instance_id=worker["instance_id"],
                details={"processIdentity": identity.value},
            )
            return
        if identity is ProcessIdentityStatus.INACCESSIBLE:
            error = {"code": "worker_identity_inaccessible"}
            self._transition(
                SupervisorState.DEGRADED,
                event_type="supervisor.recovery_blocked",
                worker_instance_id=worker["instance_id"],
                severity="error",
                details=error,
                last_error_json=json.dumps(error),
            )
            return
        status = (
            WorkerState.CRASHED
            if identity is ProcessIdentityStatus.NOT_RUNNING
            else WorkerState.UNKNOWN
        )
        self.db.update_worker(
            worker["instance_id"],
            status=status,
            stopped_at=self.clock(),
            exit_reason=f"recovery_{identity.value}",
        )
        self.db.bump_metric("failed_workers")
        target = (
            SupervisorState.IDLE
            if identity is ProcessIdentityStatus.NOT_RUNNING
            else SupervisorState.FAILED
        )
        self._transition(
            target,
            event_type="supervisor.recovery_reconciled",
            worker_instance_id=worker["instance_id"],
            severity="warning" if target is SupervisorState.IDLE else "error",
            details={"processIdentity": identity.value},
            current_worker_instance_id=None,
            current_worker_pid=None,
            current_worker_start_token=None,
            last_worker_exit_at=self.clock(),
        )

    def _exit_code(self, worker) -> int | None:
        child = self._children.get(worker["instance_id"])
        return child.poll() if child else None

    def _crash_loop_active(self, state, now: float) -> bool:
        started = state["restart_window_started_at"]
        return bool(
            started
            and now - started <= self.config.restart_window_seconds
            and state["restart_attempt_count"] >= self.config.maximum_restarts
        )

    def _finish_worker(
        self,
        worker,
        status: WorkerState,
        *,
        exit_code: int | None,
        reason: str,
    ) -> None:
        now = self.clock()
        self.db.update_worker(
            worker["instance_id"],
            status=status,
            exit_code=exit_code,
            exit_reason=reason,
            stopped_at=now,
        )
        if status in {WorkerState.COMPLETED, WorkerState.STOPPED}:
            self.db.bump_metric("completed_workers")
        else:
            self.db.bump_metric("failed_workers")
        state = self.state
        common = {
            "current_worker_instance_id": None,
            "current_worker_pid": None,
            "current_worker_start_token": None,
            "last_worker_exit_at": now,
        }
        if state["emergency_stop"]:
            target = SupervisorState.EMERGENCY_STOPPED
        elif state["status"] == SupervisorState.STOPPING.value:
            target = SupervisorState.STOPPED
            common["stopped_at"] = now
        elif self._crash_loop_active(state, now) and status is not WorkerState.COMPLETED:
            target = SupervisorState.CRASH_LOOP
            common["crash_loop_detected"] = True
        elif state["paused"]:
            target = SupervisorState.PAUSED
        else:
            target = SupervisorState.IDLE
            common["next_restart_at"] = now + calculate_backoff(
                state["restart_attempt_count"], self.config
            )
        self._transition(
            target,
            event_type="worker.completed"
            if status in {WorkerState.COMPLETED, WorkerState.STOPPED}
            else "worker.failed",
            worker_instance_id=worker["instance_id"],
            severity="info" if status in {WorkerState.COMPLETED, WorkerState.STOPPED} else "error",
            details={"status": status.value, "exitCode": exit_code, "reason": reason},
            **common,
        )
        self._children.pop(worker["instance_id"], None)

    def _handle_shutdown(self, worker) -> None:
        now = self.clock()
        requested_at = worker["shutdown_requested_at"]
        if not requested_at:
            request_graceful_shutdown(self.config.runtime_dir, worker["instance_id"])
            self.db.update_worker(
                worker["instance_id"],
                shutdown_requested_at=now,
            )
            return

        identity = inspect_process_identity(
            self.db,
            worker["instance_id"],
            worker["pid"],
            worker["process_start_token"],
        )
        if identity is ProcessIdentityStatus.NOT_RUNNING:
            failed_health_check = worker["status"] in {
                WorkerState.TIMED_OUT.value,
                WorkerState.UNHEALTHY.value,
            }
            self._finish_worker(
                worker,
                WorkerState.CRASHED if failed_health_check else WorkerState.STOPPED,
                exit_code=self._exit_code(worker),
                reason="health_check_failed" if failed_health_check else "graceful_shutdown",
            )
            return
        if identity is not ProcessIdentityStatus.VERIFIED:
            error = {"code": "worker_identity_unverified", "identity": identity.value}
            self.db.update_worker(
                worker["instance_id"],
                status=WorkerState.UNKNOWN,
                exit_reason=error["code"],
            )
            self._transition(
                SupervisorState.FAILED,
                event_type="worker.identity_unverified",
                worker_instance_id=worker["instance_id"],
                severity="error",
                details=error,
                last_error_json=json.dumps(error, sort_keys=True),
            )
            return
        if now - requested_at < self.config.graceful_shutdown_seconds:
            return
        terminated = force_terminate_process(worker["pid"], self.config.forced_shutdown_seconds)
        if terminated:
            self.db.bump_metric("forced_terminations")
            self._finish_worker(
                worker,
                WorkerState.KILLED,
                exit_code=self._exit_code(worker),
                reason="forced_shutdown",
            )
        else:
            error = {"code": "forced_shutdown_failed"}
            self._transition(
                SupervisorState.FAILED,
                event_type="worker.forced_shutdown_failed",
                worker_instance_id=worker["instance_id"],
                severity="error",
                details=error,
                last_error_json=json.dumps(error),
            )

    def tick(self) -> None:
        if not self.acquire_lease():
            raise RuntimeError("Supervisor lease ownership was lost.")
        enforce_log_retention(self.config.runtime_dir, self.config)
        state = self.state
        worker = self.db.get_worker_instance(state["current_worker_instance_id"])

        if not worker:
            if state["status"] == SupervisorState.STOPPING.value:
                self._transition(
                    SupervisorState.STOPPED,
                    event_type="supervisor.stopped",
                    stopped_at=self.clock(),
                )
            elif (
                not state["paused"]
                and not state["emergency_stop"]
                and not state["crash_loop_detected"]
                and state["status"]
                not in {SupervisorState.STOPPED.value, SupervisorState.FAILED.value}
            ):
                self.start_worker()
            return

        if (
            state["emergency_stop"]
            or state["status"] == SupervisorState.STOPPING.value
            or worker["status"] in {WorkerState.TIMED_OUT.value, WorkerState.UNHEALTHY.value}
        ):
            self._handle_shutdown(worker)
            return

        identity = inspect_process_identity(
            self.db,
            worker["instance_id"],
            worker["pid"],
            worker["process_start_token"],
        )
        if identity is ProcessIdentityStatus.NOT_RUNNING:
            exit_code = self._exit_code(worker)
            completed = exit_code == 0 and worker["status"] in {
                WorkerState.READY.value,
                WorkerState.HEALTHY.value,
            }
            self._finish_worker(
                worker,
                WorkerState.COMPLETED if completed else WorkerState.CRASHED,
                exit_code=exit_code,
                reason="process_exit",
            )
            return
        if identity is not ProcessIdentityStatus.VERIFIED:
            error = {"code": "worker_identity_unverified", "identity": identity.value}
            self.db.update_worker(
                worker["instance_id"],
                status=WorkerState.UNKNOWN,
                exit_reason=error["code"],
            )
            self._transition(
                SupervisorState.FAILED,
                event_type="worker.identity_unverified",
                worker_instance_id=worker["instance_id"],
                severity="error",
                details=error,
                last_error_json=json.dumps(error, sort_keys=True),
            )
            return

        now = self.clock()
        if worker["status"] == WorkerState.STARTING.value:
            readiness = check_readiness(
                self.config.runtime_dir,
                worker["instance_id"],
                worker["process_start_token"],
                not_before=worker["started_at"],
            )
            if readiness:
                self.db.update_worker(
                    worker["instance_id"],
                    status=WorkerState.READY,
                    ready_at=readiness["timestamp"],
                )
                if not state["paused"]:
                    self._transition(
                        SupervisorState.RUNNING,
                        event_type="worker.ready",
                        worker_instance_id=worker["instance_id"],
                        last_successful_ready_at=readiness["timestamp"],
                    )
            elif now - worker["started_at"] > self.config.readiness_timeout_seconds:
                self.db.update_worker(worker["instance_id"], status=WorkerState.TIMED_OUT)
                self._transition(
                    SupervisorState.DEGRADED,
                    event_type="worker.readiness_timeout",
                    worker_instance_id=worker["instance_id"],
                    severity="error",
                )
            return

        minimum_sequence = worker["last_heartbeat_sequence"] or 0
        heartbeat = check_heartbeat(
            self.config.runtime_dir,
            worker["instance_id"],
            worker["process_start_token"],
            minimum_sequence=minimum_sequence,
            not_before=worker["started_at"],
        )
        if heartbeat:
            self.db.update_worker(
                worker["instance_id"],
                status=WorkerState.HEALTHY,
                last_heartbeat_at=heartbeat["timestamp"],
                last_heartbeat_sequence=heartbeat["sequence_number"],
            )
            worker = self.db.get_worker_instance(worker["instance_id"])
        last_signal = worker["last_heartbeat_at"] or worker["ready_at"] or worker["started_at"]
        if now - last_signal > self.config.heartbeat_timeout_seconds:
            self.db.update_worker(worker["instance_id"], status=WorkerState.UNHEALTHY)
            self._transition(
                SupervisorState.DEGRADED,
                event_type="worker.heartbeat_timeout",
                worker_instance_id=worker["instance_id"],
                severity="error",
            )
            return
        if (
            worker["ready_at"]
            and now - worker["ready_at"] > self.config.stable_runtime_seconds
            and state["restart_attempt_count"]
        ):
            self.db.update_supervisor(
                self.supervisor_id,
                restart_attempt_count=0,
                restart_window_started_at=None,
            )
