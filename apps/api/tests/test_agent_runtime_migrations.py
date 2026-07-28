from __future__ import annotations

import json
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from app.db.models import AgentRuntimeCheckpointRow


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


# --- Checkpoint migration (20260727_07) tests ---

CHECKPOINT_HEAD = "20260727_07"
CHECKPOINT_PREV = "20260727_06"


def _insert_runtime_run_for_checkpoint(
    engine, run_id: str, extra_checkpoints: list[dict] | None = None
) -> None:
    """Insert a runtime run row and optionally checkpoint rows at revision 20260727_06."""
    specification = {
        "run_id": run_id,
        "task_id": "task-1",
        "agent_id": "agent-1",
        "requested_operation": "test",
        "created_at": "2026-07-27T00:00:00Z",
        "idempotency_key": f"key-{run_id}",
        "maximum_permitted_attempts": 1,
    }
    with engine.begin() as connection:
        connection.execute(
            text(
                """INSERT INTO agent_runtime_runs (
                run_id, task_id, agent_id, parent_run_id, state, version,
                event_sequence_number, attempt_count, active_attempt_id,
                latest_checkpoint_id, recovery_status, created_at, updated_at,
                deadline, terminal_at, specification_json, snapshot_json
            ) VALUES (
                :run_id, 'task-1', 'agent-1', NULL, 'created', 1, 1, 0,
                NULL, NULL, 'none', '2026-07-27 00:00:00', '2026-07-27 00:00:00',
                NULL, NULL, :specification_json, '{}'
            )"""
            ),
            {"run_id": run_id, "specification_json": json.dumps(specification)},
        )
        if extra_checkpoints:
            for cp in extra_checkpoints:
                connection.execute(
                    text(
                        """INSERT INTO agent_runtime_checkpoints (
                        checkpoint_id, run_id, attempt_id,
                        checkpoint_sequence, contract_json
                    ) VALUES (
                        :checkpoint_id, :run_id, :attempt_id,
                        :checkpoint_sequence, :contract_json
                    )"""
                    ),
                    cp,
                )


def test_checkpoint_migration_blank_upgrade_to_head(tmp_path: Path) -> None:
    """Blank database upgrades to 20260727_07."""
    root = Path(__file__).resolve().parents[1]
    database = tmp_path / "checkpoint-blank.db"
    config = migration_config(root, database)
    command.upgrade(config, CHECKPOINT_HEAD)
    engine = create_engine(database_url(database))
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == CHECKPOINT_HEAD


def test_checkpoint_migration_alembic_version_reports_head(tmp_path: Path) -> None:
    """Alembic version reports 20260727_07."""
    root = Path(__file__).resolve().parents[1]
    database = tmp_path / "checkpoint-version.db"
    config = migration_config(root, database)
    command.upgrade(config, CHECKPOINT_HEAD)
    engine = create_engine(database_url(database))
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == CHECKPOINT_HEAD


def test_checkpoint_migration_exactly_one_head(tmp_path: Path) -> None:
    """Alembic has exactly one head."""
    root = Path(__file__).resolve().parents[1]
    database = tmp_path / "checkpoint-heads.db"
    config = migration_config(root, database)
    command.upgrade(config, CHECKPOINT_HEAD)
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    assert len(heads) == 1
    assert heads[0] == CHECKPOINT_HEAD


def test_checkpoint_migration_primary_key_columns(tmp_path: Path) -> None:
    """Checkpoint primary-key columns are run_id, checkpoint_id."""
    root = Path(__file__).resolve().parents[1]
    database = tmp_path / "checkpoint-pk.db"
    config = migration_config(root, database)
    command.upgrade(config, CHECKPOINT_HEAD)
    engine = create_engine(database_url(database))
    inspector = inspect(engine)
    pk = inspector.get_pk_constraint("agent_runtime_checkpoints")
    assert set(pk.get("constrained_columns", [])) == {"run_id", "checkpoint_id"}


