"""widen durable outbox and audit correlation identifiers to the shared 120 limit

Revision ID: 20260728_08
Revises: 20260727_07
"""

import sqlalchemy as sa
from alembic import op

revision = "20260728_08"
down_revision = "20260727_07"
branch_labels = None
depends_on = None

WIDENED_CORRELATION_COLUMNS = (
    ("outbox_events", "correlation_id"),
    ("audit_events", "correlation_id"),
)
NEW_LENGTH = 120
OLD_LENGTH = 80


def upgrade() -> None:
    # Batch operations keep SQLite safe: each table is recreated with the wider
    # column while every row, index, constraint, default, nullability, and
    # foreign-key behavior is reproduced by Alembic from reflected metadata.
    for table_name, column_name in WIDENED_CORRELATION_COLUMNS:
        with op.batch_alter_table(table_name) as batch:
            batch.alter_column(
                column_name,
                existing_type=sa.String(length=OLD_LENGTH),
                type_=sa.String(length=NEW_LENGTH),
                existing_nullable=False,
            )


def _reject_unrepresentable_downgrade() -> None:
    connection = op.get_bind()
    for table_name, column_name in WIDENED_CORRELATION_COLUMNS:
        oversized = connection.scalar(
            sa.text(
                f"SELECT COUNT(*) FROM {table_name} "  # noqa: S608 - fixed identifiers
                f"WHERE {column_name} IS NOT NULL AND LENGTH({column_name}) > :limit"
            ),
            {"limit": OLD_LENGTH},
        )
        if oversized:
            raise RuntimeError(
                f"cannot downgrade {table_name}.{column_name}: "
                f"{oversized} stored value(s) exceed {OLD_LENGTH} characters"
            )


def downgrade() -> None:
    # Fail before any DDL so an unrepresentable downgrade leaves all rows, the
    # current schema, and the alembic revision stamp untouched.
    _reject_unrepresentable_downgrade()

    for table_name, column_name in reversed(WIDENED_CORRELATION_COLUMNS):
        with op.batch_alter_table(table_name) as batch:
            batch.alter_column(
                column_name,
                existing_type=sa.String(length=NEW_LENGTH),
                type_=sa.String(length=OLD_LENGTH),
                existing_nullable=False,
            )
