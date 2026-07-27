from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

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
    model_config = ConfigDict(from_attributes=True)


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
    display_name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    operational_status: OperationalStatus | None = None
    is_enabled: bool | None = None


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
    priority_level: int = Field(ge=0)
    hierarchy_level: int = Field(ge=0)
    delegation_ceiling: int | None = Field(default=None, ge=0)
    approval_ceiling: float | None = Field(default=None, ge=0)


class CreateRoleRequest(DefinitionRequest):
    role_scope: ScopeType
    is_system_role: bool = False


class CreatePermissionRequest(DefinitionRequest):
    resource_type: str = Field(min_length=1, max_length=60)
    action: str = Field(min_length=1, max_length=60)


class CreateCapabilityRequest(DefinitionRequest):
    category: str = Field(min_length=1, max_length=60)


class CreateTeamRequest(DefinitionRequest):
    team_type: str = Field(min_length=1, max_length=40)


class TimedAssignment(IdentityModel):
    starts_at: datetime | None = None
    expires_at: datetime | None = None
    assigned_by: str | None = Field(default=None, max_length=80)
    reason: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def time_order(self):
        if self.starts_at and self.expires_at and self.expires_at <= self.starts_at:
            raise ValueError("expires_at must occur after starts_at")
        return self


class AssignRoleRequest(TimedAssignment):
    role_id: str = Field(max_length=80)
    scope_type: ScopeType = "global"
    scope_id: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def scope_valid(self):
        if (self.scope_type == "global") != (self.scope_id is None):
            raise ValueError("global scope forbids scope_id; scoped roles require it")
        return self


class AssignPermissionRequest(TimedAssignment):
    permission_id: str = Field(max_length=80)
    effect: Literal["allow", "deny"]
    resource_type: str | None = Field(default=None, max_length=60)
    resource_id: str | None = Field(default=None, max_length=120)


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

    @model_validator(mode="after")
    def subject_valid(self):
        if (self.subject_type == "all") != (self.subject_id is None):
            raise ValueError("all forbids subject_id; other subjects require it")
        if self.starts_at and self.expires_at and self.expires_at <= self.starts_at:
            raise ValueError("expires_at must occur after starts_at")
        return self
