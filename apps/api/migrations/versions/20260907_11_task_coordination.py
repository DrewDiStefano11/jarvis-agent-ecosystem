"""Canonical coordination per durable decomposition, reusing runtime and task leases."""

import sqlalchemy as sa
from alembic import op

revision = "20260907_11"
down_revision = "20260906_10"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "task_coordinations",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("task_id", sa.String(80), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column(
            "decomposition_id",
            sa.String(80),
            sa.ForeignKey("task_decompositions.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "runtime_run_id",
            sa.String(120),
            sa.ForeignKey("agent_runtime_runs.run_id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_index("ix_task_coordinations_task_id", "task_coordinations", ["task_id"])


def downgrade():
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM task_coordinations")).scalar():
        raise RuntimeError("Export coordination history before downgrading populated revision 11")
    op.drop_table("task_coordinations")
