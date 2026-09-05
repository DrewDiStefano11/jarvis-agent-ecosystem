"""Durable explicitly authorized workspace tool execution."""

import sqlalchemy as sa
from alembic import op

revision = "20260905_08"
down_revision = "20260905_07"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "tool_executions",
        sa.Column("execution_id", sa.String(80), primary_key=True),
        sa.Column(
            "source_execution_id",
            sa.String(80),
            sa.ForeignKey("model_executions.execution_id"),
            nullable=False,
        ),
        sa.Column("source_task_id", sa.String(80), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("task_id", sa.String(80), sa.ForeignKey("tasks.id"), nullable=False, unique=True),
        sa.Column("runtime_run_id", sa.String(120), nullable=False, unique=True),
        sa.Column("actor_id", sa.String(80), sa.ForeignKey("identity_agents.id"), nullable=False),
        sa.Column(
            "target_agent_id", sa.String(80), sa.ForeignKey("identity_agents.id"), nullable=False
        ),
        sa.Column("command_id", sa.String(120), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("plan_hash", sa.String(64), nullable=False),
        sa.Column("plan_json", sa.JSON(), nullable=False),
        sa.Column("scope_json", sa.JSON(), nullable=False),
        sa.Column("steps_json", sa.JSON(), nullable=False),
        sa.Column("artifacts_json", sa.JSON(), nullable=False),
        sa.Column("stage", sa.String(20), nullable=False),
        sa.Column("failure_code", sa.String(80)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("actor_id", "command_id", name="uq_tool_execution_actor_command"),
    )
    for column in ("source_execution_id", "source_task_id", "stage"):
        op.create_index(f"ix_tool_executions_{column}", "tool_executions", [column])


def downgrade():
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM tool_executions")).scalar():
        raise RuntimeError(
            "Tool execution authorizations and artifacts are not representable before revision 08"
        )
    op.drop_table("tool_executions")
