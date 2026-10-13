"""Campaign D shared nonbillable capacity reservations."""

import sqlalchemy as sa
from alembic import op

revision = "20261013_free_ai"
down_revision = "20261012_source_plans"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "free_ai_quotas",
        sa.Column("bucket_key", sa.String(64), primary_key=True),
        sa.Column("window_key", sa.String(80), nullable=False),
        sa.Column("resets_at", sa.String(80), nullable=False),
        sa.Column("remaining_requests", sa.Integer(), nullable=False),
        sa.Column("exhausted", sa.Boolean(), nullable=False),
        sa.CheckConstraint("remaining_requests >= 0", name="ck_free_quota_remaining"),
    )


def downgrade():
    op.drop_table("free_ai_quotas")
