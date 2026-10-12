"""Append-only source edit proposals; no source mutation authority."""

import sqlalchemy as sa
from alembic import op

revision = "20261012_source_plans"
down_revision = "20261007_13"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "source_edit_plans",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("task_id", sa.String(80), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column(
            "runtime_run_id",
            sa.String(120),
            sa.ForeignKey("agent_runtime_runs.run_id"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            sa.String(80),
            sa.ForeignKey("development_workspaces.id"),
            nullable=False,
        ),
        sa.Column(
            "actor_id",
            sa.String(160),
            sa.ForeignKey("identity_agents.id"),
            nullable=False,
        ),
        sa.Column("command_id", sa.String(120), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("plan_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.UniqueConstraint("actor_id", "command_id", name="uq_source_plan_actor_command"),
    )
    op.create_index("ix_source_edit_plans_task_id", "source_edit_plans", ["task_id"])
    op.create_index("ix_source_edit_plans_workspace_id", "source_edit_plans", ["workspace_id"])


def downgrade():
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM source_edit_plans")).scalar():
        raise RuntimeError("Export source edit plan evidence before downgrading")
    op.drop_table("source_edit_plans")
