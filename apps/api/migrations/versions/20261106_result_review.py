"""Preserve operator decisions and real native artifact content."""

import sqlalchemy as sa
from alembic import op

revision = "20261106_results"
down_revision = "20261106_goals"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "result_reviews",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column(
            "execution_id",
            sa.String(80),
            sa.ForeignKey("model_executions.execution_id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("actor_id", sa.String(80), sa.ForeignKey("identity_agents.id"), nullable=False),
        sa.Column("command_id", sa.String(200), nullable=False),
        sa.Column("result_hash", sa.String(64), nullable=False),
        sa.Column("decision", sa.String(30), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "result_artifact_contents",
        sa.Column("artifact_id", sa.String(80), sa.ForeignKey("artifacts.id"), primary_key=True),
        sa.Column(
            "review_id",
            sa.String(80),
            sa.ForeignKey("result_reviews.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
    )


def downgrade():
    for table in ("result_artifact_contents", "result_reviews"):
        if op.get_bind().execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar():
            raise RuntimeError("Preserve reviewed results and artifact content before downgrade")
    op.drop_table("result_artifact_contents")
    op.drop_table("result_reviews")
