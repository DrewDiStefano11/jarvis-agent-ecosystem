from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def now_utc() -> datetime:
    return datetime.now(UTC)


class DepartmentRow(Base):
    __tablename__ = "departments"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text)
    manager_agent_id: Mapped[str | None] = mapped_column(String(80))
    schema_version: Mapped[str] = mapped_column(String(20), default="1.0")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class AgentRow(Base):
    __tablename__ = "agents"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    role: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text)
    department_id: Mapped[str] = mapped_column(ForeignKey("departments.id"), index=True)
    # Self-referential manager integrity is validated by manifests and seeding.
    manager_id: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(40), index=True)
    previous_status: Mapped[str | None] = mapped_column(String(40))
    current_task_id: Mapped[str | None] = mapped_column(String(80))
    progress: Mapped[int] = mapped_column(Integer, default=0)
    status_message: Mapped[str] = mapped_column(Text)
    deployment_status: Mapped[str] = mapped_column(String(40))
    is_temporary: Mapped[bool] = mapped_column(Boolean, default=False)
    schema_version: Mapped[str] = mapped_column(String(20), default="1.0")
    version: Mapped[str] = mapped_column(String(30), default="1.0.0")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class IdentityAgentRow(Base):
    """Authorization identity kept separate from simulator compatibility payloads."""

    __tablename__ = "identity_agents"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    stable_key: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    agent_type: Mapped[str] = mapped_column(String(30), index=True)
    lifecycle_state: Mapped[str] = mapped_column(String(30), index=True, default="provisioned")
    operational_status: Mapped[str] = mapped_column(String(30), index=True, default="offline")
    rank_id: Mapped[str | None] = mapped_column(ForeignKey("identity_ranks.id"), index=True)
    is_system_agent: Mapped[bool] = mapped_column(Boolean, default=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IdentityRankRow(Base):
    __tablename__ = "identity_ranks"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    stable_key: Mapped[str] = mapped_column(String(80), unique=True)
    display_name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    priority_level: Mapped[int] = mapped_column(Integer, unique=True)
    hierarchy_level: Mapped[int] = mapped_column(Integer, unique=True)
    delegation_ceiling: Mapped[int | None] = mapped_column(Integer)
    approval_ceiling: Mapped[float | None] = mapped_column(Numeric(12, 2))
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    __table_args__ = (
        CheckConstraint("hierarchy_level >= 0"),
        CheckConstraint("priority_level >= 0"),
    )


class IdentityRoleRow(Base):
    __tablename__ = "identity_roles"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    stable_key: Mapped[str] = mapped_column(String(80), unique=True)
    display_name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    role_scope: Mapped[str] = mapped_column(String(30))
    is_system_role: Mapped[bool] = mapped_column(Boolean, default=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class IdentityPermissionRow(Base):
    __tablename__ = "identity_permissions"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    stable_key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    resource_type: Mapped[str] = mapped_column(String(60))
    action: Mapped[str] = mapped_column(String(60))
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    __table_args__ = (UniqueConstraint("resource_type", "action"),)


class IdentityCapabilityRow(Base):
    __tablename__ = "identity_capabilities"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    stable_key: Mapped[str] = mapped_column(String(120), unique=True)
    display_name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(60))
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class IdentityTeamRow(Base):
    __tablename__ = "identity_teams"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    stable_key: Mapped[str] = mapped_column(String(80), unique=True)
    display_name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    team_type: Mapped[str] = mapped_column(String(40))
    lifecycle_state: Mapped[str] = mapped_column(String(30), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RolePermissionRow(Base):
    __tablename__ = "identity_role_permissions"
    role_id: Mapped[str] = mapped_column(ForeignKey("identity_roles.id"), primary_key=True)
    permission_id: Mapped[str] = mapped_column(
        ForeignKey("identity_permissions.id"), primary_key=True
    )
    effect: Mapped[str] = mapped_column(String(10), default="allow")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    __table_args__ = (CheckConstraint("effect IN ('allow','deny')"),)


class AgentRoleAssignmentRow(Base):
    __tablename__ = "identity_agent_roles"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("identity_agents.id"), index=True)
    role_id: Mapped[str] = mapped_column(ForeignKey("identity_roles.id"), index=True)
    scope_type: Mapped[str] = mapped_column(String(30), default="global")
    scope_id: Mapped[str | None] = mapped_column(String(120))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assigned_by: Mapped[str | None] = mapped_column(ForeignKey("identity_agents.id"))
    reason: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("agent_id", "role_id", "scope_type", "scope_id", "starts_at"),
        Index(
            "uq_identity_agent_roles_global",
            "agent_id",
            "role_id",
            "scope_type",
            "starts_at",
            unique=True,
            sqlite_where=(scope_type == "global") & scope_id.is_(None),
            postgresql_where=(scope_type == "global") & scope_id.is_(None),
        ),
    )


