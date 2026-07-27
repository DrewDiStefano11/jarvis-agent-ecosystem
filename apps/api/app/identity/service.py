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
    TeamMembershipRow,
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


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def effective_interval(data) -> tuple[datetime, datetime | None]:
    starts_at = utc(data.starts_at) if data.starts_at is not None else now()
    expires_at = utc(data.expires_at) if data.expires_at is not None else None
    if expires_at is not None and expires_at <= starts_at:
        raise DomainError(
            "INVALID_EFFECTIVE_INTERVAL",
            "expires_at must occur after the effective starts_at.",
            422,
        )
    return starts_at, expires_at


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

    def _validate_attribution(self, session: Session, agent_id: str | None) -> None:
        if agent_id is not None:
            self._agent(session, agent_id)

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
            changes = data.model_dump(exclude_unset=True)
            if row.lifecycle_state == "retired" and (
                changes.get("is_enabled") is True
                or ("operational_status" in changes and changes["operational_status"] != "offline")
            ):
                raise DomainError(
                    "RETIRED_AGENT_STATE_CONFLICT",
                    "Retired agents must remain disabled and offline.",
                    409,
                )
            for key, value in changes.items():
                setattr(row, key, value)
            row.version += 1
            row.updated_at = now()
            self._audit(
                uow.session,
                "agent.updated",
                "agent",
                row.id,
                changes=changes,
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
            self._validate_attribution(uow.session, actor)
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
            # Role definitions are authorization boundaries: assignments may not widen or
            # reinterpret them. Exact scope matching is the intentionally conservative policy.
            if data.scope_type != role.role_scope:
                raise DomainError(
                    "ROLE_SCOPE_MISMATCH",
                    "Assignment scope must match the role's declared scope.",
                    409,
                )
            self._validate_attribution(uow.session, data.assigned_by)
            starts_at, expires_at = effective_interval(data)
            infinity = datetime.max.replace(tzinfo=UTC)
            duplicate = uow.session.scalar(
                select(AgentRoleAssignmentRow.id).where(
                    AgentRoleAssignmentRow.agent_id == agent_id,
                    AgentRoleAssignmentRow.role_id == data.role_id,
                    AgentRoleAssignmentRow.scope_type == data.scope_type,
                    AgentRoleAssignmentRow.revoked_at.is_(None),
                    AgentRoleAssignmentRow.starts_at
                    < (expires_at if expires_at is not None else infinity),
                    or_(
                        AgentRoleAssignmentRow.expires_at.is_(None),
                        AgentRoleAssignmentRow.expires_at > starts_at,
                    ),
                    AgentRoleAssignmentRow.scope_id.is_(None)
                    if data.scope_id is None
                    else AgentRoleAssignmentRow.scope_id == data.scope_id,
                )
            )
            if duplicate:
                raise DomainError(
                    "DUPLICATE_ASSIGNMENT", "Equivalent assignment already exists.", 409
                )
            values = data.model_dump()
            values["starts_at"] = starts_at
            values["expires_at"] = expires_at
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
            if data.resource_type is not None and data.resource_type != permission.resource_type:
                raise DomainError(
                    "PERMISSION_SCOPE_MISMATCH",
                    "Assignment resource type must match the permission definition.",
                    409,
                )
            self._validate_attribution(uow.session, data.assigned_by)
            starts_at, expires_at = effective_interval(data)
            infinity = datetime.max.replace(tzinfo=UTC)
            duplicate = uow.session.scalar(
                select(AgentPermissionAssignmentRow.id).where(
                    AgentPermissionAssignmentRow.agent_id == agent_id,
                    AgentPermissionAssignmentRow.permission_id == data.permission_id,
                    AgentPermissionAssignmentRow.effect == data.effect,
                    AgentPermissionAssignmentRow.revoked_at.is_(None),
                    AgentPermissionAssignmentRow.starts_at
                    < (expires_at if expires_at is not None else infinity),
                    or_(
                        AgentPermissionAssignmentRow.expires_at.is_(None),
                        AgentPermissionAssignmentRow.expires_at > starts_at,
                    ),
                    AgentPermissionAssignmentRow.resource_type.is_(None)
                    if data.resource_type is None
                    else AgentPermissionAssignmentRow.resource_type == data.resource_type,
                    AgentPermissionAssignmentRow.resource_id.is_(None)
                    if data.resource_id is None
                    else AgentPermissionAssignmentRow.resource_id == data.resource_id,
                )
            )
            if duplicate:
                raise DomainError(
                    "DUPLICATE_ASSIGNMENT", "Equivalent assignment already exists.", 409
                )
            values = data.model_dump()
            values["starts_at"] = starts_at
            values["expires_at"] = expires_at
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
                if resource_type is not None and resource_type != permission.resource_type:
                    return AuthorizationDecision(
                        allowed=False,
                        matched_grants=[],
                        matched_denials=[],
                        decisive_rule="definition",
                        reason_code="resource_type_mismatch",
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
                direct_rows = s.scalars(
                    select(AgentPermissionAssignmentRow)
                    .where(active)
                    .order_by(AgentPermissionAssignmentRow.id)
                )
                for a in direct_rows:
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
                    .order_by(AgentRoleAssignmentRow.role_id, AgentRoleAssignmentRow.id)
                ).all()
                for rp, assignment in role_rows:
                    if not self._role_scope_matches(assignment, resource_type, resource_id):
                        continue
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
            self._validate_attribution(uow.session, data.assigned_by)
            starts_at, expires_at = effective_interval(data)
            infinity = datetime.max.replace(tzinfo=UTC)
            duplicate = uow.session.scalar(
                select(SupervisorRelationshipRow.id).where(
                    SupervisorRelationshipRow.supervisor_agent_id == data.supervisor_agent_id,
                    SupervisorRelationshipRow.subordinate_agent_id == data.subordinate_agent_id,
                    SupervisorRelationshipRow.relationship_type == data.relationship_type,
                    SupervisorRelationshipRow.revoked_at.is_(None),
                    SupervisorRelationshipRow.starts_at
                    < (expires_at if expires_at is not None else infinity),
                    or_(
                        SupervisorRelationshipRow.expires_at.is_(None),
                        SupervisorRelationshipRow.expires_at > starts_at,
                    ),
                )
            )
            if duplicate:
                raise DomainError(
                    "DUPLICATE_RELATIONSHIP",
                    "An equivalent relationship overlaps the requested interval.",
                    409,
                )
            if self._reachable_during(
                uow.session,
                data.subordinate_agent_id,
                data.supervisor_agent_id,
                starts_at,
                expires_at,
            ):
                raise DomainError(
                    "HIERARCHY_CYCLE", "Relationship would create a hierarchy cycle.", 409
                )
            if data.relationship_type == "primary":
                existing = uow.session.scalar(
                    select(SupervisorRelationshipRow).where(
                        SupervisorRelationshipRow.subordinate_agent_id == data.subordinate_agent_id,
                        SupervisorRelationshipRow.relationship_type == "primary",
                        SupervisorRelationshipRow.revoked_at.is_(None),
                        SupervisorRelationshipRow.starts_at
                        < (
                            expires_at
                            if expires_at is not None
                            else datetime.max.replace(tzinfo=UTC)
                        ),
                        or_(
                            SupervisorRelationshipRow.expires_at.is_(None),
                            SupervisorRelationshipRow.expires_at > starts_at,
                        ),
                    )
                )
                if existing:
                    raise DomainError(
                        "PRIMARY_SUPERVISOR_EXISTS", "Agent already has a primary supervisor.", 409
                    )
            values = data.model_dump()
            values["starts_at"] = starts_at
            values["expires_at"] = expires_at
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
            try:
                uow.session.flush()
            except IntegrityError as exc:
                raise DomainError(
                    "DUPLICATE_RELATIONSHIP", "Equivalent relationship already exists.", 409
                ) from exc
            return row

    def _reachable_during(
        self,
        s: Session,
        start: str,
        target: str,
        interval_start: datetime,
        interval_end: datetime | None,
    ) -> bool:
        """Return whether a path exists at any instant in the proposed interval.

        Each traversal state carries the intersection of all edge intervals in its path,
        preventing non-concurrent scheduled edges from producing a false cycle.
        """
        frontier = [(start, interval_start, interval_end)]
        seen: set[tuple[str, datetime, datetime | None]] = set()
        infinity = datetime.max.replace(tzinfo=UTC)
        for _ in range(MAX_HIERARCHY_DEPTH):
            if not frontier:
                return False
            next_frontier = []
            for node, path_start, path_end in frontier:
                if node == target:
                    return True
                state = (node, path_start, path_end)
                if state in seen:
                    continue
                seen.add(state)
                edges = s.scalars(
                    select(SupervisorRelationshipRow).where(
                        SupervisorRelationshipRow.supervisor_agent_id == node,
                        SupervisorRelationshipRow.revoked_at.is_(None),
                        SupervisorRelationshipRow.starts_at
                        < (path_end if path_end is not None else infinity),
                        or_(
                            SupervisorRelationshipRow.expires_at.is_(None),
                            SupervisorRelationshipRow.expires_at > path_start,
                        ),
                    )
                )
                for edge in edges:
                    overlap_start = max(utc(path_start), utc(edge.starts_at))
                    ends = [utc(x) for x in (path_end, edge.expires_at) if x is not None]
                    overlap_end = min(ends) if ends else None
                    if overlap_end is None or overlap_start < overlap_end:
                        next_frontier.append(
                            (edge.subordinate_agent_id, overlap_start, overlap_end)
                        )
            frontier = next_frontier
        raise DomainError(
            "HIERARCHY_DEPTH_EXCEEDED", "Hierarchy traversal exceeded its safe depth.", 409
        )

    def descendants(self, agent_id: str) -> list[str]:
        with self.sessions() as s:
            self._agent(s, agent_id)
            result = []
            frontier = [agent_id]
            seen = {agent_id}
            t = now()
            for _ in range(MAX_HIERARCHY_DEPTH):
                children = list(
                    s.scalars(
                        select(SupervisorRelationshipRow.subordinate_agent_id)
                        .where(
                            SupervisorRelationshipRow.supervisor_agent_id.in_(frontier),
                            SupervisorRelationshipRow.revoked_at.is_(None),
                            SupervisorRelationshipRow.starts_at <= t,
                            or_(
                                SupervisorRelationshipRow.expires_at.is_(None),
                                SupervisorRelationshipRow.expires_at > t,
                            ),
                        )
                        .order_by(SupervisorRelationshipRow.subordinate_agent_id)
                    )
                )
                frontier = []
                for child in children:
                    if child not in seen:
                        seen.add(child)
                        frontier.append(child)
                result.extend(frontier)
                if not frontier:
                    return result
            raise DomainError(
                "HIERARCHY_DEPTH_EXCEEDED", "Hierarchy traversal exceeded its safe depth.", 409
            )

    def create_resource_policy(self, data):
        with UnitOfWork(self.sessions) as uow:
            assert uow.session
            self._validate_attribution(uow.session, data.created_by)
            subject_models = {
                "agent": (IdentityAgentRow, "AGENT_NOT_FOUND", "Agent identity was not found."),
                "role": (IdentityRoleRow, "ROLE_NOT_FOUND", "Role was not found."),
                "rank": (IdentityRankRow, "RANK_NOT_FOUND", "Rank was not found."),
                "team": (IdentityTeamRow, "TEAM_NOT_FOUND", "Team was not found."),
            }
            if data.subject_type != "all":
                model, code, message = subject_models[data.subject_type]
                if not uow.session.get(model, data.subject_id):
                    raise DomainError(code, message, 404)
            starts_at, expires_at = effective_interval(data)
            values = data.model_dump()
            values["starts_at"] = starts_at
            values["expires_at"] = expires_at
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
        try:
            return self._check_resource_access(actor_id, resource_type, resource_id, action)
        except Exception:
            return AuthorizationDecision(
                allowed=False,
                permission_key=f"{resource_type}.{action}",
                actor_agent_id=actor_id,
                resource_type=resource_type,
                resource_id=resource_id,
                matched_grants=[],
                matched_denials=[],
                decisive_rule="fail_closed",
                reason_code="evaluation_failed",
            )

    def _check_resource_access(
        self, actor_id: str, resource_type: str, resource_id: str, action: str
    ) -> AuthorizationDecision:
        with self.sessions() as s:
            actor = s.get(IdentityAgentRow, actor_id)
            permission = s.scalar(
                select(IdentityPermissionRow).where(
                    IdentityPermissionRow.resource_type == resource_type,
                    IdentityPermissionRow.action == action,
                    IdentityPermissionRow.is_enabled,
                )
            )
            permission_key = permission.stable_key if permission else f"{resource_type}.{action}"
            base = self.check_permission(actor_id, permission_key, resource_type, resource_id)
            if base.reason_code in {"actor_inactive", "evaluation_failed"}:
                return base
            if not actor or actor.lifecycle_state != "active" or not actor.is_enabled:
                return base
            t = now()
            subject_pairs: set[tuple[str, str | None]] = {("all", None), ("agent", actor_id)}
            if actor.rank_id:
                rank = s.get(IdentityRankRow, actor.rank_id)
                if rank and rank.is_enabled:
                    subject_pairs.add(("rank", actor.rank_id))
            active_roles = s.scalars(
                select(AgentRoleAssignmentRow)
                .join(IdentityRoleRow)
                .where(
                    AgentRoleAssignmentRow.agent_id == actor_id,
                    IdentityRoleRow.is_enabled,
                    AgentRoleAssignmentRow.revoked_at.is_(None),
                    AgentRoleAssignmentRow.starts_at <= t,
                    or_(
                        AgentRoleAssignmentRow.expires_at.is_(None),
                        AgentRoleAssignmentRow.expires_at > t,
                    ),
                )
            )
            subject_pairs.update(
                ("role", assignment.role_id)
                for assignment in active_roles
                if self._role_scope_matches(assignment, resource_type, resource_id)
            )
            active_teams = s.scalars(
                select(TeamMembershipRow.team_id)
                .join(IdentityTeamRow)
                .where(
                    TeamMembershipRow.agent_id == actor_id,
                    IdentityTeamRow.lifecycle_state == "active",
                    TeamMembershipRow.revoked_at.is_(None),
                    TeamMembershipRow.starts_at <= t,
                    or_(
                        TeamMembershipRow.expires_at.is_(None),
                        TeamMembershipRow.expires_at > t,
                    ),
                )
            )
            subject_pairs.update(("team", team_id) for team_id in active_teams)
            subject_predicate = or_(
                *[
                    and_(
                        ResourceAccessPolicyRow.subject_type == subject_type,
                        ResourceAccessPolicyRow.subject_id.is_(None)
                        if subject_id is None
                        else ResourceAccessPolicyRow.subject_id == subject_id,
                    )
                    for subject_type, subject_id in sorted(subject_pairs)
                ]
            )
            policies = list(
                s.scalars(
                    select(ResourceAccessPolicyRow)
                    .where(
                        ResourceAccessPolicyRow.resource_type == resource_type,
                        ResourceAccessPolicyRow.resource_id == resource_id,
                        ResourceAccessPolicyRow.action == action,
                        ResourceAccessPolicyRow.revoked_at.is_(None),
                        ResourceAccessPolicyRow.starts_at <= t,
                        or_(
                            ResourceAccessPolicyRow.expires_at.is_(None),
                            ResourceAccessPolicyRow.expires_at > t,
                        ),
                        subject_predicate,
                    )
                    .order_by(ResourceAccessPolicyRow.id)
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
                    matched_denials=base.matched_denials + denies,
                    decisive_rule="resource_deny",
                    reason_code="resource_denial",
                )
            if base.matched_denials:
                return base
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

    @staticmethod
    def _role_scope_matches(
        assignment: AgentRoleAssignmentRow,
        resource_type: str | None,
        resource_id: str | None,
    ) -> bool:
        return assignment.scope_type == "global" or (
            assignment.scope_id == resource_id
            and (assignment.scope_type == "resource" or assignment.scope_type == resource_type)
        )

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
