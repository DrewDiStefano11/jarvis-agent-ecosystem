"""Add durable context assemblies.

Revision ID: 20260724_03
Revises: 20260723_02
Create Date: 2026-07-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260724_03"
down_revision: str | None = "20260723_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "context_assemblies",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("task_id", sa.String(80), nullable=False),
        sa.Column("project_id", sa.String(120), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("policy_version", sa.String(80), nullable=False),
        sa.Column("included_source_count", sa.Integer(), nullable=False),
        sa.Column("excluded_source_count", sa.Integer(), nullable=False),
        sa.Column("redaction_count", sa.Integer(), nullable=False),
        sa.Column("injection_finding_count", sa.Integer(), nullable=False),
        sa.Column("conflict_count", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(20), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.UniqueConstraint("input_hash"),
    )
    op.create_index(
        "ix_context_assemblies_project_id",
        "context_assemblies",
        ["project_id"],
    )
    op.create_index(
        "ix_context_assemblies_request_hash",
        "context_assemblies",
        ["request_hash"],
    )
    op.create_index(
        "ix_context_assemblies_status",
        "context_assemblies",
        ["status"],
    )
    op.create_index(
        "ix_context_assemblies_task_id",
        "context_assemblies",
        ["task_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_context_assemblies_task_id", table_name="context_assemblies")
    op.drop_index("ix_context_assemblies_status", table_name="context_assemblies")
    op.drop_index("ix_context_assemblies_request_hash", table_name="context_assemblies")
    op.drop_index("ix_context_assemblies_project_id", table_name="context_assemblies")
    op.drop_table("context_assemblies")
