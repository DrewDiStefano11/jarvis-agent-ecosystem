"""Immutable self-improvement evidence and planning records."""

import sqlalchemy as sa
from alembic import op

revision = "20261002_si"
down_revision = "20260906_10"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "self_improvement_records",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("baseline_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_index("ix_self_improvement_records_kind", "self_improvement_records", ["kind"])
    op.create_index(
        "ix_self_improvement_records_baseline_id", "self_improvement_records", ["baseline_id"]
    )


def downgrade():
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM self_improvement_records")).scalar():
        raise RuntimeError("Export self-improvement history before downgrading")
    op.drop_table("self_improvement_records")
