"""Durable development workspace reservations; no filesystem side effects."""

import sqlalchemy as sa
from alembic import op

revision = "20261006_12"
down_revision = "20260907_11"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "development_workspaces",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("repository_id", sa.String(120), nullable=False),
        sa.Column("repository_identity", sa.String(220), nullable=False),
        sa.Column("policy_digest", sa.String(64), nullable=False),
        sa.Column("policy_json", sa.JSON(), nullable=False),
        sa.Column(
            "runtime_run_id",
            sa.String(120),
            sa.ForeignKey("agent_runtime_runs.run_id"),
            nullable=False,
        ),
        sa.Column("task_id", sa.String(80), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("actor_id", sa.String(160), sa.ForeignKey("identity_agents.id"), nullable=False),
        sa.Column("worker_id", sa.String(80), sa.ForeignKey("workers.id"), nullable=False),
        sa.Column("approval_id", sa.String(80), sa.ForeignKey("audit_events.id"), nullable=False),
        sa.Column("lease_fingerprint", sa.String(64), nullable=False),
        sa.Column("plan_json", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("runtime_run_id", "repository_id", name="uq_workspace_run_repository"),
        sa.CheckConstraint("version >= 1"),
        sa.CheckConstraint("state IN ('reserved', 'abandoned')"),
    )
    op.create_index("ix_development_workspaces_task_id", "development_workspaces", ["task_id"])


def downgrade():
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM development_workspaces")).scalar():
        raise RuntimeError("Export development workspace history before downgrading revision 12")
    op.drop_table("development_workspaces")
