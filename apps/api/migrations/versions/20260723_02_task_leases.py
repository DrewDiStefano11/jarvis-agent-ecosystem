"""Add fenced task leases, worker registrations, and execution attempts."""

import sqlalchemy as sa
from alembic import op

revision = "20260723_02"
down_revision = "20260720_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workers",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("instance_id", sa.String(120), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_seconds", sa.Integer(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.UniqueConstraint("instance_id"),
    )
    op.create_index("ix_workers_status", "workers", ["status"])
    op.create_index("ix_workers_last_heartbeat_at", "workers", ["last_heartbeat_at"])
    op.create_table(
        "task_leases",
        sa.Column("task_id", sa.String(80), primary_key=True),
        sa.Column("worker_id", sa.String(80), nullable=False),
        sa.Column("lease_token", sa.String(80), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("renewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("checkpoint_id", sa.String(80), nullable=True),
        sa.ForeignKeyConstraint(["checkpoint_id"], ["workflow_checkpoints.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.ForeignKeyConstraint(["worker_id"], ["workers.id"]),
        sa.UniqueConstraint("lease_token"),
    )
    op.create_index("ix_task_leases_worker_id", "task_leases", ["worker_id"])
    op.create_index("ix_task_leases_expires_at", "task_leases", ["expires_at"])
    op.create_table(
        "task_attempts",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("task_id", sa.String(80), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(80), nullable=False),
        sa.Column("lease_token", sa.String(80), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(40), nullable=True),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("checkpoint_id", sa.String(80), nullable=True),
        sa.ForeignKeyConstraint(["checkpoint_id"], ["workflow_checkpoints.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.ForeignKeyConstraint(["worker_id"], ["workers.id"]),
        sa.UniqueConstraint("lease_token"),
        sa.UniqueConstraint("task_id", "attempt_number"),
    )
    op.create_index("ix_task_attempts_task_id", "task_attempts", ["task_id"])
    op.create_index("ix_task_attempts_worker_id", "task_attempts", ["worker_id"])


def downgrade() -> None:
    op.drop_index("ix_task_attempts_worker_id", table_name="task_attempts")
    op.drop_index("ix_task_attempts_task_id", table_name="task_attempts")
    op.drop_table("task_attempts")
    op.drop_index("ix_task_leases_expires_at", table_name="task_leases")
    op.drop_index("ix_task_leases_worker_id", table_name="task_leases")
    op.drop_table("task_leases")
    op.drop_index("ix_workers_last_heartbeat_at", table_name="workers")
    op.drop_index("ix_workers_status", table_name="workers")
    op.drop_table("workers")
