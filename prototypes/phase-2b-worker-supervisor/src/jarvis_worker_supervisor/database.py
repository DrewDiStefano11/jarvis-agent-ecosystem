import sqlite3
import time
import os
from pathlib import Path
from .schema import SCHEMA
from .enums import SupervisorState, WorkerState
from jarvis_simulated_worker.scenarios import WorkerScenario

class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self):
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            conn.executescript(SCHEMA)

    def set_supervisor_state(self, id: str, state: SupervisorState, paused: bool, emergency_stop: bool, crash_loop_detected: bool, restart_attempt_count: int,
                             current_worker_instance_id: str = None, current_worker_pid: int = None, current_worker_start_token: str = None):
        with self._get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO supervisor_state
                (id, status, paused, emergency_stop, crash_loop_detected, restart_attempt_count,
                 current_worker_instance_id, current_worker_pid, current_worker_start_token, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (id, state.value, paused, emergency_stop, crash_loop_detected, restart_attempt_count,
                  current_worker_instance_id, current_worker_pid, current_worker_start_token, time.time()))

    def get_supervisor_state(self, id: str):
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM supervisor_state WHERE id = ?", (id,))
            return cursor.fetchone()

    def insert_worker_instance(self, instance_id: str, pid: int, token: str, create_time: float, scenario: WorkerScenario, status: WorkerState):
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO worker_instances (instance_id, pid, process_start_token, process_create_time, scenario, status, started_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (instance_id, pid, token, create_time, scenario.value, status.value, time.time()))

    def update_worker_status(self, instance_id: str, status: WorkerState, exit_code: int = None):
        with self._get_connection() as conn:
            conn.execute("""
                UPDATE worker_instances SET status = ?, exit_code = ?, stopped_at = ? WHERE instance_id = ?
            """, (status.value, exit_code, time.time() if status in (WorkerState.STOPPED, WorkerState.CRASHED, WorkerState.KILLED) else None, instance_id))

    def get_worker_instance(self, instance_id: str):
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM worker_instances WHERE instance_id = ?", (instance_id,))
            return cursor.fetchone()

    def acquire_lease(self, supervisor_id: str, pid: int, start_token: str, expiration_seconds: float = 30.0) -> bool:
        now = time.time()
        with self._get_connection() as conn:
            # Check for valid active lease
            cursor = conn.execute("SELECT supervisor_id, expires_at FROM supervisor_lease WHERE lease_id = 'main'")
            row = cursor.fetchone()
            if row and row['expires_at'] > now and row['supervisor_id'] != supervisor_id:
                return False

            # Acquire or renew lease
            conn.execute("""
                INSERT OR REPLACE INTO supervisor_lease (lease_id, supervisor_id, pid, start_token, acquired_at, expires_at)
                VALUES ('main', ?, ?, ?, ?, ?)
            """, (supervisor_id, pid, start_token, now, now + expiration_seconds))
            return True
