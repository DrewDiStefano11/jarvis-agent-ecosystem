import time
import os
import psutil
from .config import SupervisorConfig
from .enums import SupervisorState, WorkerState
from .process_identity import verify_process_identity, generate_process_start_token, generate_instance_id
from .launcher import launch_worker
from .readiness import check_readiness
from .heartbeat import check_heartbeat
from .shutdown import request_graceful_shutdown, force_terminate_process
from .restart_policy import calculate_backoff
from .crash_loop import check_crash_loop
from .retention import enforce_log_retention
from .timing import get_current_time_utc
from .errors import WorkerIdentityUnverified

class Supervisor:
    def __init__(self, db, config: SupervisorConfig, supervisor_id: str = "main"):
        self.db = db
        self.config = config
        self.supervisor_id = supervisor_id
        self._initialize_state()

    def _initialize_state(self):
        state = self.db.get_supervisor_state(self.supervisor_id)
        if not state:
            self.db.set_supervisor_state(
                id=self.supervisor_id,
                state=SupervisorState.IDLE,
                paused=False,
                emergency_stop=False,
                crash_loop_detected=False,
                restart_attempt_count=0
            )

    def is_process_running(self, pid: int) -> bool:
        try:
            return psutil.pid_exists(pid)
        except Exception:
            return False

    def get_process_exit_code(self, pid: int):
        try:
            proc = psutil.Process(pid)
            if proc.status() == psutil.STATUS_ZOMBIE:
                return proc.wait(timeout=0)
        except psutil.NoSuchProcess:
            return -1 # Default code if missing
        except psutil.TimeoutExpired:
            pass
        except psutil.AccessDenied:
            return -1
        return None

    def acquire_lease(self) -> bool:
        return self.db.acquire_lease(
            self.supervisor_id,
            os.getpid(),
            "supervisor_token",
            expiration_seconds=self.config.watchdog_interval_seconds * 3
        )

    def start_worker(self):
        if not self.acquire_lease():
            return

        state = self.db.get_supervisor_state(self.supervisor_id)
        if state['paused'] or state['emergency_stop'] or state['crash_loop_detected']:
            return

        now = get_current_time_utc()

        # Check next_restart_at
        next_restart = state['next_restart_at']
        if next_restart and now < next_restart:
            return

        instance_id = generate_instance_id()
        start_token = generate_process_start_token()

        proc, _, _ = launch_worker(self.config.runtime_dir, instance_id, start_token, self.config.scenario)

        try:
            create_time = psutil.Process(proc.pid).create_time()
        except psutil.NoSuchProcess:
            create_time = time.time()

        self.db.insert_worker_instance(
            instance_id=instance_id,
            pid=proc.pid,
            token=start_token,
            create_time=create_time,
            scenario=self.config.scenario,
            status=WorkerState.STARTING
        )

        # Update window start if this is the first attempt
        if state['restart_attempt_count'] == 0:
            with self.db._get_connection() as conn:
                conn.execute("UPDATE supervisor_state SET restart_window_started_at = ? WHERE id = ?", (now, self.supervisor_id))

        self.db.set_supervisor_state(
            id=self.supervisor_id,
            state=SupervisorState.WAITING_FOR_READINESS,
            paused=state['paused'],
            emergency_stop=state['emergency_stop'],
            crash_loop_detected=state['crash_loop_detected'],
            restart_attempt_count=state['restart_attempt_count'] + 1,
            current_worker_instance_id=instance_id,
            current_worker_pid=proc.pid,
            current_worker_start_token=start_token
        )

        with self.db._get_connection() as conn:
            conn.execute("UPDATE supervisor_state SET last_start_attempt_at = ? WHERE id = ?", (now, self.supervisor_id))

    def _mark_worker_stopped(self, worker, state, new_status: WorkerState, exit_code: int = None):
        self.db.update_worker_status(worker['instance_id'], new_status, exit_code)

        if check_crash_loop(self.db, self.supervisor_id, self.config):
            self.db.set_supervisor_state(
                id=self.supervisor_id,
                state=SupervisorState.CRASH_LOOP,
                paused=state['paused'],
                emergency_stop=state['emergency_stop'],
                crash_loop_detected=True,
                restart_attempt_count=state['restart_attempt_count'],
                current_worker_instance_id=None,
                current_worker_pid=None,
                current_worker_start_token=None
            )
        else:
            delay = calculate_backoff(state['restart_attempt_count'], self.config)
            now = get_current_time_utc()

            self.db.set_supervisor_state(
                id=self.supervisor_id,
                state=SupervisorState.IDLE,
                paused=state['paused'],
                emergency_stop=state['emergency_stop'],
                crash_loop_detected=False,
                restart_attempt_count=state['restart_attempt_count'],
                current_worker_instance_id=None,
                current_worker_pid=None,
                current_worker_start_token=None
            )
            with self.db._get_connection() as conn:
                conn.execute("UPDATE supervisor_state SET next_restart_at = ? WHERE id = ?", (now + delay, self.supervisor_id))

    def _handle_shutdown_sequence(self, worker, state):
        now = get_current_time_utc()
        req_at = worker['shutdown_requested_at']

        if not req_at:
            request_graceful_shutdown(self.config.runtime_dir, worker['instance_id'])
            with self.db._get_connection() as conn:
                conn.execute("UPDATE worker_instances SET shutdown_requested_at = ? WHERE instance_id = ?", (now, worker['instance_id']))
            return

        if not self.is_process_running(worker['pid']):
            self._mark_worker_stopped(worker, state, WorkerState.STOPPED, 0)
            return

        if (now - req_at) > self.config.graceful_shutdown_seconds:
            # Recheck identity before kill
            if verify_process_identity(self.db, worker['instance_id'], worker['pid'], worker['process_start_token']):
                force_terminate_process(worker['pid'])
            else:
                print("worker_identity_unverified")
                # Do not terminate process. Reconcile state as unknown.
                self._mark_worker_stopped(worker, state, WorkerState.UNKNOWN, -1)
                return

            # Wait for forced shutdown
            if (now - req_at) > (self.config.graceful_shutdown_seconds + self.config.forced_shutdown_seconds):
                self._mark_worker_stopped(worker, state, WorkerState.KILLED, -1)


    def tick(self):
        self.acquire_lease()
        enforce_log_retention(self.config.runtime_dir, self.config)

        state = self.db.get_supervisor_state(self.supervisor_id)
        if not state:
            return

        now = get_current_time_utc()

        worker_id = state['current_worker_instance_id']
        worker = self.db.get_worker_instance(worker_id) if worker_id else None

        if state['emergency_stop']:
            if worker and worker['status'] not in (WorkerState.STOPPED.value, WorkerState.CRASHED.value, WorkerState.KILLED.value):
                self._handle_shutdown_sequence(worker, state)
            return

        if state['status'] == SupervisorState.CRASH_LOOP.value:
            return

        if state['status'] == SupervisorState.IDLE.value:
            if not state['paused']:
                self.start_worker()
            return

        if not worker:
            self.db.set_supervisor_state(self.supervisor_id, SupervisorState.IDLE, state['paused'], state['emergency_stop'], state['crash_loop_detected'], state['restart_attempt_count'])
            return

        # Verify identity occasionally on tick if it's supposed to be running
        if not verify_process_identity(self.db, worker['instance_id'], worker['pid'], worker['process_start_token']):
            if self.is_process_running(worker['pid']):
                # Identity mismatch! But PID exists. Treat as PID reuse, or lost identity.
                self._mark_worker_stopped(worker, state, WorkerState.UNKNOWN, -1)
                return

        # Stable runtime reset
        if worker['status'] == WorkerState.HEALTHY.value and worker['ready_at']:
            if (now - worker['ready_at']) > self.config.stable_runtime_seconds:
                if state['restart_attempt_count'] > 0:
                    self.db.set_supervisor_state(
                        id=self.supervisor_id,
                        state=SupervisorState(state['status']),
                        paused=state['paused'],
                        emergency_stop=state['emergency_stop'],
                        crash_loop_detected=False,
                        restart_attempt_count=0,
                        current_worker_instance_id=state['current_worker_instance_id'],
                        current_worker_pid=state['current_worker_pid'],
                        current_worker_start_token=state['current_worker_start_token']
                    )
                    with self.db._get_connection() as conn:
                        conn.execute("UPDATE supervisor_state SET restart_window_started_at = NULL WHERE id = ?", (self.supervisor_id,))
                        state = self.db.get_supervisor_state(self.supervisor_id) # reload state

        # If process exited
        if not self.is_process_running(worker['pid']):
            exit_code = self.get_process_exit_code(worker['pid'])
            if worker['status'] == WorkerState.HEALTHY.value and exit_code == 0:
                self._mark_worker_stopped(worker, state, WorkerState.COMPLETED, exit_code)
            else:
                self._mark_worker_stopped(worker, state, WorkerState.CRASHED, exit_code)
            return

        if state['status'] == SupervisorState.WAITING_FOR_READINESS.value:
            r = check_readiness(self.config.runtime_dir, worker['instance_id'], worker['process_start_token'])
            if r:
                self.db.update_worker_status(worker['instance_id'], WorkerState.READY)

                with self.db._get_connection() as conn:
                    conn.execute("UPDATE worker_instances SET ready_at = ? WHERE instance_id = ?", (now, worker['instance_id']))
                    conn.execute("UPDATE supervisor_state SET status = ? WHERE id = ?", (SupervisorState.RUNNING.value, self.supervisor_id))
            else:
                if (now - worker['started_at']) > self.config.readiness_timeout_seconds:
                    self.db.update_worker_status(worker['instance_id'], WorkerState.TIMED_OUT)
                    with self.db._get_connection() as conn:
                        conn.execute("UPDATE supervisor_state SET status = ? WHERE id = ?", (SupervisorState.STOPPING.value, self.supervisor_id))

        if state['status'] == SupervisorState.RUNNING.value:
            h = check_heartbeat(self.config.runtime_dir, worker['instance_id'], worker['process_start_token'])
            if h:
                with self.db._get_connection() as conn:
                    conn.execute("UPDATE worker_instances SET last_heartbeat_at = ?, status = ? WHERE instance_id = ?", (h['timestamp'], WorkerState.HEALTHY.value, worker['instance_id']))

            last_hb = worker['last_heartbeat_at'] or worker['ready_at'] or worker['started_at']
            if (now - last_hb) > self.config.heartbeat_timeout_seconds:
                self.db.update_worker_status(worker['instance_id'], WorkerState.UNHEALTHY)
                with self.db._get_connection() as conn:
                    conn.execute("UPDATE supervisor_state SET status = ? WHERE id = ?", (SupervisorState.STOPPING.value, self.supervisor_id))

        if state['status'] == SupervisorState.STOPPING.value:
            self._handle_shutdown_sequence(worker, state)