def test_checkpoint_migration_no_global_unique_on_checkpoint_id(tmp_path: Path) -> None:
    """checkpoint_id alone has no global unique constraint."""
    root = Path(__file__).resolve().parents[1]
    database = tmp_path / "checkpoint-no-global-unique.db"
    config = migration_config(root, database)
    command.upgrade(config, CHECKPOINT_HEAD)
    engine = create_engine(database_url(database))
    inspector = inspect(engine)
    unique_constraints = inspector.get_unique_constraints("agent_runtime_checkpoints")
    unique_columns = {tuple(uc["column_names"]) for uc in unique_constraints}
    assert ("checkpoint_id",) not in unique_columns


def test_checkpoint_migration_run_id_checkpoint_sequence_unique(tmp_path: Path) -> None:
    """(run_id, checkpoint_sequence) remains unique."""
    root = Path(__file__).resolve().parents[1]
    database = tmp_path / "checkpoint-unique-seq.db"
    config = migration_config(root, database)
    command.upgrade(config, CHECKPOINT_HEAD)
    engine = create_engine(database_url(database))
    inspector = inspect(engine)
    unique_constraints = inspector.get_unique_constraints("agent_runtime_checkpoints")
    assert ("run_id", "checkpoint_sequence") in {
        tuple(uc["column_names"]) for uc in unique_constraints
    }


def test_checkpoint_migration_foreign_key_to_runs(tmp_path: Path) -> None:
    """run_id retains its foreign key to agent_runtime_runs."""
    root = Path(__file__).resolve().parents[1]
    database = tmp_path / "checkpoint-fk.db"
    config = migration_config(root, database)
    command.upgrade(config, CHECKPOINT_HEAD)
    engine = create_engine(database_url(database))
    inspector = inspect(engine)
    fks = inspector.get_foreign_keys("agent_runtime_checkpoints")
    run_fk = [fk for fk in fks if fk["constrained_columns"] == ["run_id"]]
    assert len(run_fk) == 1
    assert run_fk[0]["referred_table"] == "agent_runtime_runs"


def test_checkpoint_migration_cascade_delete(tmp_path: Path) -> None:
    """Cascade deletion remains enabled."""
    root = Path(__file__).resolve().parents[1]
    database = tmp_path / "checkpoint-cascade.db"
    config = migration_config(root, database)
    command.upgrade(config, CHECKPOINT_HEAD)
    engine = create_engine(database_url(database))
    inspector = inspect(engine)
    fks = inspector.get_foreign_keys("agent_runtime_checkpoints")
    for fk in fks:
        if fk["constrained_columns"] == ["run_id"]:
            assert "CASCADE" in str(fk.get("options", {})).upper()


def test_checkpoint_migration_run_id_index_exists(tmp_path: Path) -> None:
    """The run-ID index exists."""
    root = Path(__file__).resolve().parents[1]
    database = tmp_path / "checkpoint-index.db"
    config = migration_config(root, database)
    command.upgrade(config, CHECKPOINT_HEAD)
    engine = create_engine(database_url(database))
    inspector = inspect(engine)
    indexes = {idx["name"]: idx for idx in inspector.get_indexes("agent_runtime_checkpoints")}
    assert "ix_agent_runtime_checkpoints_run_id" in indexes


def test_checkpoint_migration_upgrade_from_v06_with_existing_checkpoints(tmp_path: Path) -> None:
    """A database at 20260727_06 with existing checkpoints upgrades successfully."""
    root = Path(__file__).resolve().parents[1]
    database = tmp_path / "checkpoint-upgrade-from-v06.db"
    config = migration_config(root, database)
    command.upgrade(config, CHECKPOINT_PREV)
    engine = create_engine(database_url(database))
    _insert_runtime_run_for_checkpoint(
        engine,
        "run-a",
        extra_checkpoints=[
            {
                "checkpoint_id": "cp-1",
                "run_id": "run-a",
                "attempt_id": "attempt-1",
                "checkpoint_sequence": 1,
                "contract_json": json.dumps({"step": 1}),
            }
        ],
    )
    command.upgrade(config, CHECKPOINT_HEAD)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == CHECKPOINT_HEAD


