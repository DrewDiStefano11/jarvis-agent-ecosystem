"""Protected completed-source correction snapshots."""

import sqlalchemy as sa
from alembic import op

revision = "20261013_development_baselines"
down_revision = "20261013_development_dispatch"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "development_baselines",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "dispatch_id",
            sa.String(64),
            sa.ForeignKey("development_dispatches.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "task_id",
            sa.String(80),
            sa.ForeignKey("development_admissions.task_id"),
            nullable=False,
        ),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("integrity_digest", sa.String(64), nullable=False),
    )


def downgrade():
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM development_baselines")).scalar():
        raise RuntimeError("Export development baseline recovery evidence before downgrading")
    op.drop_table("development_baselines")
