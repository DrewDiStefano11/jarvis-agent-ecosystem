"""Private workspace creation intent; native runtime checkpoints remain authoritative."""

import sqlalchemy as sa
from alembic import op

revision = "20261007_13"
down_revision = "20261006_12"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "development_workspaces",
        sa.Column("creation_json", sa.JSON(none_as_null=True), nullable=True),
    )


def downgrade():
    if (
        op.get_bind()
        .execute(
            sa.text("SELECT COUNT(*) FROM development_workspaces WHERE creation_json IS NOT NULL")
        )
        .scalar()
    ):
        raise RuntimeError("Export workspace creation intent before downgrading revision 13")
    op.drop_column("development_workspaces", "creation_json")