def test_checkpoint_migration_upgrade_preserves_checkpoint_fields(tmp_path: Path) -> None:
    """Upgrade preserves all checkpoint fields."""
    root = Path(__file__).resolve().parents[1]
    database = tmp_path / "checkpoint-preserve.db"
    config = migration_config(root, database)
    command.upgrade(config, CHECKPOINT_PREV)
    engine = create_engine(database_url(database))
    _insert_runtime_run_for_checkpoint(
        engine,
        "run-preserve",
        extra_checkpoints=[
            {
                "checkpoint_id": "cp-preserve",
                "run_id": "run-preserve",
                "attempt_id": "att-preserve",
                "checkpoint_sequence": 1,
                "contract_json": json.dumps(
                    {
                        "checkpoint_id": "cp-preserve",
                        "run_id": "run-preserve",
                        "attempt_id": "att-preserve",
                        "checkpoint_sequence": 1,
                        "state": "running",
                    }
                ),
            }
        ],
    )
    command.upgrade(config, CHECKPOINT_HEAD)
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT checkpoint_id, run_id, attempt_id, checkpoint_sequence, contract_json "
                "FROM agent_runtime_checkpoints WHERE checkpoint_id = 'cp-preserve'"
            )
        ).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.checkpoint_id == "cp-preserve"
    assert row.run_id == "run-preserve"
    assert row.attempt_id == "att-preserve"
    assert row.checkpoint_sequence == 1
    assert "running" in row.contract_json


def test_checkpoint_migration_upgrade_row_loadable_through_orm(tmp_path: Path) -> None:
    """The preserved row loads through the ORM after upgrade."""
    root = Path(__file__).resolve().parents[1]
    database = tmp_path / "checkpoint-orm.db"
    config = migration_config(root, database)
    command.upgrade(config, CHECKPOINT_PREV)
    engine = create_engine(database_url(database))
    _insert_runtime_run_for_checkpoint(
        engine,
        "run-orm",
        extra_checkpoints=[
            {
                "checkpoint_id": "cp-orm",
                "run_id": "run-orm",
                "attempt_id": "att-orm",
                "checkpoint_sequence": 1,
                "contract_json": json.dumps({"step": 1}),
            }
        ],
    )
    command.upgrade(config, CHECKPOINT_HEAD)
    with Session(engine) as session:
        row = session.get(AgentRuntimeCheckpointRow, ("cp-orm", "run-orm"))
        assert row is not None
        assert row.checkpoint_id == "cp-orm"
        assert row.run_id == "run-orm"


def test_checkpoint_migration_downgrade_and_reupgrade(tmp_path: Path) -> None:
    """Downgrade to 20260727_06 succeeds and re-upgrade succeeds."""
    root = Path(__file__).resolve().parents[1]
    database = tmp_path / "checkpoint-downgrade-reup.db"
    config = migration_config(root, database)
    command.upgrade(config, CHECKPOINT_HEAD)
    engine = create_engine(database_url(database))
    _insert_runtime_run_for_checkpoint(
        engine,
        "run-downgrade",
        extra_checkpoints=[
            {
                "checkpoint_id": "cp-downgrade",
                "run_id": "run-downgrade",
                "attempt_id": "att-downgrade",
                "checkpoint_sequence": 1,
                "contract_json": json.dumps({"step": 1}),
            }
        ],
    )
    command.downgrade(config, CHECKPOINT_PREV)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == CHECKPOINT_PREV
    command.upgrade(config, CHECKPOINT_HEAD)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == CHECKPOINT_HEAD


