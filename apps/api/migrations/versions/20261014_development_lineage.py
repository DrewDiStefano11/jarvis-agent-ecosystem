"""Independent correction workspace lineage; preserve task budget origin."""

import sqlalchemy as sa
from alembic import op

revision = "20261014_development_lineage"
down_revision = "20261013_development_baselines"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "development_lineages",
        sa.Column(
            "workspace_id",
            sa.String(120),
            sa.ForeignKey("development_workspaces.id"),
            primary_key=True,
        ),
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
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM development_lineages")).scalar():
        raise RuntimeError("Export development lineage recovery evidence before downgrading")
    op.drop_table("development_lineages")