class AgentPermissionAssignmentRow(Base):
    __tablename__ = "identity_agent_permissions"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("identity_agents.id"), index=True)
    permission_id: Mapped[str] = mapped_column(ForeignKey("identity_permissions.id"), index=True)
    effect: Mapped[str] = mapped_column(String(10))
    resource_type: Mapped[str | None] = mapped_column(String(60))
    resource_id: Mapped[str | None] = mapped_column(String(120))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assigned_by: Mapped[str | None] = mapped_column(ForeignKey("identity_agents.id"))
    reason: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("effect IN ('allow','deny')"),
        CheckConstraint(
            "(resource_type IS NULL AND resource_id IS NULL) OR "
            "(resource_type IS NOT NULL AND resource_id IS NOT NULL)",
            name="ck_identity_agent_permissions_resource_scope",
        ),
        UniqueConstraint(
            "agent_id",
            "permission_id",
            "effect",
            "resource_type",
            "resource_id",
            "starts_at",
        ),
        Index(
            "uq_identity_agent_permissions_global",
            "agent_id",
            "permission_id",
            "effect",
            "starts_at",
            unique=True,
            sqlite_where=resource_type.is_(None) & resource_id.is_(None),
            postgresql_where=resource_type.is_(None) & resource_id.is_(None),
        ),
    )