def test_checkpoint_migration_downgrade_fails_with_duplicate_cp_ids(tmp_path: Path) -> None:
    """Downgrade after cross-run duplicate checkpoint IDs fails predictably."""
    root = Path(__file__).resolve().parents[1]
    database = tmp_path / "checkpoint-downgrade-fail.db"
    config = migration_config(root, database)
    command.upgrade(config, CHECKPOINT_HEAD)
    engine = create_engine(database_url(database))
    _insert_runtime_run_for_checkpoint(engine, "run-x")
    _insert_runtime_run_for_checkpoint(engine, "run-y")
    with engine.begin() as connection:
        connection.execute(
            text(
                """INSERT INTO agent_runtime_checkpoints (
                checkpoint_id, run_id, attempt_id,
                checkpoint_sequence, contract_json
            ) VALUES (
                'cp-shared', 'run-x', 'att-x', 1, :contract_x
            )"""
            ),
            {"contract_x": json.dumps({"step": 1})},
        )
        connection.execute(
            text(
                """INSERT INTO agent_runtime_checkpoints (
                checkpoint_id, run_id, attempt_id,
                checkpoint_sequence, contract_json
            ) VALUES (
                'cp-shared', 'run-y', 'att-y', 1, :contract_y
            )"""
            ),
            {"contract_y": json.dumps({"step": 1})},
        )
    with pytest.raises(Exception):  # noqa: B017 - alembic raises generic error on constraint violation
        command.downgrade(config, CHECKPOINT_PREV)


def test_checkpoint_migration_failed_downgrade_does_not_stamp_wrong_revision(
    tmp_path: Path,
) -> None:
    """Failed downgrade does not leave an incorrect Alembic revision stamp."""
    root = Path(__file__).resolve().parents[1]
    database = tmp_path / "checkpoint-no-stamp.db"
    config = migration_config(root, database)
    command.upgrade(config, CHECKPOINT_HEAD)
    engine = create_engine(database_url(database))
    # Insert two runs each with a checkpoint using the same checkpoint_id
    for run_id in ("run-a", "run-b"):
        _insert_runtime_run_for_checkpoint(engine, run_id)
    with engine.begin() as connection:
        connection.execute(
            text(
                """INSERT INTO agent_runtime_checkpoints (
                checkpoint_id, run_id, attempt_id,
                checkpoint_sequence, contract_json
            ) VALUES (
                'cp-shared', 'run-a', 'att-a', 1, :contract_a
            )"""
            ),
            {"contract_a": json.dumps({"step": 1})},
        )
        connection.execute(
            text(
                """INSERT INTO agent_runtime_checkpoints (
                checkpoint_id, run_id, attempt_id,
                checkpoint_sequence, contract_json
            ) VALUES (
                'cp-shared', 'run-b', 'att-b', 1, :contract_b
            )"""
            ),
            {"contract_b": json.dumps({"step": 1})},
        )
    # Downgrade should fail because cp-shared appears in two runs (would
    # violate the UNIQUE constraint on checkpoint_id alone in v06).
    with pytest.raises(Exception):  # noqa: B017 - alembic raises generic error on constraint violation
        command.downgrade(config, CHECKPOINT_PREV)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == CHECKPOINT_HEAD


def test_checkpoint_migration_round_trips_retain_one_head(tmp_path: Path) -> None:
    """Migration round trips retain one Alembic head."""
    root = Path(__file__).resolve().parents[1]
    database = tmp_path / "checkpoint-roundtrip.db"
    config = migration_config(root, database)
    command.upgrade(config, CHECKPOINT_HEAD)
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    assert len(heads) == 1
    assert heads[0] == CHECKPOINT_HEAD
    command.downgrade(config, CHECKPOINT_PREV)
    command.upgrade(config, CHECKPOINT_HEAD)
    heads = script.get_heads()
    assert len(heads) == 1
    assert heads[0] == CHECKPOINT_HEAD
