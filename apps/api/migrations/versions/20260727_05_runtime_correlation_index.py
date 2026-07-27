"""add indexed runtime correlation projection

Revision ID: 20260727_05
Revises: 20260727_04
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "20260727_05"
down_revision = "20260727_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agent_runtime_runs") as batch:
        batch.add_column(sa.Column("correlation_id", sa.String(length=120), nullable=True))
        batch.create_index("ix_agent_runtime_runs_correlation_id", ["correlation_id"])

    connection = op.get_bind()
    rows = connection.execute(
        sa.text("SELECT run_id, specification_json FROM agent_runtime_runs")
    ).mappings()
    for row in rows:
        try:
            specification = json.loads(row["specification_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"Cannot backfill runtime correlation for run {row['run_id']}: invalid specification JSON"
            ) from exc
        if not isinstance(specification, dict):
            raise RuntimeError(
                f"Cannot backfill runtime correlation for run {row['run_id']}: specification is not an object"
            )
        correlation_id = specification.get("correlation_id")
        if correlation_id is not None and not isinstance(correlation_id, str):
            raise RuntimeError(
                f"Cannot backfill runtime correlation for run {row['run_id']}: invalid correlation ID"
            )
        connection.execute(
            sa.text(
                "UPDATE agent_runtime_runs SET correlation_id = :correlation_id WHERE run_id = :run_id"
            ),
            {"run_id": row["run_id"], "correlation_id": correlation_id},
        )


def downgrade() -> None:
    with op.batch_alter_table("agent_runtime_runs") as batch:
        batch.drop_index("ix_agent_runtime_runs_correlation_id")
        batch.drop_column("correlation_id")