class AgentCapabilityAssignmentRow(Base):
    __tablename__ = "identity_agent_capabilities"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("identity_agents.id"), index=True)
    capability_id: Mapped[str] = mapped_column(ForeignKey("identity_capabilities.id"), index=True)
    source: Mapped[str] = mapped_column(String(80))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assigned_by: Mapped[str | None] = mapped_column(ForeignKey("identity_agents.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("agent_id", "capability_id", "starts_at"),)


class TeamMembershipRow(Base):
    __tablename__ = "identity_team_memberships"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("identity_teams.id"), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("identity_agents.id"), index=True)
    membership_role: Mapped[str] = mapped_column(String(20), default="member")
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assigned_by: Mapped[str | None] = mapped_column(ForeignKey("identity_agents.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("team_id", "agent_id", "starts_at"),)


class SupervisorRelationshipRow(Base):
    __tablename__ = "identity_supervisor_relationships"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    supervisor_agent_id: Mapped[str] = mapped_column(ForeignKey("identity_agents.id"), index=True)
    subordinate_agent_id: Mapped[str] = mapped_column(ForeignKey("identity_agents.id"), index=True)
    relationship_type: Mapped[str] = mapped_column(String(20))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assigned_by: Mapped[str | None] = mapped_column(ForeignKey("identity_agents.id"))
    reason: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint("supervisor_agent_id <> subordinate_agent_id"),)


class ResourceAccessPolicyRow(Base):
    __tablename__ = "identity_resource_access_policies"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    subject_type: Mapped[str] = mapped_column(String(20), index=True)
    subject_id: Mapped[str | None] = mapped_column(String(80), index=True)
    resource_type: Mapped[str] = mapped_column(String(40), index=True)
    resource_id: Mapped[str] = mapped_column(String(120), index=True)
    action: Mapped[str] = mapped_column(String(30))
    effect: Mapped[str] = mapped_column(String(10))
    access_state: Mapped[str | None] = mapped_column(String(20))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str] = mapped_column(String(500), default="")
    created_by: Mapped[str | None] = mapped_column(ForeignKey("identity_agents.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint("effect IN ('allow','deny')"),)


class DelegationPolicyRow(Base):
    __tablename__ = "identity_delegation_policies"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    delegator_agent_id: Mapped[str] = mapped_column(ForeignKey("identity_agents.id"), index=True)
    target_agent_id: Mapped[str | None] = mapped_column(
        ForeignKey("identity_agents.id"), index=True
    )
    task_category: Mapped[str] = mapped_column(String(80), index=True)
    required_capability_id: Mapped[str | None] = mapped_column(
        ForeignKey("identity_capabilities.id")
    )
    scope_type: Mapped[str] = mapped_column(String(30))
    scope_id: Mapped[str | None] = mapped_column(String(120))
    maximum_target_rank: Mapped[int | None] = mapped_column(Integer)
    maximum_delegation_depth: Mapped[int] = mapped_column(Integer, default=1)
    effect: Mapped[str] = mapped_column(String(10))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str] = mapped_column(String(500), default="")
    assigned_by: Mapped[str | None] = mapped_column(ForeignKey("identity_agents.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("effect IN ('allow','deny')"),
        CheckConstraint("maximum_delegation_depth >= 0"),
    )


class ApprovalAuthorityPolicyRow(Base):
    __tablename__ = "identity_approval_authority_policies"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("identity_agents.id"), index=True)
    may_request_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    may_grant_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    maximum_risk_level: Mapped[str | None] = mapped_column(String(20))
    maximum_cost_usd: Mapped[float | None] = mapped_column(Numeric(12, 2))
    action_category: Mapped[str] = mapped_column(String(80), index=True)
    scope_type: Mapped[str] = mapped_column(String(30))
    scope_id: Mapped[str | None] = mapped_column(String(120))
    effect: Mapped[str] = mapped_column(String(10))
    allow_self_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(ForeignKey("identity_agents.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint("effect IN ('allow','deny')"),)


class SeatPriorityPolicyRow(Base):
    __tablename__ = "identity_seat_priority_policies"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    resource_id: Mapped[str] = mapped_column(String(120), index=True)
    subject_type: Mapped[str] = mapped_column(String(20))
    subject_id: Mapped[str] = mapped_column(String(80), index=True)
    priority_marker: Mapped[str] = mapped_column(String(20))
    reservation_state: Mapped[str] = mapped_column(String(20), default="unreserved")
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IdentityAuditEventRow(Base):
    __tablename__ = "identity_audit_events"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(120), index=True)
    actor_agent_id: Mapped[str | None] = mapped_column(ForeignKey("identity_agents.id"), index=True)
    target_type: Mapped[str] = mapped_column(String(40), index=True)
    target_id: Mapped[str] = mapped_column(String(80), index=True)
    reason: Mapped[str] = mapped_column(String(500), default="")
    changes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, index=True
    )


class TaskRow(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    original_request: Mapped[str] = mapped_column(Text)
    parent_task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"), index=True)
    project_id: Mapped[str | None] = mapped_column(String(80))
    creator: Mapped[str] = mapped_column(String(120))
    assigned_manager_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"))
    priority: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(40), index=True)
    progress: Mapped[int] = mapped_column(Integer)
    status_message: Mapped[str] = mapped_column(Text)
    result: Mapped[str | None] = mapped_column(Text)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    maximum_retries: Mapped[int] = mapped_column(Integer, default=2)
    schema_version: Mapped[str] = mapped_column(String(20), default="1.0")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApprovalRow(Base):
    __tablename__ = "approvals"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    requesting_agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"))
    action_type: Mapped[str] = mapped_column(String(120))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    risk_level: Mapped[str] = mapped_column(String(20))
    affected_resources: Mapped[list[str]] = mapped_column(JSON)
    exact_action_preview: Mapped[str] = mapped_column(Text)
    expected_outcome: Mapped[str] = mapped_column(Text)
    reversal_method: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(30), index=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(120))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(Text)
    schema_version: Mapped[str] = mapped_column(String(20), default="1.0")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ArtifactRow(Base):
    __tablename__ = "artifacts"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    producing_agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"))
    name: Mapped[str] = mapped_column(String(200))
    artifact_type: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(Text)
    content_reference: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    version: Mapped[str] = mapped_column(String(30), default="1.0.0")
    schema_version: Mapped[str] = mapped_column(String(20), default="1.0")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ContextAssemblyRow(Base):
    __tablename__ = "context_assemblies"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    project_id: Mapped[str] = mapped_column(String(120), index=True)
    status: Mapped[str] = mapped_column(String(40), index=True)
    input_hash: Mapped[str] = mapped_column(String(64), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64), index=True)
    policy_version: Mapped[str] = mapped_column(String(80))
    included_source_count: Mapped[int] = mapped_column(Integer)
    excluded_source_count: Mapped[int] = mapped_column(Integer)
    redaction_count: Mapped[int] = mapped_column(Integer)
    injection_finding_count: Mapped[int] = mapped_column(Integer)
    conflict_count: Mapped[int] = mapped_column(Integer)
    schema_version: Mapped[str] = mapped_column(String(20), default="1.0")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NotificationRow(Base):
    __tablename__ = "notifications"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    notification_type: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(Text)
    related_task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"))
    related_agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"))
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    schema_version: Mapped[str] = mapped_column(String(20), default="1.0")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEventRow(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(120), index=True)
    actor: Mapped[str] = mapped_column(String(120), default="system")
    agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"))
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"), index=True)
    approval_id: Mapped[str | None] = mapped_column(ForeignKey("approvals.id"))
    previous_state: Mapped[str | None] = mapped_column(String(80))
    new_state: Mapped[str | None] = mapped_column(String(80))
    correlation_id: Mapped[str] = mapped_column(String(80), index=True)
    sequence_number: Mapped[int] = mapped_column(Integer)
    event_session_id: Mapped[str] = mapped_column(String(80), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    schema_version: Mapped[str] = mapped_column(String(20), default="1.0")


class SystemStateRow(Base):
    __tablename__ = "system_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    emergency_stop: Mapped[bool] = mapped_column(Boolean, default=False)
    simulator_status: Mapped[str] = mapped_column(String(40), default="idle")
    event_session_id: Mapped[str] = mapped_column(String(80))
    current_sequence_number: Mapped[int] = mapped_column(Integer, default=0)
    seed_data_version: Mapped[str] = mapped_column(String(30), default="2.0")
    last_successful_startup: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_clean_shutdown: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    startup_was_clean: Mapped[bool] = mapped_column(Boolean, default=True)
    last_workflow_run_id: Mapped[str | None] = mapped_column(String(80))
    last_checkpoint_id: Mapped[str | None] = mapped_column(String(80))
    recovery_status: Mapped[str] = mapped_column(String(40), default="none")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class WorkflowRunRow(Base):
    __tablename__ = "workflow_runs"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    correlation_id: Mapped[str] = mapped_column(String(80), index=True)
    root_task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"))
    workflow_type: Mapped[str] = mapped_column(String(80))
    workflow_version: Mapped[str] = mapped_column(String(30))
    current_step_index: Mapped[int] = mapped_column(Integer, default=0)
    current_step_identifier: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(40), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pause_reason: Mapped[str | None] = mapped_column(Text)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    checkpoint_id: Mapped[str | None] = mapped_column(String(80))
    resume_eligibility: Mapped[bool] = mapped_column(Boolean, default=True)


class WorkflowCheckpointRow(Base):
    __tablename__ = "workflow_checkpoints"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    workflow_run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id"), index=True)
    workflow_version: Mapped[str] = mapped_column(String(30))
    step_index: Mapped[int] = mapped_column(Integer)
    step_identifier: Mapped[str] = mapped_column(String(120))
    root_task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OutboxEventRow(Base):
    __tablename__ = "outbox_events"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(120), index=True)
    envelope: Mapped[dict[str, Any]] = mapped_column(JSON)
    correlation_id: Mapped[str] = mapped_column(String(80))
    event_session_id: Mapped[str] = mapped_column(String(80), index=True)
    sequence_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), index=True, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    publish_attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    last_publish_error: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (UniqueConstraint("event_session_id", "sequence_number"),)


