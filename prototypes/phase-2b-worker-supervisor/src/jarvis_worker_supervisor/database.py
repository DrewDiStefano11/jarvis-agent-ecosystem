from __future__ import annotations

import json
import os
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path
from typing import Any
from uuid import uuid4

from alembic import command
from alembic.config import Config

from jarvis_simulated_worker.scenarios import WorkerScenario

from .enums import SupervisorState, WorkerState
from .schema import DATABASE_REVISION

SUPERVISOR_FIELDS = {
    "status",
    "paused",
    "emergency_stop",
    "current_worker_instance_id",
    "current_worker_pid",
    "current_worker_start_token",
    "last_start_attempt_at",
    "last_successful_ready_at",
    "last_worker_exit_at",
    "restart_attempt_count",
    "restart_window_started_at",
    "crash_loop_detected",
    "next_restart_at",
    "last_error_json",
    "started_at",
    "stopped_at",
}

WORKER_FIELDS = {
    "status",
    "ready_at",
    "last_heartbeat_at",
    "last_heartbeat_sequence",
    "shutdown_requested_at",
    "stopped_at",
    "exit_code",
    "exit_reason",
}

METRIC_FIELDS = {
    "completed_workers",
    "failed_workers",
    "forced_terminations",
    "restart_count",
    "recovery_count",
    "unexpected_error_count",
}


