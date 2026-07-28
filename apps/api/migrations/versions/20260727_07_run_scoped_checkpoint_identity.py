"""scope runtime checkpoint identity to its run

Revision ID: 20260727_07
Revises: 20260727_06
"""

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


def downgrade() -> None:
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
