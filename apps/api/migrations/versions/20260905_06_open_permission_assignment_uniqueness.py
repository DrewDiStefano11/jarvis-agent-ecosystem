"""make open permission assignment convergence a database invariant

Revision ID: 20260905_06
Revises: 20260729_05
Create Date: 2026-09-05 02:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260905_06"
down_revision = "20260729_05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    duplicate = connection.execute(
        sa.text(
            """
            SELECT agent_id, permission_id, effect, resource_type, resource_id
            FROM identity_agent_permissions
            WHERE revoked_at IS NULL AND expires_at IS NULL
            GROUP BY agent_id, permission_id, effect, resource_type, resource_id
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )
    ).first()
    if duplicate is not None:
        raise RuntimeError(
            "Open permission assignments contain duplicates; repair the append-only "
            "assignment history before applying 20260905_06."
        )

    op.create_index(
        "uq_identity_agent_permissions_open_global",
        "identity_agent_permissions",
        ["agent_id", "permission_id", "effect"],
        unique=True,
        sqlite_where=sa.text(
            "resource_type IS NULL AND resource_id IS NULL "
            "AND revoked_at IS NULL AND expires_at IS NULL"
        ),
        postgresql_where=sa.text(
            "resource_type IS NULL AND resource_id IS NULL "
            "AND revoked_at IS NULL AND expires_at IS NULL"
        ),
    )
    op.create_index(
        "uq_identity_agent_permissions_open_scoped",
        "identity_agent_permissions",
        ["agent_id", "permission_id", "effect", "resource_type", "resource_id"],
        unique=True,
        sqlite_where=sa.text(
            "resource_type IS NOT NULL AND resource_id IS NOT NULL "
            "AND revoked_at IS NULL AND expires_at IS NULL"
        ),
        postgresql_where=sa.text(
            "resource_type IS NOT NULL AND resource_id IS NOT NULL "
            "AND revoked_at IS NULL AND expires_at IS NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_identity_agent_permissions_open_scoped",
        table_name="identity_agent_permissions",
    )
    op.drop_index(
        "uq_identity_agent_permissions_open_global",
        table_name="identity_agent_permissions",
    )
