"""safe local autonomous planning worker

Revision ID: 20260729_05
Revises: 20260729_04
Create Date: 2026-07-29 18:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260729_05"
down_revision = "20260729_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_executions",
        sa.Column("execution_id", sa.String(length=80), nullable=False),
        sa.Column("runtime_run_id", sa.String(length=120), nullable=False),
        sa.Column("runtime_attempt_id", sa.String(length=120), nullable=False),
        sa.Column("task_id", sa.String(length=80), nullable=False),
        sa.Column("target_agent_id", sa.String(length=80), nullable=False),
        sa.Column("context_assembly_id", sa.String(length=80), nullable=False),
        sa.Column("worker_id", sa.String(length=80), nullable=False),
        sa.Column("task_attempt_number", sa.Integer(), nullable=False),
        sa.Column("lease_token_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("stage", sa.String(length=40), nullable=False),
        sa.Column("schema_version", sa.String(length=20), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("execution_request_hash", sa.String(length=64), nullable=False),
        sa.Column("result_hash", sa.String(length=64), nullable=True),
        sa.Column("provider", sa.String(length=120), nullable=True),
        sa.Column("model", sa.String(length=200), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Numeric(14, 3), nullable=True),
        sa.Column("finish_reason", sa.String(length=120), nullable=True),
        sa.Column("estimated_cost_usd", sa.Numeric(14, 8), nullable=True),
        sa.Column("requires_human_review", sa.Boolean(), nullable=False),
        sa.Column("failure_code", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("request_count >= 0 AND request_count <= 2"),
        sa.ForeignKeyConstraint(
            ["runtime_run_id"], ["agent_runtime_runs.run_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.ForeignKeyConstraint(["target_agent_id"], ["identity_agents.id"]),
        sa.ForeignKeyConstraint(["context_assembly_id"], ["context_assemblies.id"]),
        sa.ForeignKeyConstraint(["worker_id"], ["workers.id"]),
        sa.PrimaryKeyConstraint("execution_id"),
        sa.UniqueConstraint(
            "runtime_run_id",
            "runtime_attempt_id",
            name="uq_model_executions_runtime_attempt",
        ),
    )
    op.create_index(
        "ix_model_executions_context_assembly_id",
        "model_executions",
        ["context_assembly_id"],
    )
    op.create_index("ix_model_executions_completed_at", "model_executions", ["completed_at"])
    op.create_index("ix_model_executions_runtime_run_id", "model_executions", ["runtime_run_id"])
    op.create_index(
        "ix_model_executions_runtime_attempt_id",
        "model_executions",
        ["runtime_attempt_id"],
    )
    op.create_index("ix_model_executions_task_id", "model_executions", ["task_id"])
    op.create_index("ix_model_executions_target_agent_id", "model_executions", ["target_agent_id"])
    op.create_index("ix_model_executions_worker_id", "model_executions", ["worker_id"])
    op.create_index("ix_model_executions_stage", "model_executions", ["stage"])
    op.create_index("ix_model_executions_updated_at", "model_executions", ["updated_at"])
    op.create_index("ix_model_executions_recovery", "model_executions", ["stage", "updated_at"])


def downgrade() -> None:
    connection = op.get_bind()
    stored = int(connection.scalar(sa.text("SELECT COUNT(*) FROM model_executions")) or 0)
    if stored:
        raise RuntimeError(
            "cannot downgrade model executions: Phase 2C execution data is not representable "
            f"at 20260729_04 ({stored} stored row(s))"
        )
    op.drop_index("ix_model_executions_recovery", table_name="model_executions")
    op.drop_index("ix_model_executions_updated_at", table_name="model_executions")
    op.drop_index("ix_model_executions_stage", table_name="model_executions")
    op.drop_index("ix_model_executions_worker_id", table_name="model_executions")
    op.drop_index("ix_model_executions_target_agent_id", table_name="model_executions")
    op.drop_index("ix_model_executions_task_id", table_name="model_executions")
    op.drop_index("ix_model_executions_runtime_attempt_id", table_name="model_executions")
    op.drop_index("ix_model_executions_runtime_run_id", table_name="model_executions")
    op.drop_index("ix_model_executions_completed_at", table_name="model_executions")
    op.drop_index("ix_model_executions_context_assembly_id", table_name="model_executions")
    op.drop_table("model_executions")
