from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_simulated_worker.scenarios import WorkerScenario
from jarvis_worker_supervisor.config import SupervisorConfig
from jarvis_worker_supervisor.database import Database
from jarvis_worker_supervisor.enums import SupervisorState
from jarvis_worker_supervisor.restart_policy import calculate_backoff
from jarvis_worker_supervisor.schema import DATABASE_REVISION


def test_configuration_normalizes_path_and_scenario(tmp_path: Path) -> None:
    config = SupervisorConfig(runtime_dir=str(tmp_path), scenario="healthy")
    assert config.runtime_dir == str(tmp_path.resolve())
    assert config.scenario is WorkerScenario.HEALTHY
    assert config.lease_ttl_seconds == config.watchdog_interval_seconds * 3


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("readiness_timeout_seconds", 0),
        ("heartbeat_timeout_seconds", -1),
        ("watchdog_interval_seconds", 0),
        ("max_log_bytes", 0),
        ("maximum_restarts", -1),
    ],
)
def test_configuration_rejects_invalid_values(tmp_path: Path, field: str, value: float) -> None:
    with pytest.raises(ValueError):
        SupervisorConfig(
            runtime_dir=str(tmp_path),
            scenario=WorkerScenario.HEALTHY,
            **{field: value},
        )


def test_configuration_rejects_invalid_backoff_and_lease(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="maximum_backoff"):
        SupervisorConfig(
            runtime_dir=str(tmp_path),
            scenario=WorkerScenario.HEALTHY,
            initial_backoff_seconds=2,
            maximum_backoff_seconds=1,
        )
    with pytest.raises(ValueError, match="lease_ttl"):
        SupervisorConfig(
            runtime_dir=str(tmp_path),
            scenario=WorkerScenario.HEALTHY,
            watchdog_interval_seconds=1,
            lease_ttl_seconds=1,
        )


def test_backoff_is_bounded(tmp_path: Path) -> None:
    config = SupervisorConfig(
        runtime_dir=str(tmp_path),
        scenario=WorkerScenario.HEALTHY,
        initial_backoff_seconds=1,
        maximum_backoff_seconds=5,
    )
    assert [calculate_backoff(i, config) for i in range(5)] == [0, 1, 2, 4, 5]


def test_database_requires_explicit_initialization(runtime_dir: Path) -> None:
    database = Database(runtime_dir / "state" / "missing.db")
    with pytest.raises(RuntimeError, match="not initialized"):
        database.verify_schema()


def test_database_initialization_is_idempotent(database: Database) -> None:
    database.initialize()
    database.verify_schema()
    with database._get_connection() as connection:
        version = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    assert version == DATABASE_REVISION


def test_legacy_prototype_database_is_upgraded(runtime_dir: Path) -> None:
    database = Database(runtime_dir / "state" / "legacy.db")
    with database.connection() as connection:
        connection.executescript(
            """
            CREATE TABLE supervisor_state (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                paused INTEGER NOT NULL DEFAULT 0,
                emergency_stop INTEGER NOT NULL DEFAULT 0,
                current_worker_instance_id TEXT,
                current_worker_pid INTEGER,
                current_worker_start_token TEXT,
                last_start_attempt_at REAL,
                last_successful_ready_at REAL,
                last_worker_exit_at REAL,
                restart_attempt_count INTEGER DEFAULT 0,
                restart_window_started_at REAL,
                crash_loop_detected INTEGER DEFAULT 0,
                next_restart_at REAL,
                last_error_json TEXT,
                updated_at REAL NOT NULL
            );
            CREATE TABLE worker_instances (
                instance_id TEXT PRIMARY KEY,
                pid INTEGER,
                process_start_token TEXT,
                process_create_time REAL,
                scenario TEXT,
                status TEXT,
                started_at REAL,
                ready_at REAL,
                last_heartbeat_at REAL,
                shutdown_requested_at REAL,
                stopped_at REAL,
                exit_code INTEGER,
                exit_reason TEXT,
                log_stdout_path TEXT,
                log_stderr_path TEXT
            );
            CREATE TABLE supervisor_lease (
                lease_id TEXT PRIMARY KEY,
                supervisor_id TEXT,
                pid INTEGER,
                start_token TEXT,
                acquired_at REAL,
                expires_at REAL
            );
            """
        )
    database.initialize()
    database.verify_schema()
    with database.connection() as connection:
        supervisor_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(supervisor_state)").fetchall()
        }
        worker_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(worker_instances)").fetchall()
        }
        lease_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(supervisor_lease)").fetchall()
        }
    assert {"started_at", "stopped_at"} <= supervisor_columns
    assert "last_heartbeat_sequence" in worker_columns
    assert {"owner_id", "renewed_at"} <= lease_columns
    assert database.acquire_lease("main", "owner", 1, "token", 3)


def test_state_updates_preserve_unrelated_fields(database: Database) -> None:
    database.ensure_supervisor("main", now=100)
    database.update_supervisor(
        "main",
        status=SupervisorState.IDLE,
        paused=True,
        restart_attempt_count=3,
    )
    database.update_supervisor("main", emergency_stop=True)
    state = database.get_supervisor_state("main")
    assert state["paused"] == 1
    assert state["restart_attempt_count"] == 3
    assert state["emergency_stop"] == 1


def test_lease_is_exclusive_renewable_and_expirable(database: Database) -> None:
    assert database.acquire_lease("main", "owner-a", 1, "a", 10, now=100)
    assert database.acquire_lease("main", "owner-a", 1, "a", 10, now=101)
    assert not database.acquire_lease("main", "owner-b", 2, "b", 10, now=105)
    assert database.acquire_lease("main", "owner-b", 2, "b", 10, now=112)
    assert not database.release_lease("main", "owner-a")
    assert database.release_lease("main", "owner-b")


def test_events_and_metrics_are_append_only(database: Database) -> None:
    database.append_event("runtime.test", details={"ok": True})
    database.append_event("runtime.test", details={"ok": True})
    database.bump_metric("recovery_count")
    assert database.event_count() == 2
    assert database.metrics()["recovery_count"] == 1


def test_transaction_rolls_back_on_error(database: Database) -> None:
    with pytest.raises(RuntimeError):
        with database.transaction() as connection:
            connection.execute("UPDATE runtime_metrics SET recovery_count = 99 WHERE singleton = 1")
            raise RuntimeError("injected")
    assert database.metrics()["recovery_count"] == 0
