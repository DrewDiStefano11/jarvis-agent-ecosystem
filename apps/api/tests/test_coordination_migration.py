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
