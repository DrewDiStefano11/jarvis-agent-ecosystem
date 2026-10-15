"""Fenced source mutation intents depend explicitly on durable source planning."""

import sqlalchemy as sa
from alembic import op

revision = "20261012_source_mutation"
down_revision = "20261012_source_plans"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "source_mutation_intents",
        sa.Column(
            "editing_record_id",
            sa.String(64),
            sa.ForeignKey("source_edit_plans.id"),
            primary_key=True,
        ),
        sa.Column("operation_id", sa.String(120), nullable=False, unique=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("integrity_digest", sa.String(64), nullable=False),
    )


def downgrade():
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM source_mutation_intents")).scalar():
        raise RuntimeError("Export source mutation recovery evidence before downgrading")
    op.drop_table("source_mutation_intents")
