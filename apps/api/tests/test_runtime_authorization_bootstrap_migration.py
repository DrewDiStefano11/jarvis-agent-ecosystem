from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

HEAD = "20260727_06"
PREVIOUS = "20260727_05"
OPERATOR_ID = "agent-runtime-local-operator"
ROLE_ID = "role-runtime-local-operator"


def config_for(root: Path, database: Path) -> Config:
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database.as_posix()}")
    return config


def test_runtime_authorization_catalog_upgrade_downgrade_and_reupgrade(tmp_path: Path) -> None:
    config = config_for(Path(__file__).resolve().parents[1], tmp_path / "bootstrap.db")
    command.upgrade(config, HEAD)
    engine = create_engine(config.get_main_option("sqlalchemy.url"))
    with engine.connect() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM identity_permissions WHERE stable_key LIKE 'agent_runtime.%'"
                )
            )
            == 18
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM identity_role_permissions WHERE role_id = :role_id"),
                {"role_id": ROLE_ID},
            )
            == 18
        )
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM identity_capabilities WHERE stable_key IN ('agent_runtime.control', 'agent_runtime.recovery')"
                )
            )
            == 2
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM identity_agent_capabilities WHERE agent_id = :agent_id"),
                {"agent_id": OPERATOR_ID},
            )
            == 2
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM identity_agents WHERE id = :agent_id"),
                {"agent_id": OPERATOR_ID},
            )
            == 1
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM identity_roles WHERE id = :role_id"),
                {"role_id": ROLE_ID},
            )
            == 1
        )
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM identity_agent_roles WHERE id = 'assign-runtime-local-operator-role'"
                )
            )
            == 1
        )
    command.downgrade(config, PREVIOUS)
    with engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM identity_permissions WHERE id LIKE 'perm-runtime-%'")
            )
            == 0
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM identity_agents WHERE id = :agent_id"),
                {"agent_id": OPERATOR_ID},
            )
            == 0
        )
    command.upgrade(config, HEAD)
    with engine.connect() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM identity_permissions WHERE stable_key LIKE 'agent_runtime.%'"
                )
            )
            == 18
        )
