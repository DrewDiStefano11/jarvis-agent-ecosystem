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
                connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260907_11"
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
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260907_11"
    engine.dispose()
    command.downgrade(config, "20260729_04")
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT title FROM tasks WHERE id='preserved-task'"))
            == "Populated upgrade"
        )
    engine.dispose()