class Database:
    """Prototype-local operational state.

    This database does not contain domain tasks, checkpoints, audit records, or
    outbox events. Those remain owned by the Phase 2A control plane.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = str(Path(db_path).resolve())

    def _get_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=5.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._get_connection()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self, *, immediate: bool = True) -> Iterator[sqlite3.Connection]:
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            try:
                yield connection
            except BaseException:
                connection.rollback()
                raise
            else:
                connection.commit()

    def initialize(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
        config = Config()
        migration_path = files("jarvis_worker_supervisor").joinpath("migrations")
        config.set_main_option("script_location", str(migration_path))
        database_url = f"sqlite:///{Path(self.db_path).as_posix()}"
        config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
        command.upgrade(config, "head")
        with self.transaction() as connection:
            connection.execute("INSERT OR IGNORE INTO runtime_metrics(singleton) VALUES (1)")

    def verify_schema(self) -> None:
        if not Path(self.db_path).is_file():
            raise RuntimeError("Runtime database is not initialized.")
        with self.connection() as connection:
            try:
                row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
            except sqlite3.OperationalError as exc:
                raise RuntimeError("Runtime database schema is missing.") from exc
        if not row or row["version_num"] != DATABASE_REVISION:
            raise RuntimeError(
                f"Runtime database schema is missing or unsupported; expected {DATABASE_REVISION}."
            )

    def ensure_supervisor(self, supervisor_id: str, now: float | None = None) -> None:
        timestamp = time.time() if now is None else now
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO supervisor_state(
                    id, status, paused, emergency_stop, restart_attempt_count,
                    crash_loop_detected, started_at, updated_at
                ) VALUES (?, ?, 0, 0, 0, 0, ?, ?)
                """,
                (supervisor_id, SupervisorState.STARTING.value, timestamp, timestamp),
            )

    def get_supervisor_state(self, supervisor_id: str) -> sqlite3.Row | None:
        with self.connection() as connection:
            return connection.execute(
                "SELECT * FROM supervisor_state WHERE id = ?", (supervisor_id,)
            ).fetchone()

    def update_supervisor(self, supervisor_id: str, **changes: Any) -> None:
        unknown = set(changes) - SUPERVISOR_FIELDS
        if unknown:
            raise ValueError(f"Unknown supervisor fields: {sorted(unknown)}")
        if not changes:
            return
        normalized = {
            name: value.value if isinstance(value, SupervisorState) else value
            for name, value in changes.items()
        }
        normalized["updated_at"] = time.time()
        assignments = ", ".join(f"{name} = ?" for name in normalized)
        values = [*normalized.values(), supervisor_id]
        with self.transaction() as connection:
            cursor = connection.execute(
                f"UPDATE supervisor_state SET {assignments} WHERE id = ?", values
            )
            if cursor.rowcount != 1:
                raise RuntimeError(f"Supervisor state {supervisor_id!r} does not exist.")

    def insert_worker_instance(
        self,
        *,
        instance_id: str,
        pid: int,
        token: str,
        create_time: float,
        executable: str,
        command_line: str,
        scenario: WorkerScenario,
        status: WorkerState,
        stdout_path: Path,
        stderr_path: Path,
        started_at: float | None = None,
    ) -> None:
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO worker_instances(
                    instance_id, pid, process_start_token, process_create_time,
                    executable, command_line,
                    scenario, status, started_at, log_stdout_path, log_stderr_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    instance_id,
                    pid,
                    token,
                    create_time,
                    executable,
                    command_line,
                    scenario.value,
                    status.value,
                    time.time() if started_at is None else started_at,
                    str(stdout_path),
                    str(stderr_path),
                ),
            )

    def get_worker_instance(self, instance_id: str | None) -> sqlite3.Row | None:
        if not instance_id:
            return None
        with self.connection() as connection:
            return connection.execute(
                "SELECT * FROM worker_instances WHERE instance_id = ?", (instance_id,)
            ).fetchone()

    def update_worker(self, instance_id: str, **changes: Any) -> None:
        unknown = set(changes) - WORKER_FIELDS
        if unknown:
            raise ValueError(f"Unknown worker fields: {sorted(unknown)}")
        if not changes:
            return
        normalized = {
            name: value.value if isinstance(value, WorkerState) else value
            for name, value in changes.items()
        }
        assignments = ", ".join(f"{name} = ?" for name in normalized)
        values = [*normalized.values(), instance_id]
        with self.transaction() as connection:
            cursor = connection.execute(
                f"UPDATE worker_instances SET {assignments} WHERE instance_id = ?", values
            )
            if cursor.rowcount != 1:
                raise RuntimeError(f"Worker instance {instance_id!r} does not exist.")

    def acquire_lease(
        self,
        lease_id: str,
        owner_id: str,
        pid: int,
        start_token: str,
        expiration_seconds: float,
        now: float | None = None,
    ) -> bool:
        timestamp = time.time() if now is None else now
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT owner_id, expires_at FROM supervisor_lease WHERE lease_id = ?",
                (lease_id,),
            ).fetchone()
            if row and row["expires_at"] > timestamp and row["owner_id"] != owner_id:
                return False
            acquired_at = timestamp
            if row and row["owner_id"] == owner_id:
                acquired_at_row = connection.execute(
                    "SELECT acquired_at FROM supervisor_lease WHERE lease_id = ?", (lease_id,)
                ).fetchone()
                acquired_at = acquired_at_row["acquired_at"]
            connection.execute(
                """
                INSERT INTO supervisor_lease(
                    lease_id, owner_id, pid, start_token, acquired_at, renewed_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(lease_id) DO UPDATE SET
                    owner_id = excluded.owner_id,
                    pid = excluded.pid,
                    start_token = excluded.start_token,
                    acquired_at = excluded.acquired_at,
                    renewed_at = excluded.renewed_at,
                    expires_at = excluded.expires_at
                """,
                (
                    lease_id,
                    owner_id,
                    pid,
                    start_token,
                    acquired_at,
                    timestamp,
                    timestamp + expiration_seconds,
                ),
            )
        return True

    def release_lease(self, lease_id: str, owner_id: str) -> bool:
        with self.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM supervisor_lease WHERE lease_id = ? AND owner_id = ?",
                (lease_id, owner_id),
            )
            return cursor.rowcount == 1

    def append_event(
        self,
        event_type: str,
        *,
        worker_instance_id: str | None = None,
        previous_state: str | None = None,
        new_state: str | None = None,
        severity: str = "info",
        details: dict[str, Any] | None = None,
        timestamp: float | None = None,
    ) -> None:
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO supervisor_events(
                    event_id, timestamp, event_type, worker_instance_id,
                    previous_state, new_state, severity, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    time.time() if timestamp is None else timestamp,
                    event_type,
                    worker_instance_id,
                    previous_state,
                    new_state,
                    severity,
                    json.dumps(details or {}, sort_keys=True),
                ),
            )

    def bump_metric(self, metric: str, amount: int = 1) -> None:
        if metric not in METRIC_FIELDS:
            raise ValueError(f"Unknown metric: {metric}")
        with self.transaction() as connection:
            connection.execute(
                f"UPDATE runtime_metrics SET {metric} = {metric} + ? WHERE singleton = 1",
                (amount,),
            )

    def metrics(self) -> dict[str, int]:
        with self.connection() as connection:
            row = connection.execute("SELECT * FROM runtime_metrics WHERE singleton = 1").fetchone()
        if not row:
            return {name: 0 for name in sorted(METRIC_FIELDS)}
        return {name: int(row[name]) for name in METRIC_FIELDS}

    def worker_counts(self) -> dict[str, int]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM worker_instances GROUP BY status"
            ).fetchall()
        return {row["status"]: row["count"] for row in rows}

    def event_count(self) -> int:
        with self.connection() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM supervisor_events").fetchone()[0])

    def delete_runtime_state(self) -> None:
        for suffix in ("-wal", "-shm", ""):
            path = Path(f"{self.db_path}{suffix}")
            if path.exists():
                os.remove(path)
