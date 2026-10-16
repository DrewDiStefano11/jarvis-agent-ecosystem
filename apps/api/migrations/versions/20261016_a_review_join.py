"""Join development dispatch and shared nonbillable capacity history."""

import sqlalchemy as sa
from alembic import op

revision = "20261016_a_review_join"
down_revision = ("20261013_development_dispatch", "20261013_free_ai")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    # Refuse before splitting the version stamp, just as the dispatch ancestor
    # refuses before dropping any durable recovery evidence.
    connection = op.get_bind()
    for table in ("development_dispatches", "development_admissions"):
        if connection.execute(sa.text("SELECT COUNT(*) FROM " + table)).scalar():
            raise RuntimeError("Export development dispatch recovery evidence before downgrading")
