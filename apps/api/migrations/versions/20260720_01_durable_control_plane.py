"""Create the Phase 2A durable control-plane schema."""

from alembic import op

from app.db import models  # noqa: F401
from app.db.base import Base

revision = "20260720_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=False)


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind(), checkfirst=True)