class IdempotencyRecordRow(Base):
    __tablename__ = "idempotency_records"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    idempotency_key: Mapped[str] = mapped_column(String(200))
    command_type: Mapped[str] = mapped_column(String(120))
    canonical_request_hash: Mapped[str] = mapped_column(String(64))
    response_status: Mapped[int] = mapped_column(Integer)
    response_body: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_resource_id: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expiration_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("idempotency_key", "command_type"),)


class TaskAgentRow(Base):
    __tablename__ = "task_agents"
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), primary_key=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), primary_key=True)


class TaskDependencyRow(Base):
    __tablename__ = "task_dependencies"
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), primary_key=True)
    dependency_task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), primary_key=True)
    dependency_type: Mapped[str] = mapped_column(String(30), default="requires")


class TaskBlockerRow(Base):
    __tablename__ = "task_blockers"
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), primary_key=True)
    blocker_task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), primary_key=True)


class WorkerRow(Base):
    __tablename__ = "workers"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    instance_id: Mapped[str] = mapped_column(String(120), unique=True)
    status: Mapped[str] = mapped_column(String(30), index=True, default="active")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_seconds: Mapped[int] = mapped_column(Integer)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)


class TaskLeaseRow(Base):
    __tablename__ = "task_leases"
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), primary_key=True)
    worker_id: Mapped[str] = mapped_column(ForeignKey("workers.id"), index=True)
    lease_token: Mapped[str] = mapped_column(String(80), unique=True)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    renewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempt_number: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer, default=1)
    checkpoint_id: Mapped[str | None] = mapped_column(
        ForeignKey("workflow_checkpoints.id"), nullable=True
    )


