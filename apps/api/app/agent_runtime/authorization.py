from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app.core.errors import DomainError
from app.identity.service import IdentityService
from app.models.agent_runtime import AgentRunSnapshot, AgentRunSpecification
from app.models.identity import AuthorizationDecision

RUNTIME_ADMIN_PERMISSION = "runtime.admin"
RUNTIME_ADMIN_RESOURCE_TYPE = "administrative_function"
RUNTIME_ADMIN_RESOURCE_ID = "agent_runtime"
RUNTIME_RESOURCE_TYPE = "task"

RUNTIME_PERMISSION_KEYS: dict[str, str] = {
    "read": "runtime.read",
    "create": "runtime.create",
    "queue": "runtime.queue",
    "claim": "runtime.execute",
    "begin_attempt": "runtime.execute",
    "start_attempt": "runtime.execute",
    "heartbeat": "runtime.execute",
    "request_pause": "runtime.pause",
    "confirm_pause": "runtime.pause",
    "resume": "runtime.pause",
    "block": "runtime.pause",
    "unblock": "runtime.pause",
    "request_cancellation": "runtime.cancel",
    "start_cancellation": "runtime.cancel",
    "confirm_cancellation": "runtime.cancel",
    "record_checkpoint": "runtime.checkpoint",
    "complete_attempt": "runtime.complete",
    "fail_attempt": "runtime.complete",
    "timeout_attempt": "runtime.complete",
    "abandon_attempt": "runtime.complete",
    "complete_run": "runtime.complete",
    "fail_run": "runtime.complete",
    "timeout_run": "runtime.complete",
    "abandon_run": "runtime.complete",
    "request_recovery_plan": "runtime.recover",
}


@dataclass(frozen=True)
class RuntimeActorContext:
    actor_id: str
    stable_key: str


@dataclass(frozen=True)
class RuntimeAuthorizationContext:
    actor: RuntimeActorContext
    permission_key: str
    resource_type: str
    resource_id: str
    allowed_by_admin: bool
    decision: AuthorizationDecision
    admin_decision: AuthorizationDecision | None = None
    extra: dict[str, object] = field(default_factory=dict)

    def bounded_metadata(self) -> dict[str, object]:
        metadata = {
            "actorId": self.actor.actor_id,
            "permissionKey": self.permission_key,
            "resourceType": self.resource_type,
            "resourceId": self.resource_id,
            "decisionRule": self.decision.decisive_rule,
            "reasonCode": self.decision.reason_code,
            "grantCount": len(self.decision.matched_grants),
            "denialCount": len(self.decision.matched_denials),
            "allowedByAdmin": self.allowed_by_admin,
        }
        metadata.update(self.extra)
        return metadata


class RuntimeAuthorizer(Protocol):
    def authenticate(self, actor_id: str | None) -> RuntimeActorContext: ...

    def authorize(
        self,
        actor: RuntimeActorContext,
        operation: str,
        *,
        specification: AgentRunSpecification | None = None,
        snapshot: AgentRunSnapshot | None = None,
    ) -> RuntimeAuthorizationContext: ...


class IdentityRuntimeAuthorizer(RuntimeAuthorizer):
    """Runtime authorization adapter over the existing identity/RBAC service.

    Runtime resources are scoped to their authoritative task ID during this local
    control-plane phase. Project-aware filtering can be added later without
    changing runtime command semantics because the adapter is the only place that
    translates runtime snapshots into identity resource checks.
    """

    def __init__(self, identity: IdentityService) -> None:
        self.identity = identity

    def authenticate(self, actor_id: str | None) -> RuntimeActorContext:
        if actor_id is None or not actor_id.strip():
            from app.agent_runtime.errors import RuntimeAuthenticationRequiredError

            raise RuntimeAuthenticationRequiredError()
        try:
            row = self.identity.get_agent(actor_id.strip())
        except DomainError as exc:
            from app.agent_runtime.errors import RuntimeActorNotFoundError

            raise RuntimeActorNotFoundError() from exc
        if row.lifecycle_state != "active" or not row.is_enabled:
            from app.agent_runtime.errors import RuntimeActorInactiveError

            raise RuntimeActorInactiveError(metadata={"actorId": row.id})
        return RuntimeActorContext(actor_id=row.id, stable_key=row.stable_key)

    def authorize(
        self,
        actor: RuntimeActorContext,
        operation: str,
        *,
        specification: AgentRunSpecification | None = None,
        snapshot: AgentRunSnapshot | None = None,
    ) -> RuntimeAuthorizationContext:
        from app.agent_runtime.errors import RuntimePermissionDeniedError

        target = specification or (snapshot.specification if snapshot is not None else None)
        if target is None:
            raise RuntimePermissionDeniedError(metadata={"operation": operation})
        permission_key = RUNTIME_PERMISSION_KEYS[operation]
        resource_id = target.task_id
        admin_decision = self.identity.check_permission(
            actor.actor_id,
            RUNTIME_ADMIN_PERMISSION,
            RUNTIME_ADMIN_RESOURCE_TYPE,
            RUNTIME_ADMIN_RESOURCE_ID,
        )
        if admin_decision.allowed:
            return RuntimeAuthorizationContext(
                actor=actor,
                permission_key=permission_key,
                resource_type=RUNTIME_ADMIN_RESOURCE_TYPE,
                resource_id=RUNTIME_ADMIN_RESOURCE_ID,
                allowed_by_admin=True,
                decision=admin_decision,
                admin_decision=admin_decision,
            )
        decision = self.identity.check_permission_resource_access(
            actor.actor_id,
            permission_key,
            RUNTIME_RESOURCE_TYPE,
            resource_id,
        )
        if not decision.allowed:
            raise RuntimePermissionDeniedError(
                metadata={
                    "permissionKey": permission_key,
                    "resourceType": RUNTIME_RESOURCE_TYPE,
                    "reasonCode": decision.reason_code,
                    "decisionRule": decision.decisive_rule,
                }
            )
        return RuntimeAuthorizationContext(
            actor=actor,
            permission_key=permission_key,
            resource_type=RUNTIME_RESOURCE_TYPE,
            resource_id=resource_id,
            allowed_by_admin=False,
            decision=decision,
            admin_decision=admin_decision,
        )
