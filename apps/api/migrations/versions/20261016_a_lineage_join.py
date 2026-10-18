"""Join correction lineage and integrated development migration histories."""

import sqlalchemy as sa
from alembic import op

revision = "20261016_a_lineage_join"
down_revision = ("20261014_development_lineage", "20261016_a_baseline_join")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    connection = op.get_bind()
    for table, label in (
        ("development_lineages", "lineage"),
        ("development_baselines", "baseline"),
        ("development_dispatches", "dispatch"),
        ("development_admissions", "dispatch"),
    ):
        if connection.execute(sa.text("SELECT COUNT(*) FROM " + table)).scalar():
            raise RuntimeError(f"Export development {label} recovery evidence before downgrading")
