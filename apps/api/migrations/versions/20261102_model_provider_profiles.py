"""Operator provider profiles with encrypted credential bytes and native audit binding."""

import sqlalchemy as sa
from alembic import op

revision = "20261102_models"
down_revision = "20261016_a_lineage_join"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "model_provider_profiles",
        sa.Column("provider_name", sa.String(120), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("encrypted_credential", sa.LargeBinary(), nullable=True),
        sa.Column("private_digest", sa.String(64), nullable=False),
        sa.Column("last_audit_id", sa.String(80), sa.ForeignKey("audit_events.id"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("revision > 0", name="ck_model_profile_revision"),
    )


def downgrade():
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM model_provider_profiles")).scalar():
        raise RuntimeError("Export protected provider configuration before downgrading")
    op.drop_table("model_provider_profiles")
