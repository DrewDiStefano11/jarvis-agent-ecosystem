"""Add durable conversations, original messages and native goal/task links."""

import sqlalchemy as sa
from alembic import op

revision = "20261106_goals"
down_revision = "20261102_groq"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "workspace_conversations",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "workspace_messages",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.String(80),
            sa.ForeignKey("workspace_conversations.id"),
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_workspace_messages_conversation_id", "workspace_messages", ["conversation_id"]
    )
    op.create_table(
        "workspace_goals",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.String(80),
            sa.ForeignKey("workspace_conversations.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("task_id", sa.String(80), sa.ForeignKey("tasks.id"), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_workspace_goals_created_at", "workspace_goals", ["created_at"])


def downgrade():
    for table in ("workspace_goals", "workspace_messages", "workspace_conversations"):
        if op.get_bind().execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar():
            raise RuntimeError("Preserve saved conversations and goals before downgrade")
    op.drop_table("workspace_goals")
    op.drop_table("workspace_messages")
    op.drop_table("workspace_conversations")
