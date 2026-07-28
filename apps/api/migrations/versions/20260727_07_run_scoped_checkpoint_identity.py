"""scope runtime projection identities to their run

Revision ID: 20260727_07
Revises: 20260727_06
"""

import sqlalchemy as sa
from alembic import op

revision = "20260727_07"
down_revision = "20260727_06"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """CREATE TABLE agent_runtime_checkpoints_new (
        checkpoint_id VARCHAR(120) NOT NULL,
        run_id VARCHAR(120) NOT NULL,
        attempt_id VARCHAR(120) NOT NULL,
        checkpoint_sequence INTEGER NOT NULL,
        contract_json TEXT NOT NULL,
        PRIMARY KEY (run_id, checkpoint_id),
        UNIQUE (run_id, checkpoint_sequence),
        FOREIGN KEY(run_id) REFERENCES agent_runtime_runs(run_id) ON DELETE CASCADE
        )"""
    )
    op.execute(
        """INSERT INTO agent_runtime_checkpoints_new
        (checkpoint_id, run_id, attempt_id, checkpoint_sequence, contract_json)
        SELECT checkpoint_id, run_id, attempt_id, checkpoint_sequence, contract_json
        FROM agent_runtime_checkpoints"""
    )
    op.drop_table("agent_runtime_checkpoints")
    op.rename_table("agent_runtime_checkpoints_new", "agent_runtime_checkpoints")
    op.create_index("ix_agent_runtime_checkpoints_run_id", "agent_runtime_checkpoints", ["run_id"])

    op.execute(
        """CREATE TABLE agent_runtime_attempts_new (
        attempt_id VARCHAR(120) NOT NULL,
        run_id VARCHAR(120) NOT NULL,
        attempt_number INTEGER NOT NULL,
        contract_json TEXT NOT NULL,
        PRIMARY KEY (run_id, attempt_id),
        UNIQUE (run_id, attempt_number),
        FOREIGN KEY(run_id) REFERENCES agent_runtime_runs(run_id) ON DELETE CASCADE
        )"""
    )
    op.execute(
        """INSERT INTO agent_runtime_attempts_new
        (attempt_id, run_id, attempt_number, contract_json)
        SELECT attempt_id, run_id, attempt_number, contract_json
        FROM agent_runtime_attempts"""
    )
    op.drop_table("agent_runtime_attempts")
    op.rename_table("agent_runtime_attempts_new", "agent_runtime_attempts")
    op.create_index("ix_agent_runtime_attempts_run_id", "agent_runtime_attempts", ["run_id"])


def _reject_unrepresentable_downgrade() -> None:
    connection = op.get_bind()
    duplicate_checkpoint_id = connection.scalar(
        sa.text(
            """SELECT checkpoint_id FROM agent_runtime_checkpoints
            GROUP BY checkpoint_id HAVING COUNT(*) > 1 LIMIT 1"""
        )
    )
    if duplicate_checkpoint_id is not None:
        raise RuntimeError(
            "cannot downgrade runtime checkpoints: duplicate checkpoint_id values exist across runs"
        )
    duplicate_attempt_id = connection.scalar(
        sa.text(
            """SELECT attempt_id FROM agent_runtime_attempts
            GROUP BY attempt_id HAVING COUNT(*) > 1 LIMIT 1"""
        )
    )
    if duplicate_attempt_id is not None:
        raise RuntimeError(
            "cannot downgrade runtime attempts: duplicate attempt_id values exist across runs"
        )


def downgrade() -> None:
    # Revision 06 used globally unique projection IDs. Fail before any DDL so a
    # downgrade that cannot represent run-scoped data leaves every row intact.
    _reject_unrepresentable_downgrade()

    op.execute(
        """CREATE TABLE agent_runtime_attempts_old (
        attempt_id VARCHAR(120) NOT NULL PRIMARY KEY,
        run_id VARCHAR(120) NOT NULL,
        attempt_number INTEGER NOT NULL,
        contract_json TEXT NOT NULL,
        UNIQUE (run_id, attempt_number),
        FOREIGN KEY(run_id) REFERENCES agent_runtime_runs(run_id) ON DELETE CASCADE
        )"""
    )
    op.execute(
        """INSERT INTO agent_runtime_attempts_old
        (attempt_id, run_id, attempt_number, contract_json)
        SELECT attempt_id, run_id, attempt_number, contract_json
        FROM agent_runtime_attempts"""
    )
    op.drop_table("agent_runtime_attempts")
    op.rename_table("agent_runtime_attempts_old", "agent_runtime_attempts")
    op.create_index("ix_agent_runtime_attempts_run_id", "agent_runtime_attempts", ["run_id"])

    op.execute(
        """CREATE TABLE agent_runtime_checkpoints_old (
        checkpoint_id VARCHAR(120) NOT NULL PRIMARY KEY,
        run_id VARCHAR(120) NOT NULL,
        attempt_id VARCHAR(120) NOT NULL,
        checkpoint_sequence INTEGER NOT NULL,
        contract_json TEXT NOT NULL,
        UNIQUE (run_id, checkpoint_sequence),
        FOREIGN KEY(run_id) REFERENCES agent_runtime_runs(run_id) ON DELETE CASCADE
        )"""
    )
    op.execute(
        """INSERT INTO agent_runtime_checkpoints_old
        (checkpoint_id, run_id, attempt_id, checkpoint_sequence, contract_json)
        SELECT checkpoint_id, run_id, attempt_id, checkpoint_sequence, contract_json
        FROM agent_runtime_checkpoints"""
    )
    op.drop_table("agent_runtime_checkpoints")
    op.rename_table("agent_runtime_checkpoints_old", "agent_runtime_checkpoints")
    op.create_index("ix_agent_runtime_checkpoints_run_id", "agent_runtime_checkpoints", ["run_id"])
