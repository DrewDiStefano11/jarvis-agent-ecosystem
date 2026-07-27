from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import DomainError
from app.db.models import (
    AgentCapabilityAssignmentRow,
    AgentPermissionAssignmentRow,
    AgentRoleAssignmentRow,
    IdentityAgentRow,
    IdentityAuditEventRow,
    IdentityCapabilityRow,
    IdentityPermissionRow,
    IdentityRankRow,
    IdentityRoleRow,
    IdentityTeamRow,
    ResourceAccessPolicyRow,
    RolePermissionRow,
    SupervisorRelationshipRow,
)
from app.models.identity import AuthorizationDecision
from app.services.unit_of_work import UnitOfWork

TRANSITIONS = {
    "provisioned": {"active", "retired"},
    "active": {"suspended", "retired"},
    "suspended": {"active", "retired"},
    "retired": set(),
}
MAX_HIERARCHY_DEPTH = 100


def now() -> datetime:
    return datetime.now(UTC)


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


class IdentityService:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    @staticmethod
    def _audit(
        session: Session,
        event: str,
        target_type: str,
        target_id: str,
        actor: str | None = None,
        reason: str = "",
        changes: dict | None = None,
    ) -> None:
        session.add(
            IdentityAuditEventRow(
                id=uid("iaud"),
                event_type=event,
                actor_agent_id=actor,
                target_type=target_type,
                target_id=target_id,
                reason=reason,
                changes=changes or {},
            )
        )

    @staticmethod
    def _agent(session: Session, agent_id: str) -> IdentityAgentRow:
        row = session.get(IdentityAgentRow, agent_id)
        if not row:
            raise DomainError("AGENT_NOT_FOUND", "Agent identity was not found.", 404)
        return row

    def create_agent(self, data) -> IdentityAgentRow:
        with UnitOfWork(self.sessions) as uow:
            assert uow.session
            if data.rank_id and not uow.session.get(IdentityRankRow, data.rank_id):
                raise DomainError("RANK_NOT_FOUND", "Rank was not found.", 404)
            row = IdentityAgentRow(id=uid("agent"), **data.model_dump())
            uow.session.add(row)
            self._audit(uow.session, "agent.created", "agent", row.id)
            try:
                uow.session.flush()
            except IntegrityError as exc:
                raise DomainError(
                    "DUPLICATE_STABLE_KEY", "Agent stable key already exists.", 409
                ) from exc
            return row

    def list_agents(
        self, offset: int, limit: int, capability: str | None = None
    ) -> list[IdentityAgentRow]:
        with self.sessions() as s:
            query = select(IdentityAgentRow)
            if capability:
                t = now()
                query = (
                    query.join(AgentCapabilityAssignmentRow)
                    .join(IdentityCapabilityRow)
                    .where(
                        IdentityCapabilityRow.stable_key == capability,
                        IdentityCapabilityRow.is_enabled,
                        AgentCapabilityAssignmentRow.revoked_at.is_(None),
                        AgentCapabilityAssignmentRow.starts_at <= t,
                        or_(
                            AgentCapabilityAssignmentRow.expires_at.is_(None),
                            AgentCapabilityAssignmentRow.expires_at > t,
                        ),
                    )
                )
            return list(
                s.scalars(query.order_by(IdentityAgentRow.stable_key).offset(offset).limit(limit))
            )

    def get_agent(self, agent_id: str) -> IdentityAgentRow:
        with self.sessions() as s:
            return self._agent(s, agent_id)

    def update_agent(self, agent_id: str, data) -> IdentityAgentRow:
        with UnitOfWork(self.sessions) as uow:
            assert uow.session
            row = self._agent(uow.session, agent_id)
            for key, value in data.model_dump(exclude_unset=True).items():
                setattr(row, key, value)
            row.version += 1
            row.updated_at = now()
            self._audit(
                uow.session,
                "agent.updated",
                "agent",
                row.id,
                changes=data.model_dump(exclude_unset=True),
            )
            return row

    def transition(
        self, agent_id: str, target: str, actor: str | None = None, reason: str = ""
    ) -> IdentityAgentRow:
        with UnitOfWork(self.sessions) as uow:
            assert uow.session
            row = self._agent(uow.session, agent_id)
            if target == row.lifecycle_state:
                return row
            if target not in TRANSITIONS[row.lifecycle_state]:
                raise DomainError(
                    "INVALID_LIFECYCLE_TRANSITION",
                    f"Cannot transition from {row.lifecycle_state} to {target}.",
                    409,
                )
            previous = row.lifecycle_state
            row.lifecycle_state = target
            row.updated_at = now()
            row.version += 1
            if target in {"suspended", "retired"}:
                row.operational_status = "offline"
            if target == "retired":
                row.retired_at = now()
                row.is_enabled = False
            self._audit(
                uow.session,
                f"agent.{target}",
                "agent",
                row.id,
                actor,
                reason,
                {"lifecycle_state": [previous, target]},
            )
            return row

    def create_definition(self, kind: str, data):
        models = {
            "rank": IdentityRankRow,
            "role": IdentityRoleRow,
            "permission": IdentityPermissionRow,
            "capability": IdentityCapabilityRow,
            "team": IdentityTeamRow,
        }
        with UnitOfWork(self.sessions) as uow:
            assert uow.session
            row = models[kind](id=uid(kind), **data.model_dump())
            uow.session.add(row)
            self._audit(uow.session, f"{kind}.created", kind, row.id)
            try:
                uow.session.flush()
            except IntegrityError as exc:
                raise DomainError(
                    "DUPLICATE_DEFINITION", f"Duplicate {kind} definition.", 409
                ) from exc
            return row

    def list_definitions(self, kind: str, offset: int, limit: int):
        model = {
            "rank": IdentityRankRow,
            "role": IdentityRoleRow,
            "permission": IdentityPermissionRow,
            "capability": IdentityCapabilityRow,
            "team": IdentityTeamRow,
        }[kind]
        order = model.hierarchy_level if kind == "rank" else model.stable_key
        with self.sessions() as s:
            return list(
                s.scalars(select(model).order_by(order, model.id).offset(offset).limit(limit))
            )

    def assign_role(self, agent_id: str, data):
        with UnitOfWork(self.sessions) as uow:
            assert uow.session
            agent = self._agent(uow.session, agent_id)
            role = uow.session.get(IdentityRoleRow, data.role_id)
            if agent.lifecycle_state == "retired" or not agent.is_enabled:
                raise DomainError(
                    "AGENT_INACTIVE", "Inactive agents cannot receive assignments.", 409
                )
            if not role:
                raise DomainError("ROLE_NOT_FOUND", "Role was not found.", 404)
            if not role.is_enabled:
                raise DomainError("ROLE_DISABLED", "Disabled roles cannot be assigned.", 409)
            values = data.model_dump()
            values["starts_at"] = values["starts_at"] or now()
            row = AgentRoleAssignmentRow(id=uid("arole"), agent_id=agent_id, **values)
            uow.session.add(row)
            self._audit(
                uow.session, "role.assigned", "agent", agent_id, data.assigned_by, data.reason
            )
            try:
                uow.session.flush()
            except IntegrityError as exc:
                raise DomainError(
                    "DUPLICATE_ASSIGNMENT", "Equivalent assignment already exists.", 409
                ) from exc
            return row

    def attach_permission(self, role_id: str, permission_id: str, effect: str):
        with UnitOfWork(self.sessions) as uow:
            assert uow.session
            if not uow.session.get(IdentityRoleRow, role_id):
                raise DomainError("ROLE_NOT_FOUND", "Role was not found.", 404)
            if not uow.session.get(IdentityPermissionRow, permission_id):
                raise DomainError("PERMISSION_NOT_FOUND", "Permission was not found.", 404)
            row = RolePermissionRow(role_id=role_id, permission_id=permission_id, effect=effect)
            uow.session.add(row)
            self._audit(uow.session, "role.permission_attached", "role", role_id)
            try:
                uow.session.flush()
            except IntegrityError as exc:
                raise DomainError(
                    "DUPLICATE_ASSIGNMENT", "Permission is already attached.", 409
                ) from exc
            return row

    def assign_permission(self, agent_id: str, data):
        with UnitOfWork(self.sessions) as uow:
            assert uow.session
            agent = self._agent(uow.session, agent_id)
            permission = uow.session.get(IdentityPermissionRow, data.permission_id)
            if agent.lifecycle_state != "active" or not agent.is_enabled:
                raise DomainError(
                    "AGENT_INACTIVE", "Only active agents may receive permission assignments.", 409
                )
            if not permission:
                raise DomainError("PERMISSION_NOT_FOUND", "Permission was not found.", 404)
            values = data.model_dump()
            values["starts_at"] = values["starts_at"] or now()
            row = AgentPermissionAssignmentRow(id=uid("aperm"), agent_id=agent_id, **values)
            uow.session.add(row)
            self._audit(
                uow.session,
                f"permission.{'granted' if data.effect == 'allow' else 'denied'}",
                "agent",
                agent_id,
                data.assigned_by,
                data.reason,
            )
            try:
                uow.session.flush()
            except IntegrityError as exc:
                raise DomainError(
                    "DUPLICATE_ASSIGNMENT", "Equivalent assignment already exists.", 409
                ) from exc
            return row

    def check_permission(
        self,
        actor_id: str,
        permission_key: str,
        resource_type: str | None = None,
        resource_id: str | None = None,
        at: datetime | None = None,
    ) -> AuthorizationDecision:
        t = at or now()
        grants = []
        denials = []
        try:
            with self.sessions() as s:
                actor = s.get(IdentityAgentRow, actor_id)
                base = dict(
                    permission_key=permission_key,
                    actor_agent_id=actor_id,
                    resource_type=resource_type,
                    resource_id=resource_id,
                )
                if not actor or actor.lifecycle_state != "active" or not actor.is_enabled:
                    return AuthorizationDecision(
                        allowed=False,
                        matched_grants=[],
                        matched_denials=[],
                        decisive_rule="actor_state",
                        reason_code="actor_inactive",
                        **base,
                    )
                permission = s.scalar(
                    select(IdentityPermissionRow).where(
                        IdentityPermissionRow.stable_key == permission_key,
                        IdentityPermissionRow.is_enabled,
                    )
                )
                if not permission:
                    return AuthorizationDecision(
                        allowed=False,
                        matched_grants=[],
                        matched_denials=[],
                        decisive_rule="definition",
                        reason_code="permission_unknown",
                        **base,
                    )
                active = and_(
                    AgentPermissionAssignmentRow.agent_id == actor_id,
                    AgentPermissionAssignmentRow.permission_id == permission.id,
                    AgentPermissionAssignmentRow.revoked_at.is_(None),
                    AgentPermissionAssignmentRow.starts_at <= t,
                    or_(
                        AgentPermissionAssignmentRow.expires_at.is_(None),
                        AgentPermissionAssignmentRow.expires_at > t,
                    ),
                )
                for a in s.scalars(select(AgentPermissionAssignmentRow).where(active)):
                    applicable = (a.resource_type is None or a.resource_type == resource_type) and (
                        a.resource_id is None or a.resource_id == resource_id
                    )
                    if applicable:
                        (denials if a.effect == "deny" else grants).append(f"direct:{a.id}")
                role_rows = s.execute(
                    select(RolePermissionRow, AgentRoleAssignmentRow)
                    .join(
                        AgentRoleAssignmentRow,
                        AgentRoleAssignmentRow.role_id == RolePermissionRow.role_id,
                    )
                    .join(IdentityRoleRow)
                    .where(
                        AgentRoleAssignmentRow.agent_id == actor_id,
                        RolePermissionRow.permission_id == permission.id,
                        IdentityRoleRow.is_enabled,
                        AgentRoleAssignmentRow.revoked_at.is_(None),
                        AgentRoleAssignmentRow.starts_at <= t,
                        or_(
                            AgentRoleAssignmentRow.expires_at.is_(None),
                            AgentRoleAssignmentRow.expires_at > t,
                        ),
                    )
                ).all()
                for rp, assignment in role_rows:
                    (denials if rp.effect == "deny" else grants).append(
                        f"role:{assignment.role_id}"
                    )
                if denials:
                    return AuthorizationDecision(
                        allowed=False,
                        matched_grants=grants,
                        matched_denials=denials,
                        decisive_rule="explicit_deny",
                        reason_code="explicit_denial",
                        **base,
                    )
                return AuthorizationDecision(
                    allowed=bool(grants),
                    matched_grants=grants,
                    matched_denials=[],
                    decisive_rule="grant" if grants else "default_deny",
                    reason_code="direct_grant"
                    if grants and grants[0].startswith("direct")
                    else "role_grant"
                    if grants
                    else "no_applicable_grant",
                    **base,
                )
        except Exception:
            return AuthorizationDecision(
                allowed=False,
                permission_key=permission_key,
                actor_agent_id=actor_id,
                resource_type=resource_type,
                resource_id=resource_id,
                matched_grants=[],
                matched_denials=[],
                decisive_rule="fail_closed",
                reason_code="evaluation_failed",
            )

    def add_supervisor(self, data):
        if data.supervisor_agent_id == data.subordinate_agent_id:
            raise DomainError("SELF_SUPERVISION", "An agent cannot supervise itself.", 409)
        with UnitOfWork(self.sessions) as uow:
            assert uow.session
            for aid in (data.supervisor_agent_id, data.subordinate_agent_id):
                a = self._agent(uow.session, aid)
                if a.lifecycle_state == "retired":
                    raise DomainError(
                        "AGENT_INACTIVE",
                        "Retired agents cannot enter hierarchy relationships.",
                        409,
                    )
            if self._reachable(uow.session, data.subordinate_agent_id, data.supervisor_agent_id):
                raise DomainError(
                    "HIERARCHY_CYCLE", "Relationship would create a hierarchy cycle.", 409
                )
            if data.relationship_type == "primary":
                existing = uow.session.scalar(
                    select(SupervisorRelationshipRow).where(
                        SupervisorRelationshipRow.subordinate_agent_id == data.subordinate_agent_id,
                        SupervisorRelationshipRow.relationship_type == "primary",
                        SupervisorRelationshipRow.revoked_at.is_(None),
                    )
                )
                if existing:
                    raise DomainError(
                        "PRIMARY_SUPERVISOR_EXISTS", "Agent already has a primary supervisor.", 409
                    )
            values = data.model_dump()
            values["starts_at"] = values["starts_at"] or now()
            row = SupervisorRelationshipRow(id=uid("rel"), **values)
            uow.session.add(row)
            self._audit(
                uow.session,
                "hierarchy.relationship_added",
                "agent",
                data.subordinate_agent_id,
                data.assigned_by,
                data.reason,
            )
            return row

    def _reachable(self, s: Session, start: str, target: str) -> bool:
        frontier = [start]
        seen = set()
        t = now()
        for _ in range(MAX_HIERARCHY_DEPTH):
            if not frontier:
                return False
            if target in frontier:
                return True
            seen.update(frontier)
            frontier = list(
                s.scalars(
                    select(SupervisorRelationshipRow.subordinate_agent_id).where(
                        SupervisorRelationshipRow.supervisor_agent_id.in_(frontier),
                        SupervisorRelationshipRow.revoked_at.is_(None),
                        SupervisorRelationshipRow.starts_at <= t,
                        or_(
                            SupervisorRelationshipRow.expires_at.is_(None),
                            SupervisorRelationshipRow.expires_at > t,
                        ),
                        SupervisorRelationshipRow.subordinate_agent_id.not_in(seen),
                    )
                )
            )
        raise DomainError(
            "HIERARCHY_DEPTH_EXCEEDED", "Hierarchy traversal exceeded its safe depth.", 409
        )

    def descendants(self, agent_id: str) -> list[str]:
        with self.sessions() as s:
            self._agent(s, agent_id)
            result = []
            frontier = [agent_id]
            for _ in range(MAX_HIERARCHY_DEPTH):
                children = list(
                    s.scalars(
                        select(SupervisorRelationshipRow.subordinate_agent_id)
                        .where(
                            SupervisorRelationshipRow.supervisor_agent_id.in_(frontier),
                            SupervisorRelationshipRow.revoked_at.is_(None),
                        )
                        .order_by(SupervisorRelationshipRow.subordinate_agent_id)
                    )
                )
                frontier = [x for x in children if x not in result]
                result.extend(frontier)
                if not frontier:
                    return result
            raise DomainError(
                "HIERARCHY_DEPTH_EXCEEDED", "Hierarchy traversal exceeded its safe depth.", 409
            )

    def create_resource_policy(self, data):
        with UnitOfWork(self.sessions) as uow:
            assert uow.session
            values = data.model_dump()
            values["starts_at"] = values["starts_at"] or now()
            row = ResourceAccessPolicyRow(id=uid("policy"), **values)
            uow.session.add(row)
            self._audit(
                uow.session,
                "access_policy.created",
                "access_policy",
                row.id,
                data.created_by,
                data.reason,
            )
            return row

    def check_resource_access(
        self, actor_id: str, resource_type: str, resource_id: str, action: str
    ) -> AuthorizationDecision:
        base = self.check_permission(
            actor_id, f"{resource_type}.{action}", resource_type, resource_id
        )
        with self.sessions() as s:
            actor = s.get(IdentityAgentRow, actor_id)
            if not actor or actor.lifecycle_state != "active" or not actor.is_enabled:
                return base
            t = now()
            policies = list(
                s.scalars(
                    select(ResourceAccessPolicyRow).where(
                        ResourceAccessPolicyRow.resource_type == resource_type,
                        ResourceAccessPolicyRow.resource_id == resource_id,
                        ResourceAccessPolicyRow.action == action,
                        ResourceAccessPolicyRow.revoked_at.is_(None),
                        ResourceAccessPolicyRow.starts_at <= t,
                        or_(
                            ResourceAccessPolicyRow.expires_at.is_(None),
                            ResourceAccessPolicyRow.expires_at > t,
                        ),
                        or_(
                            and_(
                                ResourceAccessPolicyRow.subject_type == "agent",
                                ResourceAccessPolicyRow.subject_id == actor_id,
                            ),
                            ResourceAccessPolicyRow.subject_type == "all",
                        ),
                    )
                )
            )
            denies = [
                f"policy:{p.id}"
                for p in policies
                if p.effect == "deny" or p.access_state == "blocked"
            ]
            allows = [f"policy:{p.id}" for p in policies if p.effect == "allow"]
            if denies:
                return AuthorizationDecision(
                    allowed=False,
                    permission_key=base.permission_key,
                    actor_agent_id=actor_id,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    matched_grants=base.matched_grants + allows,
                    matched_denials=denies,
                    decisive_rule="resource_deny",
                    reason_code="resource_denial",
                )
            if allows:
                return AuthorizationDecision(
                    allowed=True,
                    permission_key=base.permission_key,
                    actor_agent_id=actor_id,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    matched_grants=base.matched_grants + allows,
                    matched_denials=[],
                    decisive_rule="resource_policy",
                    reason_code="direct_grant",
                )
            return base

    def audits(self, offset: int, limit: int, event_type: str | None = None):
        with self.sessions() as s:
            q = select(IdentityAuditEventRow)
            if event_type:
                q = q.where(IdentityAuditEventRow.event_type == event_type)
            return list(
                s.scalars(
                    q.order_by(IdentityAuditEventRow.timestamp.desc(), IdentityAuditEventRow.id)
                    .offset(offset)
                    .limit(limit)
                )
            )
