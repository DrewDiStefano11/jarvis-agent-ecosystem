"""bootstrap trusted-local runtime authorization catalog

Revision ID: 20260727_06
Revises: 20260727_05
"""

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "20260727_06"
down_revision = "20260727_05"
branch_labels = None
depends_on = None

OPERATION_CAPABILITIES = {
    "read": None,
    "create": "agent_runtime.control",
    "transition": "agent_runtime.control",
    "cancel": "agent_runtime.control",
    "recover": "agent_runtime.recovery",
    "integrity_check": "agent_runtime.recovery",
}
SCOPE_DEFINITIONS = {
    "global": ("administrative_function", None),
    "task": ("task", None),
    "run": ("resource", None),
}
OPERATOR_ID = "agent-runtime-local-operator"
ROLE_ID = "role-runtime-local-operator"


def _permission_id(operation: str, scope: str) -> str:
    return f"perm-runtime-{operation}-{scope}"


def _capability_id(key: str) -> str:
    return f"cap-runtime-{key.rsplit('.', 1)[1]}"


def _require_same(connection, table: str, identifier: str, stable_key: str, expected: dict) -> None:
    row = (
        connection.execute(
            sa.text(f"SELECT * FROM {table} WHERE id = :id OR stable_key = :stable_key"),
            {"id": identifier, "stable_key": stable_key},
        )
        .mappings()
        .first()
    )
    if row is None:
        return
    if (
        row["id"] != identifier
        or row["stable_key"] != stable_key
        or any(row[key] != value for key, value in expected.items())
    ):
        raise RuntimeError(f"Runtime authorization bootstrap conflict in {table}: {stable_key}")


