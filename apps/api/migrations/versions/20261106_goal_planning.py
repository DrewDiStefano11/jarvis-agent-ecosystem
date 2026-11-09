"""Explicit expiring goal consent and immutable native task/run bindings."""

import sqlalchemy as sa
from alembic import op

revision = "20261106_goal_plans"
down_revision = "20261106_results"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "goal_planning_policies",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False, unique=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "operator_id", sa.String(80), sa.ForeignKey("identity_agents.id"), nullable=False
        ),
        sa.Column(
            "worker_actor_id", sa.String(80), sa.ForeignKey("identity_agents.id"), nullable=False
        ),
        sa.Column("maximum_goals", sa.Integer(), nullable=False),
        sa.Column("admitted_goals", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("policy_hash", sa.String(64), nullable=False),
        sa.Column("scope_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("maximum_goals >= 1 AND maximum_goals <= 20"),
        sa.CheckConstraint("admitted_goals >= 0 AND admitted_goals <= maximum_goals"),
    )
    op.create_table(
        "goal_planning_admissions",
        sa.Column("goal_id", sa.String(80), sa.ForeignKey("workspace_goals.id"), primary_key=True),
        sa.Column("task_id", sa.String(80), sa.ForeignKey("tasks.id"), nullable=False, unique=True),
        sa.Column(
            "policy_id", sa.String(80), sa.ForeignKey("goal_planning_policies.id"), nullable=False
        ),
        sa.Column("policy_hash", sa.String(64), nullable=False),
        sa.Column(
            "worker_actor_id", sa.String(80), sa.ForeignKey("identity_agents.id"), nullable=False
        ),
        sa.Column("run_id", sa.String(120), nullable=False, unique=True),
        sa.Column("task_input_hash", sa.String(64), nullable=False),
        sa.Column(
            "context_assembly_id",
            sa.String(80),
            sa.ForeignKey("context_assemblies.id"),
            nullable=False,
        ),
        sa.Column("context_input_hash", sa.String(64), nullable=False),
        sa.Column("last_error_code", sa.String(80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    for table in ("goal_planning_admissions", "goal_planning_policies"):
        if op.get_bind().execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar():
            raise RuntimeError("Preserve captured goal-planning authority before downgrade")
    op.drop_table("goal_planning_admissions")
    op.drop_table("goal_planning_policies")
