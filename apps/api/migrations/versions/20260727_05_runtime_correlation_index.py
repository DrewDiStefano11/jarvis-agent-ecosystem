"""add indexed runtime correlation projection

Revision ID: 20260727_05
Revises: 20260727_04
"""

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


def downgrade() -> None:
    with op.batch_alter_table("agent_runtime_runs") as batch:
        batch.drop_index("ix_agent_runtime_runs_correlation_id")
        batch.drop_column("correlation_id")
