"""durable agent runtime control plane

Revision ID: 20260729_04
Revises: a87a487dd714
Create Date: 2026-07-29 12:30:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260729_04"
down_revision = "a87a487dd714"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table_name, column_name in (
        ("outbox_events", "correlation_id"),
        ("audit_events", "correlation_id"),
    ):
        with op.batch_alter_table(table_name) as batch:
            batch.alter_column(
                column_name,
                existing_type=sa.String(length=80),
                type_=sa.String(length=120),
                existing_nullable=False,
            )

    op.create_table(
        "agent_runtime_runs",
        sa.Column("run_id", sa.String(length=120), nullable=False),
        sa.Column("task_id", sa.String(length=120), nullable=False),
        sa.Column("agent_id", sa.String(length=120), nullable=False),
        sa.Column("correlation_id", sa.String(length=120), nullable=True),
        sa.Column("parent_run_id", sa.String(length=120), nullable=True),
        sa.Column("state", sa.String(length=40), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("event_sequence_number", sa.Integer(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("active_attempt_id", sa.String(length=120), nullable=True),
        sa.Column("latest_checkpoint_id", sa.String(length=120), nullable=True),
        sa.Column("recovery_status", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("specification_json", sa.Text(), nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_index("ix_agent_runtime_runs_agent_id", "agent_runtime_runs", ["agent_id"])
    op.create_index(
        "ix_agent_runtime_runs_correlation_id", "agent_runtime_runs", ["correlation_id"]
    )
    op.create_index("ix_agent_runtime_runs_created_at", "agent_runtime_runs", ["created_at"])
    op.create_index("ix_agent_runtime_runs_deadline", "agent_runtime_runs", ["deadline"])
    op.create_index(
        "ix_agent_runtime_runs_nonterminal", "agent_runtime_runs", ["state", "created_at"]
    )
    op.create_index("ix_agent_runtime_runs_parent_run_id", "agent_runtime_runs", ["parent_run_id"])
    op.create_index("ix_agent_runtime_runs_state", "agent_runtime_runs", ["state"])
    op.create_index("ix_agent_runtime_runs_task_id", "agent_runtime_runs", ["task_id"])

    op.create_table(
        "agent_runtime_events",
        sa.Column("event_id", sa.String(length=120), nullable=False),
        sa.Column("run_id", sa.String(length=120), nullable=False),
        sa.Column("attempt_id", sa.String(length=120), nullable=True),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("schema_version", sa.String(length=20), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("run_version", sa.Integer(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_reference", sa.String(length=160), nullable=True),
        sa.Column("command_id", sa.String(length=120), nullable=True),
        sa.Column("correlation_id", sa.String(length=120), nullable=True),
        sa.Column("causation_id", sa.String(length=120), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        sa.Column("envelope_json", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runtime_runs.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("event_id"),
        sa.UniqueConstraint("run_id", "run_version"),
        sa.UniqueConstraint("run_id", "sequence_number"),
    )
    op.create_index("ix_agent_runtime_events_run_id", "agent_runtime_events", ["run_id"])

    op.create_table(
        "agent_runtime_attempts",
        sa.Column("attempt_id", sa.String(length=120), nullable=False),
        sa.Column("run_id", sa.String(length=120), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("contract_json", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runtime_runs.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("attempt_id", "run_id"),
        sa.UniqueConstraint("run_id", "attempt_number"),
    )
    op.create_index("ix_agent_runtime_attempts_run_id", "agent_runtime_attempts", ["run_id"])

    op.create_table(
        "agent_runtime_checkpoints",
        sa.Column("checkpoint_id", sa.String(length=120), nullable=False),
        sa.Column("run_id", sa.String(length=120), nullable=False),
        sa.Column("attempt_id", sa.String(length=120), nullable=False),
        sa.Column("checkpoint_sequence", sa.Integer(), nullable=False),
        sa.Column("contract_json", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runtime_runs.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("checkpoint_id", "run_id"),
        sa.UniqueConstraint("run_id", "checkpoint_sequence"),
    )

    op.create_table(
        "agent_runtime_processed_commands",
        sa.Column("run_id", sa.String(length=120), nullable=False),
        sa.Column("command_id", sa.String(length=120), nullable=False),
        sa.Column("command_hash", sa.String(length=64), nullable=False),
        sa.Column("command_type", sa.String(length=120), nullable=False),
        sa.Column("verified_actor_id", sa.String(length=160), nullable=True),
        sa.Column("authorization_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runtime_runs.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id", "command_id"),
    )


def _reject_unrepresentable_downgrade() -> None:
    connection = op.get_bind()
    for table_name, column_name in (
        ("outbox_events", "correlation_id"),
        ("audit_events", "correlation_id"),
    ):
        oversized = connection.scalar(
            sa.text(
                f"SELECT COUNT(*) FROM {table_name} "
                f"WHERE {column_name} IS NOT NULL AND LENGTH({column_name}) > :limit"
            ),
            {"limit": 80},
        )
        if oversized:
            raise RuntimeError(
                f"cannot downgrade {table_name}.{column_name}: {oversized} stored value(s) exceed 80 characters"
            )


def downgrade() -> None:
    _reject_unrepresentable_downgrade()

    op.drop_table("agent_runtime_processed_commands")
    op.drop_table("agent_runtime_checkpoints")
    op.drop_index("ix_agent_runtime_attempts_run_id", table_name="agent_runtime_attempts")
    op.drop_table("agent_runtime_attempts")
    op.drop_index("ix_agent_runtime_events_run_id", table_name="agent_runtime_events")
    op.drop_table("agent_runtime_events")
    op.drop_index("ix_agent_runtime_runs_task_id", table_name="agent_runtime_runs")
    op.drop_index("ix_agent_runtime_runs_state", table_name="agent_runtime_runs")
    op.drop_index("ix_agent_runtime_runs_parent_run_id", table_name="agent_runtime_runs")
    op.drop_index("ix_agent_runtime_runs_nonterminal", table_name="agent_runtime_runs")
    op.drop_index("ix_agent_runtime_runs_deadline", table_name="agent_runtime_runs")
    op.drop_index("ix_agent_runtime_runs_created_at", table_name="agent_runtime_runs")
    op.drop_index("ix_agent_runtime_runs_correlation_id", table_name="agent_runtime_runs")
    op.drop_index("ix_agent_runtime_runs_agent_id", table_name="agent_runtime_runs")
    op.drop_table("agent_runtime_runs")

    for table_name, column_name in reversed(
        (("outbox_events", "correlation_id"), ("audit_events", "correlation_id"))
    ):
        with op.batch_alter_table(table_name) as batch:
            batch.alter_column(
                column_name,
                existing_type=sa.String(length=120),
                type_=sa.String(length=80),
                existing_nullable=False,
            )
