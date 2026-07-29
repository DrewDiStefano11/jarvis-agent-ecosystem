from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.db.session import create_database_engine
from tests.test_persistence import database_url


def migration_config(path: Path) -> Config:
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url(path))
    return config


def test_phase_2c_upgrade_downgrade_and_reupgrade(tmp_path: Path) -> None:
    path = tmp_path / "phase-2c-migration.db"
    config = migration_config(path)
    command.upgrade(config, "20260729_04")
    assert "model_executions" not in inspect(create_engine(database_url(path))).get_table_names()

    command.upgrade(config, "head")
    engine = create_engine(database_url(path))
    inspector = inspect(engine)
    assert "model_executions" in inspector.get_table_names()
    assert {
        tuple(item["column_names"]) for item in inspector.get_unique_constraints("model_executions")
    } == {("runtime_run_id", "runtime_attempt_id")}
    assert {
        tuple(item["constrained_columns"])
        for item in inspector.get_foreign_keys("model_executions")
    } == {
        ("runtime_run_id",),
        ("task_id",),
        ("target_agent_id",),
        ("context_assembly_id",),
        ("worker_id",),
    }
    assert "ix_model_executions_recovery" in {
        item["name"] for item in inspector.get_indexes("model_executions")
    }
    engine.dispose()

    command.downgrade(config, "20260729_04")
    with create_engine(database_url(path)).connect() as connection:
        assert connection.scalar(text("select version_num from alembic_version")) == "20260729_04"
    command.upgrade(config, "head")
    with create_database_engine(database_url(path)).connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert connection.scalar(text("select version_num from alembic_version")) == "20260729_05"


def test_phase_2c_downgrade_refuses_unrepresentable_rows_before_ddl(tmp_path: Path) -> None:
    path = tmp_path / "phase-2c-nonempty.db"
    config = migration_config(path)
    command.upgrade(config, "head")
    engine = create_engine(database_url(path))
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.execute(
            text(
                """
                INSERT INTO model_executions (
                    execution_id, runtime_run_id, runtime_attempt_id, task_id,
                    target_agent_id, context_assembly_id, worker_id,
                    task_attempt_number, lease_token_fingerprint, stage,
                    schema_version, request_hash, execution_request_hash,
                    request_count, requires_human_review, created_at, updated_at
                ) VALUES (
                    'exec-fixture', 'run-fixture', 'attempt-fixture', 'task-fixture',
                    'agent-fixture', 'assembly-fixture', 'worker-fixture',
                    1, 'fingerprint', 'prepared', '1.0',
                    :request_hash, :execution_hash, 0, 0,
                    '2026-07-29 18:00:00', '2026-07-29 18:00:00'
                )
                """
            ),
            {"request_hash": "a" * 64, "execution_hash": "b" * 64},
        )
    engine.dispose()

    with pytest.raises(RuntimeError, match="not representable"):
        command.downgrade(config, "20260729_04")
    guarded = create_engine(database_url(path))
    assert "model_executions" in inspect(guarded).get_table_names()
    with guarded.connect() as connection:
        assert connection.scalar(text("select version_num from alembic_version")) == "20260729_05"
    guarded.dispose()
