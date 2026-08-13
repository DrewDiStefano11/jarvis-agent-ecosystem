from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_KEY = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
AgentType = Literal[
    "system", "coordinator", "supervisor", "specialist", "worker", "reviewer", "monitor"
]
LifecycleState = Literal["provisioned", "active", "suspended", "retired"]
OperationalStatus = Literal[
    "offline", "idle", "available", "assigned", "busy", "waiting", "blocked", "error"
]
ScopeType = Literal["global", "project", "team", "resource"]
ResourceType = Literal[
    "room", "door", "desk", "seat", "project", "tool", "task", "artifact", "administrative_function"
]


class IdentityModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


T = TypeVar("T")


class ResponseMeta(IdentityModel):
    schemaVersion: Literal["1.0"] = "1.0"


class IdentityResponse(IdentityModel, Generic[T]):
    data: T
    meta: ResponseMeta = Field(default_factory=ResponseMeta)


class CreateAgentRequest(IdentityModel):
    stable_key: str = Field(min_length=2, max_length=80)
    display_name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    agent_type: AgentType
    rank_id: str | None = Field(default=None, max_length=80)
    is_system_agent: bool = False

    @field_validator("stable_key")
    @classmethod
    def stable_key_valid(cls, value: str) -> str:
        if not _KEY.fullmatch(value):
            raise ValueError("must be a normalized stable key")
        return value


class UpdateAgentRequest(IdentityModel):
    # These non-nullable types remain omittable through their defaults, while explicit
    # JSON null is rejected before a PATCH can reach non-nullable database columns.
    display_name: str = Field(default=None, min_length=1, max_length=160)
    description: str = Field(default=None, max_length=2000)
    operational_status: OperationalStatus = None
    is_enabled: bool = None


class AgentIdentity(IdentityModel):
    id: str
    stable_key: str
    display_name: str
    description: str
    agent_type: str
    lifecycle_state: str
    operational_status: str
    rank_id: str | None
    is_system_agent: bool
    is_enabled: bool
    version: int
    created_at: datetime
    updated_at: datetime
    retired_at: datetime | None


class DefinitionRequest(IdentityModel):
    stable_key: str = Field(min_length=2, max_length=120)
    display_name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)

    @field_validator("stable_key")
    @classmethod
    def stable_key_valid(cls, value: str) -> str:
        if not _KEY.fullmatch(value):
            raise ValueError("must be a normalized stable key")
        return value


class CreateRankRequest(DefinitionRequest):
    stable_key: str = Field(min_length=2, max_length=80)
    priority_level: int = Field(ge=0)
    hierarchy_level: int = Field(ge=0)
    delegation_ceiling: int | None = Field(default=None, ge=0)
    approval_ceiling: float | None = Field(default=None, ge=0)


class CreateRoleRequest(DefinitionRequest):
    stable_key: str = Field(min_length=2, max_length=80)
    role_scope: ScopeType
    is_system_role: bool = False


class CreatePermissionRequest(DefinitionRequest):
    resource_type: str = Field(min_length=1, max_length=60)
    action: str = Field(min_length=1, max_length=60)


class CreateCapabilityRequest(DefinitionRequest):
    category: str = Field(min_length=1, max_length=60)


class CreateTeamRequest(DefinitionRequest):
    stable_key: str = Field(min_length=2, max_length=80)
    team_type: str = Field(min_length=1, max_length=40)


class TimedAssignment(IdentityModel):
    starts_at: datetime | None = None
    expires_at: datetime | None = None
    assigned_by: str | None = Field(default=None, max_length=80)
    reason: str = Field(default="", max_length=500)

    @field_validator("starts_at", "expires_at")
    @classmethod
    def timestamps_are_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    @model_validator(mode="after")
    def time_order(self):
        if self.starts_at and self.expires_at and self.expires_at <= self.starts_at:
            raise ValueError("expires_at must occur after starts_at")
        return self


class AssignRoleRequest(TimedAssignment):
    role_id: str = Field(max_length=80)
    scope_type: ScopeType = "global"
    scope_id: str | None = Field(default=None, min_length=1, max_length=120)

    @model_validator(mode="after")
    def scope_valid(self):
        if (self.scope_type == "global") != (self.scope_id is None):
            raise ValueError("global scope forbids scope_id; scoped roles require it")
        return self


class AssignPermissionRequest(TimedAssignment):
    permission_id: str = Field(max_length=80)
    effect: Literal["allow", "deny"]
    resource_type: str | None = Field(default=None, min_length=1, max_length=60)
    resource_id: str | None = Field(default=None, min_length=1, max_length=120)

    @model_validator(mode="after")
    def resource_scope_valid(self):
        if (self.resource_type is None) != (self.resource_id is None):
            raise ValueError("resource_type and resource_id must be provided together")
        return self


