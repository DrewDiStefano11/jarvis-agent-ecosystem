"""preserve full correlation identifiers in shared event records

Revision ID: 20260728_08
Revises: 20260727_07
"""

import sqlalchemy as sa
from alembic import op

revision = "20260728_08"
down_revision = "20260727_07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("outbox_events") as batch:
        batch.alter_column(
            "correlation_id",
            existing_type=sa.String(length=80),
            type_=sa.String(length=120),
            existing_nullable=False,
        )
    with op.batch_alter_table("audit_events") as batch:
        batch.alter_column(
            "correlation_id",
            existing_type=sa.String(length=80),
            type_=sa.String(length=120),
            existing_nullable=False,
        )


def downgrade() -> None:
    connection = op.get_bind()
    oversized_outbox_id = connection.scalar(
        sa.text("SELECT id FROM outbox_events WHERE length(correlation_id) > 80 LIMIT 1")
    )
    if oversized_outbox_id is not None:
        raise RuntimeError("cannot downgrade outbox_events: correlation_id exceeds 80 characters")
    oversized_audit_id = connection.scalar(
        sa.text("SELECT id FROM audit_events WHERE length(correlation_id) > 80 LIMIT 1")
    )
    if oversized_audit_id is not None:
        raise RuntimeError("cannot downgrade audit_events: correlation_id exceeds 80 characters")

    with op.batch_alter_table("audit_events") as batch:
        batch.alter_column(
            "correlation_id",
            existing_type=sa.String(length=120),
            type_=sa.String(length=80),
            existing_nullable=False,
        )
    with op.batch_alter_table("outbox_events") as batch:
        batch.alter_column(
            "correlation_id",
            existing_type=sa.String(length=120),
            type_=sa.String(length=80),
            existing_nullable=False,
        )
