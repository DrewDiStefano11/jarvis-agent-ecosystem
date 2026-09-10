"""Bounded versioned planned graphs; no executable child tasks."""

import sqlalchemy as sa
from alembic import op

revision = "20260906_10"
down_revision = "20260906_09"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "task_decompositions",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("task_id", sa.String(80), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("active_task_id", sa.String(80), sa.ForeignKey("tasks.id"), unique=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.UniqueConstraint("task_id", "version"),
        sa.CheckConstraint("version > 0"),
        sa.CheckConstraint("active_task_id IS NULL OR active_task_id = task_id"),
    )
    op.create_index("ix_task_decompositions_task_id", "task_decompositions", ["task_id"])


def downgrade():
    # Children, dependencies and assignments are contained in the single atomic
    # bounded graph payload; none can outlive or bypass this guard.
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM task_decompositions")).scalar():
        raise RuntimeError("Export decomposition history before downgrading populated revision 10")
    op.drop_table("task_decompositions")