class AssignCapabilityRequest(TimedAssignment):
    capability_id: str = Field(max_length=80)
    source: str = Field(min_length=1, max_length=80)


class TeamMembershipRequest(TimedAssignment):
    agent_id: str = Field(max_length=80)
    membership_role: Literal["member", "lead", "deputy"] = "member"


class SupervisorRequest(TimedAssignment):
    supervisor_agent_id: str = Field(max_length=80)
    subordinate_agent_id: str = Field(max_length=80)
    relationship_type: Literal["primary", "secondary", "temporary", "functional"]


class PermissionCheckRequest(IdentityModel):
    actor_agent_id: str = Field(max_length=80)
    permission_key: str = Field(max_length=120)
    resource_type: str | None = Field(default=None, max_length=60)
    resource_id: str | None = Field(default=None, max_length=120)


class AuthorizationDecision(IdentityModel):
    allowed: bool
    permission_key: str
    actor_agent_id: str
    resource_type: str | None = None
    resource_id: str | None = None
    matched_grants: list[str] = Field(default_factory=list)
    matched_denials: list[str] = Field(default_factory=list)
    decisive_rule: str
    reason_code: str


class RankIdentity(IdentityModel):
    id: str
    stable_key: str
    display_name: str
    description: str
    priority_level: int
    hierarchy_level: int
    delegation_ceiling: int | None
    approval_ceiling: float | None
    is_enabled: bool
    created_at: datetime
    updated_at: datetime


class RoleIdentity(IdentityModel):
    id: str
    stable_key: str
    display_name: str
    description: str
    role_scope: ScopeType
    is_system_role: bool
    is_enabled: bool
    created_at: datetime
    updated_at: datetime


class PermissionIdentity(IdentityModel):
    id: str
    stable_key: str
    display_name: str
    description: str
    resource_type: str
    action: str
    is_enabled: bool
    created_at: datetime
    updated_at: datetime


class CapabilityIdentity(IdentityModel):
    id: str
    stable_key: str
    display_name: str
    description: str
    category: str
    is_enabled: bool
    created_at: datetime
    updated_at: datetime


class TeamIdentity(IdentityModel):
    id: str
    stable_key: str
    display_name: str
    description: str
    team_type: str
    lifecycle_state: str
    created_at: datetime
    updated_at: datetime
    retired_at: datetime | None


class TimedAssignmentIdentity(IdentityModel):
    id: str
    starts_at: datetime
    expires_at: datetime | None
    assigned_by: str | None
    reason: str
    created_at: datetime
    revoked_at: datetime | None


class RoleAssignmentIdentity(TimedAssignmentIdentity):
    agent_id: str
    role_id: str
    scope_type: ScopeType
    scope_id: str | None


class PermissionAssignmentIdentity(TimedAssignmentIdentity):
    agent_id: str
    permission_id: str
    effect: Literal["allow", "deny"]
    resource_type: str | None
    resource_id: str | None


class RolePermissionIdentity(IdentityModel):
    role_id: str
    permission_id: str
    effect: Literal["allow", "deny"]
    created_at: datetime


class SupervisorRelationshipIdentity(TimedAssignmentIdentity):
    supervisor_agent_id: str
    subordinate_agent_id: str
    relationship_type: Literal["primary", "secondary", "temporary", "functional"]


class ResourcePolicyIdentity(IdentityModel):
    id: str
    subject_type: str
    subject_id: str | None
    resource_type: str
    resource_id: str
    action: str
    effect: Literal["allow", "deny"]
    access_state: str | None
    starts_at: datetime
    expires_at: datetime | None
    reason: str
    created_by: str | None
    created_at: datetime
    revoked_at: datetime | None


class AuditEventIdentity(IdentityModel):
    id: str
    event_type: str
    actor_agent_id: str | None
    target_type: str
    target_id: str
    reason: str
    changes: dict[str, object]
    timestamp: datetime


class ResourcePolicyRequest(IdentityModel):
    subject_type: Literal["agent", "role", "rank", "team", "all"]
    subject_id: str | None = Field(default=None, max_length=80)
    resource_type: ResourceType
    resource_id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    action: Literal["view", "enter", "use", "reserve", "manage", "assign", "modify"]
    effect: Literal["allow", "deny"]
    access_state: Literal["general", "restricted", "temporary", "blocked"] | None = None
    starts_at: datetime | None = None
    expires_at: datetime | None = None
    reason: str = Field(default="", max_length=500)
    created_by: str | None = Field(default=None, max_length=80)

    @field_validator("starts_at", "expires_at")
    @classmethod
    def timestamps_are_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    @model_validator(mode="after")
    def subject_valid(self):
        if (self.subject_type == "all") != (self.subject_id is None):
            raise ValueError("all forbids subject_id; other subjects require it")
        if self.starts_at and self.expires_at and self.expires_at <= self.starts_at:
            raise ValueError("expires_at must occur after starts_at")
        return self
