"""Persist local office assignments and idempotent movement intents."""

import sqlalchemy as sa
from alembic import op

revision = "20260905_07"
down_revision = "20260905_06"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "office_placements",
        sa.Column(
            "identity_id", sa.String(80), sa.ForeignKey("identity_agents.id"), primary_key=True
        ),
        sa.Column("station_id", sa.String(100), nullable=True, unique=True),
        sa.Column("sprite_id", sa.String(80), nullable=False),
        sa.Column("position_json", sa.JSON(), nullable=False),
        sa.Column("motion_json", sa.JSON(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "office_commands",
        sa.Column("command_id", sa.String(120), primary_key=True),
        sa.Column(
            "identity_id", sa.String(80), sa.ForeignKey("identity_agents.id"), nullable=False
        ),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("response_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_office_commands_identity_id", "office_commands", ["identity_id"])


def downgrade() -> None:
    op.drop_table("office_commands")
    op.drop_table("office_placements")
