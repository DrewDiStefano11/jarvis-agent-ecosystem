from __future__ import annotations

import json
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def database_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def migration_config(path: Path, database: Path) -> Config:
    config = Config(str(path / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url(database))
    return config


def insert_runtime_run(connection, run_id: str, correlation_id: str | None) -> None:
    specification = {
        "run_id": run_id,
        "task_id": "task-1",
        "agent_id": "agent-1",
        "requested_operation": "test",
        "created_at": "2026-07-27T00:00:00Z",
        "idempotency_key": f"key-{run_id}",
        "maximum_permitted_attempts": 1,
        "correlation_id": correlation_id,
    }
    connection.execute(
        text(
            """
            INSERT INTO agent_runtime_runs (
                run_id, task_id, agent_id, parent_run_id, state, version,
                event_sequence_number, attempt_count, active_attempt_id,
                latest_checkpoint_id, recovery_status, created_at, updated_at,
                deadline, terminal_at, specification_json, snapshot_json
            ) VALUES (
                :run_id, 'task-1', 'agent-1', NULL, 'created', 1, 1, 0,
                NULL, NULL, 'none', '2026-07-27 00:00:00', '2026-07-27 00:00:00',
                NULL, NULL, :specification_json, '{}'
            )
            """
        ),
        {"run_id": run_id, "specification_json": json.dumps(specification)},
    )


def test_runtime_correlation_backfill_upgrade_downgrade_and_exact_values(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    database = tmp_path / "runtime-migration.db"
    config = migration_config(root, database)
    command.upgrade(config, "20260727_04")
    engine = create_engine(database_url(database))
    with engine.begin() as connection:
        insert_runtime_run(connection, "run-exact", "corr-123")
        insert_runtime_run(connection, "run-prefix", "corr-1234")
        insert_runtime_run(connection, "run-none", None)

    command.upgrade(config, "20260727_05")
    with engine.connect() as connection:
        values = dict(
            connection.execute(
                text("SELECT run_id, correlation_id FROM agent_runtime_runs ORDER BY run_id")
            ).all()
        )
    assert values == {"run-exact": "corr-123", "run-none": None, "run-prefix": "corr-1234"}
    indexes = {item["name"] for item in inspect(engine).get_indexes("agent_runtime_runs")}
    assert "ix_agent_runtime_runs_correlation_id" in indexes

    command.downgrade(config, "20260727_04")
    command.upgrade(config, "20260727_05")
    with engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT correlation_id FROM agent_runtime_runs WHERE run_id = 'run-exact'")
            )
            == "corr-123"
        )
