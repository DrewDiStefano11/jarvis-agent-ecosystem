"""Native one-request Groq authority and independent durable rate windows."""

import sqlalchemy as sa
from alembic import op

revision = "20261102_groq"
down_revision = "20261102_models"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "groq_acceptance_runs",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("scope_key", sa.String(64), nullable=False, unique=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("private_digest", sa.String(64), nullable=False),
        sa.Column("last_audit_id", sa.String(80), sa.ForeignKey("audit_events.id"), nullable=False),
        sa.CheckConstraint("revision > 0", name="ck_groq_acceptance_revision"),
    )
    op.create_table(
        "groq_rate_states",
        sa.Column("scope_key", sa.String(64), primary_key=True),
        sa.Column(
            "acceptance_id",
            sa.String(80),
            sa.ForeignKey("groq_acceptance_runs.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("private_digest", sa.String(64), nullable=False),
        sa.Column("last_audit_id", sa.String(80), sa.ForeignKey("audit_events.id"), nullable=False),
        sa.CheckConstraint("revision > 0", name="ck_groq_rate_revision"),
    )


def downgrade():
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT COUNT(*) FROM groq_acceptance_runs")).scalar():
        raise RuntimeError("Preserve native acceptance/rate history before downgrade")
    op.drop_table("groq_rate_states")
    op.drop_table("groq_acceptance_runs")
