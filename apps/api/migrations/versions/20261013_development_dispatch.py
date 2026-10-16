"""Protected development admission and physical request evidence."""

import sqlalchemy as sa
from alembic import op

revision = "20261013_development_dispatch"
down_revision = "20261012_source_mutation"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "development_admissions",
        sa.Column("task_id", sa.String(80), sa.ForeignKey("tasks.id"), primary_key=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("integrity_digest", sa.String(64), nullable=False),
    )
    op.create_table(
        "development_dispatches",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "task_id",
            sa.String(80),
            sa.ForeignKey("development_admissions.task_id"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("integrity_digest", sa.String(64), nullable=False),
        sa.UniqueConstraint("task_id", "ordinal"),
    )


def downgrade():
    connection = op.get_bind()
    for table in ("development_dispatches", "development_admissions"):
        if connection.execute(sa.text("SELECT COUNT(*) FROM " + table)).scalar():
            raise RuntimeError("Export development dispatch recovery evidence before downgrading")
    op.drop_table("development_dispatches")
    op.drop_table("development_admissions")
