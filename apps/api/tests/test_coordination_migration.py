from alembic import command
from sqlalchemy import create_engine, inspect, text

from tests.test_autonomous_worker_migration import migration_config
from tests.test_persistence import database_url


def test_coordination_upgrade_from_dependency_and_empty_roundtrip(tmp_path):
    path = tmp_path / "coordination-migration.db"
    config = migration_config(path)
    command.upgrade(config, "20260906_10")
    engine = create_engine(database_url(path))
    try:
        assert "task_coordinations" not in inspect(engine).get_table_names()
        command.upgrade(config, "head")
        engine.dispose()
        schema = inspect(engine)
        assert {
            tuple(c["column_names"]) for c in schema.get_unique_constraints("task_coordinations")
        } == {
            ("decomposition_id",),
            ("runtime_run_id",),
        }
        assert {
            tuple(c["constrained_columns"]) for c in schema.get_foreign_keys("task_coordinations")
        } == {
            ("task_id",),
            ("decomposition_id",),
            ("runtime_run_id",),
        }
        command.downgrade(config, "20260906_10")
        engine.dispose()
        assert "task_coordinations" not in inspect(engine).get_table_names()
        command.upgrade(config, "head")
        engine.dispose()
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version")) == "20261007_13"
            )
    finally:
        engine.dispose()


def test_populated_main_upgrade_preserves_task_and_supported_roundtrip(tmp_path):
    from datetime import UTC, datetime

    from sqlalchemy.orm import Session

    from app.db.models import TaskRow

    path = tmp_path / "populated-main.db"
    config = migration_config(path)
    command.upgrade(config, "20260906_10")
    engine = create_engine(database_url(path))
    now = datetime.now(UTC)
    with Session(engine) as session, session.begin():
        session.add(
            TaskRow(
                id="preserved-task",
                title="Populated upgrade",
                description="Preserve history",
                original_request="Preserve history",
                creator="operator",
                priority="normal",
                status="draft",
                progress=0,
                status_message="Draft",
                payload={"immutable_evidence": "preserved"},
                created_at=now,
                updated_at=now,
            )
        )
    engine.dispose()
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT title FROM tasks WHERE id='preserved-task'"))
            == "Populated upgrade"
        )
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20261007_13"
    engine.dispose()
    command.downgrade(config, "20260729_04")
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT title FROM tasks WHERE id='preserved-task'"))
            == "Populated upgrade"
        )
    engine.dispose()


def test_upgrade_from_self_improvement_preserves_populated_history(tmp_path):
    import json
    from datetime import UTC, datetime

    import pytest
    from alembic.script import ScriptDirectory

    config = migration_config(tmp_path / "self-improvement-upgrade.db")
    assert ScriptDirectory.from_config(config).get_heads() == ["20261007_13"]
    command.upgrade(config, "20261002_si")
    engine = create_engine(config.get_main_option("sqlalchemy.url"))
    payload = json.dumps({"immutable_evidence": "preserve self-improvement history"})
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO self_improvement_records (id,kind,baseline_id,created_at,payload) "
                "VALUES (:id,:kind,:baseline_id,:created_at,:payload)"
            ),
            dict(
                id="preserved-analysis",
                kind="analysis",
                baseline_id="preserved-baseline",
                created_at=datetime.now(UTC),
                payload=payload,
            ),
        )
    engine.dispose()
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20261007_13"
        assert connection.scalar(text("SELECT payload FROM self_improvement_records")) == payload
    engine.dispose()
    command.downgrade(config, "20261002_si")
    assert "task_coordinations" not in inspect(engine).get_table_names()
    with pytest.raises(RuntimeError, match="Export self-improvement history"):
        command.downgrade(config, "20260906_10")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT payload FROM self_improvement_records")) == payload
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20261002_si"
    engine.dispose()
    command.upgrade(config, "head")
    assert "task_coordinations" in inspect(engine).get_table_names()
    engine.dispose()