class TaskAttemptRow(Base):
    __tablename__ = "task_attempts"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    attempt_number: Mapped[int] = mapped_column(Integer)
    worker_id: Mapped[str] = mapped_column(ForeignKey("workers.id"), index=True)
    lease_token: Mapped[str] = mapped_column(String(80), unique=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str | None] = mapped_column(String(40))
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    checkpoint_id: Mapped[str | None] = mapped_column(
        ForeignKey("workflow_checkpoints.id"), nullable=True
    )
    __table_args__ = (UniqueConstraint("task_id", "attempt_number"),)

class AgentRuntimeRunRow(Base):
    __tablename__="agent_runtime_runs"
    run_id: Mapped[str]=mapped_column(String(120),primary_key=True); task_id: Mapped[str]=mapped_column(String(120),index=True); agent_id: Mapped[str]=mapped_column(String(120),index=True); parent_run_id: Mapped[str|None]=mapped_column(String(120),index=True); state: Mapped[str]=mapped_column(String(40),index=True); version: Mapped[int]=mapped_column(Integer); event_sequence_number: Mapped[int]=mapped_column(Integer); attempt_count: Mapped[int]=mapped_column(Integer); active_attempt_id: Mapped[str|None]=mapped_column(String(120)); latest_checkpoint_id: Mapped[str|None]=mapped_column(String(120)); recovery_status: Mapped[str]=mapped_column(String(30)); created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),index=True); updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True)); deadline: Mapped[datetime|None]=mapped_column(DateTime(timezone=True),index=True); terminal_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True)); specification_json: Mapped[str]=mapped_column(Text); snapshot_json: Mapped[str]=mapped_column(Text)
    __table_args__=(Index("ix_agent_runtime_runs_nonterminal","state","created_at"),)
class AgentRuntimeEventRow(Base):
    __tablename__="agent_runtime_events"
    event_id: Mapped[str]=mapped_column(String(120),primary_key=True); run_id: Mapped[str]=mapped_column(ForeignKey("agent_runtime_runs.run_id",ondelete="CASCADE"),index=True); attempt_id: Mapped[str|None]=mapped_column(String(120)); event_type: Mapped[str]=mapped_column(String(80)); schema_version: Mapped[str]=mapped_column(String(20)); sequence_number: Mapped[int]=mapped_column(Integer); run_version: Mapped[int]=mapped_column(Integer); timestamp: Mapped[datetime]=mapped_column(DateTime(timezone=True)); actor_reference: Mapped[str|None]=mapped_column(String(160)); command_id: Mapped[str|None]=mapped_column(String(120)); correlation_id: Mapped[str|None]=mapped_column(String(120)); causation_id: Mapped[str|None]=mapped_column(String(120)); payload_json: Mapped[str]=mapped_column(Text); metadata_json: Mapped[str]=mapped_column(Text); envelope_json: Mapped[str]=mapped_column(Text)
    __table_args__=(UniqueConstraint("run_id","sequence_number"),UniqueConstraint("run_id","run_version"))
class AgentRuntimeAttemptRow(Base):
    __tablename__="agent_runtime_attempts"
    attempt_id: Mapped[str]=mapped_column(String(120),primary_key=True); run_id: Mapped[str]=mapped_column(ForeignKey("agent_runtime_runs.run_id",ondelete="CASCADE"),index=True); attempt_number: Mapped[int]=mapped_column(Integer); contract_json: Mapped[str]=mapped_column(Text)
    __table_args__=(UniqueConstraint("run_id","attempt_number"),)
class AgentRuntimeCheckpointRow(Base):
    __tablename__="agent_runtime_checkpoints"
    checkpoint_id: Mapped[str]=mapped_column(String(120),primary_key=True); run_id: Mapped[str]=mapped_column(ForeignKey("agent_runtime_runs.run_id",ondelete="CASCADE"),index=True); attempt_id: Mapped[str]=mapped_column(String(120)); checkpoint_sequence: Mapped[int]=mapped_column(Integer); contract_json: Mapped[str]=mapped_column(Text)
    __table_args__=(UniqueConstraint("run_id","checkpoint_sequence"),)
class AgentRuntimeProcessedCommandRow(Base):
    __tablename__="agent_runtime_processed_commands"
    run_id: Mapped[str]=mapped_column(ForeignKey("agent_runtime_runs.run_id",ondelete="CASCADE"),primary_key=True); command_id: Mapped[str]=mapped_column(String(120),primary_key=True); command_hash: Mapped[str]=mapped_column(String(64)); command_type: Mapped[str]=mapped_column(String(120)); result_json: Mapped[str]=mapped_column(Text); processed_at: Mapped[datetime]=mapped_column(DateTime(timezone=True))