def upgrade() -> None:
    connection = op.get_bind()
    timestamp = datetime.now(UTC)
    permissions = []
    for operation in OPERATION_CAPABILITIES:
        for scope, (resource_type, _) in SCOPE_DEFINITIONS.items():
            key = f"agent_runtime.{operation}.{scope}"
            identifier = _permission_id(operation, scope)
            expected = {"resource_type": resource_type, "action": operation, "is_enabled": True}
            _require_same(connection, "identity_permissions", identifier, key, expected)
            permissions.append((identifier, key, resource_type, operation))
    for identifier, key, resource_type, operation in permissions:
        exists = connection.execute(
            sa.text("SELECT 1 FROM identity_permissions WHERE id = :id"), {"id": identifier}
        ).scalar()
        if not exists:
            connection.execute(
                sa.text(
                    """INSERT INTO identity_permissions
                    (id, stable_key, display_name, description, resource_type, action, is_enabled, created_at, updated_at)
                    VALUES (:id, :key, :name, :description, :resource_type, :action, 1, :timestamp, :timestamp)"""
                ),
                {
                    "id": identifier,
                    "key": key,
                    "name": key,
                    "description": "Trusted-local runtime permission.",
                    "resource_type": resource_type,
                    "action": operation,
                    "timestamp": timestamp,
                },
            )
    for key in ("agent_runtime.control", "agent_runtime.recovery"):
        identifier = _capability_id(key)
        _require_same(
            connection,
            "identity_capabilities",
            identifier,
            key,
            {"category": "agent_runtime", "is_enabled": True},
        )
        if not connection.execute(
            sa.text("SELECT 1 FROM identity_capabilities WHERE id = :id"), {"id": identifier}
        ).scalar():
            connection.execute(
                sa.text("""INSERT INTO identity_capabilities
                (id, stable_key, display_name, description, category, is_enabled, created_at, updated_at)
                VALUES (:id, :key, :key, 'Trusted-local runtime eligibility capability.', 'agent_runtime', 1, :timestamp, :timestamp)"""),
                {"id": identifier, "key": key, "timestamp": timestamp},
            )
    _require_same(
        connection,
        "identity_agents",
        OPERATOR_ID,
        "agent.runtime-local-operator",
        {
            "agent_type": "system",
            "lifecycle_state": "active",
            "operational_status": "available",
            "is_system_agent": True,
            "is_enabled": True,
        },
    )
    if not connection.execute(
        sa.text("SELECT 1 FROM identity_agents WHERE id = :id"), {"id": OPERATOR_ID}
    ).scalar():
        connection.execute(
            sa.text("""INSERT INTO identity_agents
            (id, stable_key, display_name, description, agent_type, lifecycle_state, operational_status, rank_id, is_system_agent, is_enabled, version, created_at, updated_at, retired_at)
            VALUES (:id, 'agent.runtime-local-operator', 'Runtime local operator', 'Trusted-local authorization subject; not authenticated.', 'system', 'active', 'available', NULL, 1, 1, 1, :timestamp, :timestamp, NULL)"""),
            {"id": OPERATOR_ID, "timestamp": timestamp},
        )
    _require_same(
        connection,
        "identity_roles",
        ROLE_ID,
        "role.runtime-local-operator",
        {"role_scope": "global", "is_system_role": True, "is_enabled": True},
    )
    if not connection.execute(
        sa.text("SELECT 1 FROM identity_roles WHERE id = :id"), {"id": ROLE_ID}
    ).scalar():
        connection.execute(
            sa.text("""INSERT INTO identity_roles
            (id, stable_key, display_name, description, role_scope, is_system_role, is_enabled, created_at, updated_at)
            VALUES (:id, 'role.runtime-local-operator', 'Runtime local operator', 'Trusted-local runtime role.', 'global', 1, 1, :timestamp, :timestamp)"""),
            {"id": ROLE_ID, "timestamp": timestamp},
        )
    for permission_id, *_ in permissions:
        connection.execute(
            sa.text(
                "INSERT OR IGNORE INTO identity_role_permissions (role_id, permission_id, effect, created_at) VALUES (:role_id, :permission_id, 'allow', :timestamp)"
            ),
            {"role_id": ROLE_ID, "permission_id": permission_id, "timestamp": timestamp},
        )
    connection.execute(
        sa.text(
            "INSERT OR IGNORE INTO identity_agent_roles (id, agent_id, role_id, scope_type, scope_id, starts_at, expires_at, assigned_by, reason, created_at, revoked_at) VALUES ('assign-runtime-local-operator-role', :agent_id, :role_id, 'global', NULL, :timestamp, NULL, NULL, 'Runtime bootstrap', :timestamp, NULL)"
        ),
        {"agent_id": OPERATOR_ID, "role_id": ROLE_ID, "timestamp": timestamp},
    )
    for key in ("agent_runtime.control", "agent_runtime.recovery"):
        connection.execute(
            sa.text(
                "INSERT OR IGNORE INTO identity_agent_capabilities (id, agent_id, capability_id, source, starts_at, expires_at, assigned_by, created_at, revoked_at) VALUES (:id, :agent_id, :capability_id, 'runtime-bootstrap', :timestamp, NULL, NULL, :timestamp, NULL)"
            ),
            {
                "id": f"assign-runtime-local-{key.rsplit('.', 1)[1]}",
                "agent_id": OPERATOR_ID,
                "capability_id": _capability_id(key),
                "timestamp": timestamp,
            },
        )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "DELETE FROM identity_agent_capabilities WHERE agent_id = :id AND source = 'runtime-bootstrap'"
        ),
        {"id": OPERATOR_ID},
    )
    connection.execute(
        sa.text(
            "DELETE FROM identity_agent_roles WHERE id = 'assign-runtime-local-operator-role' AND agent_id = :id"
        ),
        {"id": OPERATOR_ID},
    )
    connection.execute(
        sa.text("DELETE FROM identity_role_permissions WHERE role_id = :id"), {"id": ROLE_ID}
    )
    connection.execute(
        sa.text(
            "DELETE FROM identity_roles WHERE id = :id AND stable_key = 'role.runtime-local-operator'"
        ),
        {"id": ROLE_ID},
    )
    connection.execute(
        sa.text(
            "DELETE FROM identity_agents WHERE id = :id AND stable_key = 'agent.runtime-local-operator'"
        ),
        {"id": OPERATOR_ID},
    )
    connection.execute(
        sa.text(
            "DELETE FROM identity_capabilities WHERE id IN ('cap-runtime-control', 'cap-runtime-recovery')"
        )
    )
    connection.execute(sa.text("DELETE FROM identity_permissions WHERE id LIKE 'perm-runtime-%'"))
