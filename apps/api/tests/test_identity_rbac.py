import asyncio
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.errors import DomainError
from app.db.models import (
    AgentPermissionAssignmentRow,
    AgentRoleAssignmentRow,
    IdentityAuditEventRow,
    ResourceAccessPolicyRow,
    SupervisorRelationshipRow,
    TeamMembershipRow,
)
from app.db.session import create_database_engine, create_session_factory
from app.identity.service import IdentityService
from app.main import create_app
from app.models.identity import (
    AssignCapabilityRequest,
    AssignPermissionRequest,
    AssignRoleRequest,
    AuthorizationDecision,
    CreateAgentRequest,
    CreatePermissionRequest,
    CreateRankRequest,
    CreateRoleRequest,
    CreateTeamRequest,
    ResourcePolicyRequest,
    SupervisorRequest,
    TeamMembershipRequest,
)


@pytest.fixture
def service(tmp_path: Path):
    url = f"sqlite:///{tmp_path / 'identity.db'}"
    config = Config("alembic.ini")
    config.set_main_option("script_location", "migrations")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    engine = create_database_engine(url)
    yield IdentityService(create_session_factory(engine))
    engine.dispose()


def agent(service, key):
    return service.create_agent(
        CreateAgentRequest(stable_key=key, display_name=key, agent_type="worker")
    )


def test_lifecycle_is_audited_and_retirement_terminal(service):
    row = agent(service, "agent.alpha")
    identity = row.id
    service.transition(identity, "active")
    service.transition(identity, "suspended")
    service.transition(identity, "retired")
    with pytest.raises(DomainError, match="Cannot transition"):
        service.transition(identity, "active")
    assert service.get_agent(identity).stable_key == "agent.alpha"
    assert {x.event_type for x in service.audits(0, 100)} >= {
        "agent.created",
        "agent.active",
        "agent.suspended",
        "agent.retired",
    }


def test_agent_patch_rejects_explicit_nulls(service):
    app = create_app(database_url=str(service.sessions.kw["bind"].url))
    update_schema = app.openapi()["components"]["schemas"]["UpdateAgentRequest"]
    for field in ("display_name", "description", "operational_status", "is_enabled"):
        property_schema = update_schema["properties"][field]
        assert property_schema.get("type") != "null"
        assert not any(branch.get("type") == "null" for branch in property_schema.get("anyOf", []))
    with TestClient(app) as client:
        response = client.patch("/api/identity/agents/missing", json={"display_name": None})
    assert response.status_code == 422


@pytest.mark.parametrize(
    "payload",
    [
        {"is_enabled": True},
        {"operational_status": "available"},
        {"is_enabled": True, "operational_status": "available"},
    ],
)
def test_retired_agent_patch_preserves_terminal_state_atomically(service, payload):
    row = agent(service, "agent.retired-patch")
    service.transition(row.id, "active")
    retired = service.transition(row.id, "retired")
    version = retired.version
    audit_count = len(service.audits(0, 100))
    app = create_app(database_url=str(service.sessions.kw["bind"].url))

    with TestClient(app) as client:
        response = client.patch(f"/api/identity/agents/{row.id}", json=payload)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "RETIRED_AGENT_STATE_CONFLICT"
    persisted = service.get_agent(row.id)
    assert persisted.lifecycle_state == "retired"
    assert persisted.is_enabled is False
    assert persisted.operational_status == "offline"
    assert persisted.version == version
    assert len(service.audits(0, 100)) == audit_count


def test_retired_agent_patch_allows_metadata_without_relaxing_terminal_state(service):
    row = agent(service, "agent.retired-metadata")
    service.transition(row.id, "active")
    service.transition(row.id, "retired")
    app = create_app(database_url=str(service.sessions.kw["bind"].url))

    with TestClient(app) as client:
        response = client.patch(
            f"/api/identity/agents/{row.id}",
            json={"display_name": "Retired agent", "description": "Archived"},
        )

    assert response.status_code == 200
    assert response.json()["data"]["display_name"] == "Retired agent"
    persisted = service.get_agent(row.id)
    assert persisted.lifecycle_state == "retired"
    assert persisted.is_enabled is False
    assert persisted.operational_status == "offline"


def test_duplicate_and_invalid_stable_key(service):
    agent(service, "agent.unique")
    with pytest.raises(DomainError) as duplicate:
        agent(service, "agent.unique")
    assert duplicate.value.code == "DUPLICATE_STABLE_KEY"
    with pytest.raises(ValueError):
        CreateAgentRequest(stable_key="Not Valid", display_name="x", agent_type="worker")


def test_explicit_deny_overrides_role_grant(service):
    actor = agent(service, "agent.authorized")
    service.transition(actor.id, "active")
    permission = service.create_definition(
        "permission",
        CreatePermissionRequest(
            stable_key="room.enter", display_name="Enter", resource_type="room", action="enter"
        ),
    )
    role = service.create_definition(
        "role", CreateRoleRequest(stable_key="room.user", display_name="User", role_scope="global")
    )
    service.attach_permission(role.id, permission.id, "allow")
    service.assign_role(actor.id, AssignRoleRequest(role_id=role.id))
    assert (
        service.check_permission(actor.id, "room.enter", "room", "one").reason_code == "role_grant"
    )
    service.assign_permission(
        actor.id,
        AssignPermissionRequest(
            permission_id=permission.id, effect="deny", resource_type="room", resource_id="one"
        ),
    )
    decision = service.check_permission(actor.id, "room.enter", "room", "one")
    assert not decision.allowed and decision.reason_code == "explicit_denial"


def test_role_assignment_audits_identify_role_and_assignment(service):
    actor = agent(service, "agent.role-audit")
    roles = [
        service.create_definition(
            "role",
            CreateRoleRequest(
                stable_key=f"role.audit-{index}",
                display_name=f"Audit role {index}",
                role_scope="global",
            ),
        )
        for index in range(2)
    ]
    assignments = [
        service.assign_role(actor.id, AssignRoleRequest(role_id=role.id)) for role in roles
    ]

    events = service.audits(0, 100, "role.assigned")
    changes_by_role = {event.changes["role_id"]: event.changes for event in events}
    assert changes_by_role == {
        role.id: {
            "assignment_id": assignment.id,
            "role_id": role.id,
            "scope_type": "global",
            "scope_id": None,
        }
        for role, assignment in zip(roles, assignments, strict=True)
    }


def test_unknown_and_inactive_fail_closed(service):
    actor = agent(service, "agent.inactive")
    assert service.check_permission(actor.id, "unknown.action").reason_code == "actor_inactive"
    service.transition(actor.id, "active")
    assert service.check_permission(actor.id, "unknown.action").reason_code == "permission_unknown"


def test_hierarchy_rejects_self_and_multihop_cycle(service):
    a, b, c = [agent(service, f"agent.{key}") for key in "abc"]

    def relationship(supervisor, subordinate, kind="primary"):
        return SupervisorRequest(
            supervisor_agent_id=supervisor.id,
            subordinate_agent_id=subordinate.id,
            relationship_type=kind,
        )

    with pytest.raises(DomainError) as self_error:
        service.add_supervisor(relationship(a, a))
    assert self_error.value.code == "SELF_SUPERVISION"
    service.add_supervisor(relationship(a, b))
    service.add_supervisor(relationship(b, c))
    with pytest.raises(DomainError) as cycle:
        service.add_supervisor(relationship(c, a, "secondary"))
    assert cycle.value.code == "HIERARCHY_CYCLE"
    assert service.descendants(a.id) == [b.id, c.id]


def test_blocked_resource_policy_overrides_grant(service):
    actor = agent(service, "agent.office")
    service.transition(actor.id, "active")
    permission = service.create_definition(
        "permission",
        CreatePermissionRequest(
            stable_key="door.enter", display_name="Enter", resource_type="door", action="enter"
        ),
    )
    service.assign_permission(
        actor.id, AssignPermissionRequest(permission_id=permission.id, effect="allow")
    )
    service.create_resource_policy(
        ResourcePolicyRequest(
            subject_type="all",
            resource_type="door",
            resource_id="red-door",
            action="enter",
            effect="deny",
            access_state="blocked",
        )
    )
    decision = service.check_resource_access(actor.id, "door", "red-door", "enter")
    assert not decision.allowed and decision.reason_code == "resource_denial"


def test_contract_scope_and_expiration_validation():
    with pytest.raises(ValueError):
        AssignRoleRequest(role_id="role", scope_type="team")


def test_resource_policy_allow_cannot_override_explicit_denial(service):
    actor = agent(service, "agent.denied")
    service.transition(actor.id, "active")
    permission = service.create_definition(
        "permission",
        CreatePermissionRequest(
            stable_key="office.entry", display_name="Enter", resource_type="door", action="enter"
        ),
    )
    service.assign_permission(
        actor.id, AssignPermissionRequest(permission_id=permission.id, effect="deny")
    )
    service.create_resource_policy(
        ResourcePolicyRequest(
            subject_type="all",
            resource_type="door",
            resource_id="door-a",
            action="enter",
            effect="allow",
        )
    )

    decision = service.check_resource_access(actor.id, "door", "door-a", "enter")

    assert not decision.allowed
    assert decision.reason_code == "explicit_denial"
    assert decision.permission_key == "office.entry"
    assert decision.matched_denials


@pytest.mark.parametrize("effect", ["allow", "deny"])
def test_resource_policy_cannot_override_evaluation_failure(service, monkeypatch, effect):
    actor = agent(service, f"agent.evaluation.failure.{effect}")
    service.transition(actor.id, "active")
    service.create_resource_policy(
        ResourcePolicyRequest(
            subject_type="all",
            resource_type="door",
            resource_id="door-failed",
            action="enter",
            effect=effect,
        )
    )
    failed = AuthorizationDecision(
        allowed=False,
        permission_key="door.enter",
        actor_agent_id=actor.id,
        resource_type="door",
        resource_id="door-failed",
        matched_grants=[],
        matched_denials=[],
        decisive_rule="fail_closed",
        reason_code="evaluation_failed",
    )
    monkeypatch.setattr(service, "check_permission", lambda *args, **kwargs: failed)

    decision = service.check_resource_access(actor.id, "door", "door-failed", "enter")

    assert decision == failed
    assert not decision.allowed
    assert decision.decisive_rule == "fail_closed"
    assert decision.reason_code == "evaluation_failed"
    assert decision.matched_grants == []


def test_resource_policy_cannot_override_concurrent_inactive_decision(service, monkeypatch):
    actor = agent(service, "agent.concurrent.inactive")
    service.transition(actor.id, "active")
    service.create_resource_policy(
        ResourcePolicyRequest(
            subject_type="all",
            resource_type="door",
            resource_id="door-concurrent",
            action="enter",
            effect="allow",
        )
    )
    inactive = AuthorizationDecision(
        allowed=False,
        permission_key="door.enter",
        actor_agent_id=actor.id,
        resource_type="door",
        resource_id="door-concurrent",
        matched_grants=[],
        matched_denials=[],
        decisive_rule="actor_state",
        reason_code="actor_inactive",
    )
    monkeypatch.setattr(service, "check_permission", lambda *args, **kwargs: inactive)

    decision = service.check_resource_access(actor.id, "door", "door-concurrent", "enter")

    assert decision == inactive
    assert not decision.allowed
    assert decision.decisive_rule == "actor_state"
    assert decision.reason_code == "actor_inactive"
    assert decision.matched_grants == []


def test_resource_access_evaluator_fails_closed_when_outer_session_fails(service, monkeypatch):
    def unavailable():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(service, "sessions", unavailable)

    decision = service.check_resource_access("agent-unavailable", "artifact", "artifact-a", "use")

    assert not decision.allowed
    assert decision.permission_key == "artifact.use"
    assert decision.actor_agent_id == "agent-unavailable"
    assert decision.matched_grants == []
    assert decision.matched_denials == []
    assert decision.decisive_rule == "fail_closed"
    assert decision.reason_code == "evaluation_failed"


@pytest.mark.parametrize("subject_type", ["agent", "role", "rank", "team", "all"])
def test_resource_policies_match_every_active_subject(service, subject_type):
    rank = service.create_definition(
        "rank",
        CreateRankRequest(
            stable_key=f"rank.{subject_type}",
            display_name="Rank",
            priority_level=1,
            hierarchy_level=1,
        ),
    )
    actor = service.create_agent(
        CreateAgentRequest(
            stable_key=f"agent.{subject_type}",
            display_name="Actor",
            agent_type="worker",
            rank_id=rank.id,
        )
    )
    service.transition(actor.id, "active")
    role = service.create_definition(
        "role",
        CreateRoleRequest(
            stable_key=f"role.{subject_type}", display_name="Role", role_scope="global"
        ),
    )
    service.assign_role(actor.id, AssignRoleRequest(role_id=role.id))
    team = service.create_definition(
        "team",
        CreateTeamRequest(
            stable_key=f"team.{subject_type}", display_name="Team", team_type="delivery"
        ),
    )
    with service.sessions.begin() as session:
        session.add(
            TeamMembershipRow(id=f"membership-{subject_type}", team_id=team.id, agent_id=actor.id)
        )
    subject_ids = {
        "agent": actor.id,
        "role": role.id,
        "rank": rank.id,
        "team": team.id,
        "all": None,
    }
    service.create_resource_policy(
        ResourcePolicyRequest(
            subject_type=subject_type,
            subject_id=subject_ids[subject_type],
            resource_type="door",
            resource_id="door-a",
            action="enter",
            effect="allow",
        )
    )

    decision = service.check_resource_access(actor.id, "door", "door-a", "enter")

    assert decision.allowed
    assert decision.reason_code == "direct_grant"
    assert decision.matched_grants[0].startswith("policy:")


def test_resource_policy_role_subject_respects_assignment_scope(service):
    actor = agent(service, "agent.policy.scope")
    service.transition(actor.id, "active")
    role = service.create_definition(
        "role",
        CreateRoleRequest(
            stable_key="role.policy.scope", display_name="Scoped", role_scope="resource"
        ),
    )
    service.assign_role(
        actor.id,
        AssignRoleRequest(role_id=role.id, scope_type="resource", scope_id="door-a"),
    )
    for resource_id in ("door-a", "door-b"):
        service.create_resource_policy(
            ResourcePolicyRequest(
                subject_type="role",
                subject_id=role.id,
                resource_type="door",
                resource_id=resource_id,
                action="enter",
                effect="allow",
            )
        )

    assert service.check_resource_access(actor.id, "door", "door-a", "enter").allowed
    outside_scope = service.check_resource_access(actor.id, "door", "door-b", "enter")
    assert not outside_scope.allowed
    assert outside_scope.reason_code == "permission_unknown"


@pytest.mark.parametrize(
    ("scope_type", "scope_id", "matching_type", "matching_id"),
    [
        ("global", None, "resource", "anything"),
        ("resource", "door-a", "asset", "door-a"),
        ("team", "team-a", "team", "team-a"),
        ("project", "project-a", "project", "project-a"),
    ],
)
def test_role_permissions_respect_assignment_scope(
    service, scope_type, scope_id, matching_type, matching_id
):
    actor = agent(service, f"agent.scope.{scope_type}")
    service.transition(actor.id, "active")
    permission = service.create_definition(
        "permission",
        CreatePermissionRequest(
            stable_key=f"scope.{scope_type}",
            display_name="Use",
            resource_type=matching_type,
            action="use",
        ),
    )
    role = service.create_definition(
        "role",
        CreateRoleRequest(
            stable_key=f"role.scope.{scope_type}", display_name="Role", role_scope=scope_type
        ),
    )
    service.attach_permission(role.id, permission.id, "allow")
    service.assign_role(
        actor.id, AssignRoleRequest(role_id=role.id, scope_type=scope_type, scope_id=scope_id)
    )

    assert service.check_permission(
        actor.id, permission.stable_key, matching_type, matching_id
    ).allowed
    if scope_type != "global":
        assert not service.check_permission(
            actor.id, permission.stable_key, matching_type, "different"
        ).allowed


def test_inactive_role_assignments_and_hierarchy_links_are_ignored(service):
    actor = agent(service, "agent.timed")
    child = agent(service, "agent.child")
    service.transition(actor.id, "active")
    permission = service.create_definition(
        "permission",
        CreatePermissionRequest(
            stable_key="timed.use", display_name="Use", resource_type="asset", action="use"
        ),
    )
    role = service.create_definition(
        "role", CreateRoleRequest(stable_key="role.timed", display_name="Role", role_scope="global")
    )
    service.attach_permission(role.id, permission.id, "allow")
    future = datetime.now(UTC) + timedelta(days=1)
    service.assign_role(actor.id, AssignRoleRequest(role_id=role.id, starts_at=future))
    service.add_supervisor(
        SupervisorRequest(
            supervisor_agent_id=actor.id,
            subordinate_agent_id=child.id,
            relationship_type="temporary",
            starts_at=future,
        )
    )
    assert not service.check_permission(actor.id, permission.stable_key).allowed
    assert service.descendants(actor.id) == []

    with service.sessions.begin() as session:
        role_assignment = session.scalar(select(AgentRoleAssignmentRow))
        relationship = session.scalar(select(SupervisorRelationshipRow))
        role_assignment.starts_at = relationship.starts_at = datetime.now(UTC) - timedelta(days=2)
        role_assignment.expires_at = relationship.expires_at = datetime.now(UTC) - timedelta(days=1)
    assert not service.check_permission(actor.id, permission.stable_key).allowed
    assert service.descendants(actor.id) == []


def test_descendants_are_deduplicated_when_hierarchy_converges(service):
    root, left, right, leaf = [
        agent(service, f"agent.descendants.converge.{key}") for key in "abcd"
    ]
    service.add_supervisor(relationship(root, left))
    service.add_supervisor(relationship(root, right))
    service.add_supervisor(relationship(left, leaf))
    service.add_supervisor(relationship(right, leaf))
    descendants = service.descendants(root.id)
    assert set(descendants) == {left.id, right.id, leaf.id}
    assert descendants.count(leaf.id) == 1


def test_authorization_evidence_and_openapi_bodies_are_deterministic(service):
    actor = agent(service, "agent.evidence")
    service.transition(actor.id, "active")
    permission = service.create_definition(
        "permission",
        CreatePermissionRequest(
            stable_key="evidence.use", display_name="Use", resource_type="asset", action="use"
        ),
    )
    for resource_id in ("b", "a"):
        service.assign_permission(
            actor.id,
            AssignPermissionRequest(
                permission_id=permission.id,
                effect="allow",
                resource_type="asset",
                resource_id=resource_id,
            ),
        )
    first = service.check_permission(actor.id, permission.stable_key, "asset", "a")
    second = service.check_permission(actor.id, permission.stable_key, "asset", "a")
    assert first.matched_grants == second.matched_grants == sorted(first.matched_grants)

    schema = create_app(database_url=str(service.sessions.kw["bind"].url)).openapi()
    schemas = {
        "ranks": "CreateRankRequest",
        "roles": "CreateRoleRequest",
        "permissions": "CreatePermissionRequest",
        "capabilities": "CreateCapabilityRequest",
        "teams": "CreateTeamRequest",
    }
    for path, model in schemas.items():
        body_schema = schema["paths"][f"/api/identity/{path}"]["post"]["requestBody"]["content"][
            "application/json"
        ]["schema"]
        assert body_schema["$ref"].endswith(f"/{model}")


@pytest.mark.parametrize("role_scope", ["project", "team", "resource"])
def test_scoped_roles_cannot_be_assigned_globally(service, role_scope):
    actor = agent(service, f"agent.role.{role_scope}")
    role = service.create_definition(
        "role",
        CreateRoleRequest(
            stable_key=f"role.{role_scope}", display_name="Scoped", role_scope=role_scope
        ),
    )
    with pytest.raises(DomainError) as error:
        service.assign_role(actor.id, AssignRoleRequest(role_id=role.id))
    assert error.value.code == "ROLE_SCOPE_MISMATCH"
    assignment = service.assign_role(
        actor.id,
        AssignRoleRequest(role_id=role.id, scope_type=role_scope, scope_id=f"{role_scope}-1"),
    )
    assert assignment.scope_type == role_scope


def test_global_role_requires_global_assignment(service):
    actor = agent(service, "agent.role.global")
    role = service.create_definition(
        "role",
        CreateRoleRequest(stable_key="role.global", display_name="Global", role_scope="global"),
    )
    assert service.assign_role(actor.id, AssignRoleRequest(role_id=role.id)).scope_id is None
    with pytest.raises(ValueError):
        AssignRoleRequest(role_id=role.id, scope_type="team")
    with pytest.raises(ValueError):
        AssignRoleRequest(role_id=role.id, scope_type="global", scope_id="unexpected")


def test_duplicate_global_role_assignment_is_structured_and_atomic(service):
    actor = agent(service, "agent.role.global.duplicate")
    role = service.create_definition(
        "role",
        CreateRoleRequest(
            stable_key="role.global.duplicate", display_name="Global", role_scope="global"
        ),
    )
    request = AssignRoleRequest(role_id=role.id)
    service.assign_role(actor.id, request)
    before = len(service.audits(0, 100))
    with pytest.raises(DomainError) as error:
        service.assign_role(actor.id, request)
    assert error.value.code == "DUPLICATE_ASSIGNMENT"
    with service.sessions() as session:
        assert len(list(session.scalars(select(AgentRoleAssignmentRow)))) == 1
        assert len(list(session.scalars(select(IdentityAuditEventRow)))) == before


@pytest.mark.parametrize(("scope_type", "scope_id"), [("global", None), ("resource", "artifact-a")])
def test_active_role_assignment_blocks_overlap_atomically(service, scope_type, scope_id):
    actor = agent(service, f"agent.role.active.{scope_type}")
    role = service.create_definition(
        "role",
        CreateRoleRequest(
            stable_key=f"role.active.{scope_type}",
            display_name="Active",
            role_scope=scope_type,
        ),
    )
    request = AssignRoleRequest(role_id=role.id, scope_type=scope_type, scope_id=scope_id)
    service.assign_role(actor.id, request)
    before = len(service.audits(0, 100))
    with pytest.raises(DomainError) as error:
        service.assign_role(actor.id, request)
    assert error.value.code == "DUPLICATE_ASSIGNMENT"
    with service.sessions() as session:
        assert len(list(session.scalars(select(AgentRoleAssignmentRow)))) == 1
        assert len(list(session.scalars(select(IdentityAuditEventRow)))) == before


@pytest.mark.parametrize(("scope_type", "scope_id"), [("global", None), ("resource", "artifact-a")])
def test_expired_and_revoked_role_assignments_can_be_renewed(service, scope_type, scope_id):
    actor = agent(service, f"agent.role.renew.{scope_type}")
    role = service.create_definition(
        "role",
        CreateRoleRequest(
            stable_key=f"role.renew.{scope_type}",
            display_name="Renewable",
            role_scope=scope_type,
        ),
    )
    base = {"role_id": role.id, "scope_type": scope_type, "scope_id": scope_id}
    past = datetime.now(UTC) - timedelta(days=4)
    expired = service.assign_role(
        actor.id,
        AssignRoleRequest(**base, starts_at=past, expires_at=past + timedelta(days=1)),
    )
    active = service.assign_role(actor.id, AssignRoleRequest(**base))
    with service.sessions.begin() as session:
        session.get(AgentRoleAssignmentRow, active.id).revoked_at = datetime.now(UTC)
    renewed = service.assign_role(actor.id, AssignRoleRequest(**base))

    assert len({expired.id, active.id, renewed.id}) == 3
    with service.sessions() as session:
        rows = list(session.scalars(select(AgentRoleAssignmentRow)))
        assert len(rows) == 3
        assert session.get(AgentRoleAssignmentRow, expired.id)
        assert session.get(AgentRoleAssignmentRow, active.id)


@pytest.mark.parametrize(("scope_type", "scope_id"), [("global", None), ("resource", "artifact-a")])
def test_overlapping_role_assignments_are_structured_and_atomic(service, scope_type, scope_id):
    actor = agent(service, f"agent.role.overlap.{scope_type}")
    role = service.create_definition(
        "role",
        CreateRoleRequest(
            stable_key=f"role.overlap.{scope_type}",
            display_name="Overlapping",
            role_scope=scope_type,
        ),
    )
    base = {"role_id": role.id, "scope_type": scope_type, "scope_id": scope_id}
    future = datetime.now(UTC) + timedelta(days=2)
    service.assign_role(
        actor.id,
        AssignRoleRequest(**base, starts_at=future, expires_at=future + timedelta(days=2)),
    )
    before = len(service.audits(0, 100))
    with pytest.raises(DomainError) as overlap:
        service.assign_role(
            actor.id,
            AssignRoleRequest(
                **base,
                starts_at=future + timedelta(days=1),
                expires_at=future + timedelta(days=3),
            ),
        )
    assert overlap.value.code == "DUPLICATE_ASSIGNMENT"
    assert service.assign_role(
        actor.id,
        AssignRoleRequest(
            **base,
            starts_at=future + timedelta(days=2),
            expires_at=future + timedelta(days=3),
        ),
    )
    service.assign_role(actor.id, AssignRoleRequest(**base, starts_at=future + timedelta(days=4)))
    with pytest.raises(DomainError) as open_ended:
        service.assign_role(
            actor.id,
            AssignRoleRequest(
                **base,
                starts_at=future + timedelta(days=5),
                expires_at=future + timedelta(days=6),
            ),
        )
    assert open_ended.value.code == "DUPLICATE_ASSIGNMENT"
    with service.sessions() as session:
        assert len(list(session.scalars(select(AgentRoleAssignmentRow)))) == 3
        assert len(list(session.scalars(select(IdentityAuditEventRow)))) == before + 2


def test_role_assignments_keep_scopes_and_roles_independent(service):
    actor = agent(service, "agent.role.independent")
    roles = [
        service.create_definition(
            "role",
            CreateRoleRequest(
                stable_key=f"role.independent.{key}",
                display_name=key,
                role_scope="resource",
            ),
        )
        for key in ("one", "two")
    ]
    service.assign_role(
        actor.id,
        AssignRoleRequest(role_id=roles[0].id, scope_type="resource", scope_id="artifact-a"),
    )
    service.assign_role(
        actor.id,
        AssignRoleRequest(role_id=roles[0].id, scope_type="resource", scope_id="artifact-b"),
    )
    service.assign_role(
        actor.id,
        AssignRoleRequest(role_id=roles[1].id, scope_type="resource", scope_id="artifact-a"),
    )
    with service.sessions() as session:
        assert len(list(session.scalars(select(AgentRoleAssignmentRow)))) == 3


def test_attribution_agents_are_validated_before_persistence(service):
    target = agent(service, "agent.attribution.target")
    service.transition(target.id, "active")
    supervisor = agent(service, "agent.attribution.supervisor")
    attribution = agent(service, "agent.attribution.valid")
    role = service.create_definition(
        "role",
        CreateRoleRequest(
            stable_key="role.attribution", display_name="Attributed", role_scope="global"
        ),
    )
    permission = service.create_definition(
        "permission",
        CreatePermissionRequest(
            stable_key="artifact.attribution",
            display_name="Attributed",
            resource_type="artifact",
            action="use",
        ),
    )
    app = create_app(database_url=str(service.sessions.kw["bind"].url))
    requests = [
        (
            f"/api/identity/agents/{target.id}/roles",
            {"role_id": role.id, "assigned_by": "agent-missing"},
        ),
        (
            f"/api/identity/agents/{target.id}/permissions",
            {
                "permission_id": permission.id,
                "effect": "allow",
                "assigned_by": "agent-missing",
            },
        ),
        (
            "/api/identity/access-policies",
            {
                "subject_type": "all",
                "resource_type": "artifact",
                "resource_id": "artifact-a",
                "action": "use",
                "effect": "allow",
                "created_by": "agent-missing",
            },
        ),
        (
            "/api/identity/hierarchy",
            {
                "supervisor_agent_id": supervisor.id,
                "subordinate_agent_id": target.id,
                "relationship_type": "secondary",
                "assigned_by": "agent-missing",
            },
        ),
    ]
    before = len(service.audits(0, 100))
    with TestClient(app) as client:
        responses = [client.post(path, json=body) for path, body in requests]
    assert {response.status_code for response in responses} == {404}
    assert {response.json()["error"]["code"] for response in responses} == {"AGENT_NOT_FOUND"}
    with service.sessions() as session:
        assert not list(session.scalars(select(AgentRoleAssignmentRow)))
        assert not list(session.scalars(select(AgentPermissionAssignmentRow)))
        assert not list(session.scalars(select(ResourceAccessPolicyRow)))
        assert not list(session.scalars(select(SupervisorRelationshipRow)))
        assert len(list(session.scalars(select(IdentityAuditEventRow)))) == before

    assert service.assign_role(
        target.id, AssignRoleRequest(role_id=role.id, assigned_by=attribution.id)
    )
    assert service.assign_permission(
        target.id, AssignPermissionRequest(permission_id=permission.id, effect="allow")
    )
    assert service.create_resource_policy(
        ResourcePolicyRequest(
            subject_type="all",
            resource_type="artifact",
            resource_id="artifact-a",
            action="use",
            effect="allow",
            created_by=attribution.id,
        )
    )
    assert service.add_supervisor(relationship(supervisor, target, assigned_by=None))

    transition_target = agent(service, "agent.attribution.transition")
    before_transition = len(service.audits(0, 100))
    with pytest.raises(DomainError) as transition_error:
        service.transition(transition_target.id, "active", actor="agent-missing")
    assert transition_error.value.code == "AGENT_NOT_FOUND"
    assert service.get_agent(transition_target.id).lifecycle_state == "provisioned"
    assert len(service.audits(0, 100)) == before_transition


def test_timed_identity_mutations_normalize_offsets_to_utc(service):
    actor = agent(service, "agent.timezone.actor")
    service.transition(actor.id, "active")
    supervisor = agent(service, "agent.timezone.supervisor")
    role = service.create_definition(
        "role",
        CreateRoleRequest(stable_key="role.timezone", display_name="Timezone", role_scope="global"),
    )
    permission = service.create_definition(
        "permission",
        CreatePermissionRequest(
            stable_key="artifact.timezone",
            display_name="Timezone",
            resource_type="artifact",
            action="use",
        ),
    )
    offset = timezone(timedelta(hours=5))
    starts_at = (datetime.now(UTC) - timedelta(minutes=5)).astimezone(offset)
    expires_at = (datetime.now(UTC) + timedelta(hours=1)).astimezone(offset)
    expected_start = starts_at.astimezone(UTC)
    expected_end = expires_at.astimezone(UTC)
    capability_request = AssignCapabilityRequest(
        capability_id="capability-timezone",
        source="test",
        starts_at=starts_at,
        expires_at=expires_at,
    )
    membership_request = TeamMembershipRequest(
        agent_id=actor.id,
        starts_at=starts_at,
        expires_at=expires_at,
    )
    for request in (capability_request, membership_request):
        assert request.starts_at == expected_start
        assert request.expires_at == expected_end

    role_assignment = service.assign_role(
        actor.id,
        AssignRoleRequest(role_id=role.id, starts_at=starts_at, expires_at=expires_at),
    )
    permission_assignment = service.assign_permission(
        actor.id,
        AssignPermissionRequest(
            permission_id=permission.id,
            effect="allow",
            starts_at=starts_at,
            expires_at=expires_at,
        ),
    )
    supervisor_relationship = service.add_supervisor(
        relationship(
            supervisor,
            actor,
            starts_at=starts_at,
            expires_at=expires_at,
        )
    )
    policy = service.create_resource_policy(
        ResourcePolicyRequest(
            subject_type="all",
            resource_type="artifact",
            resource_id="artifact-timezone",
            action="use",
            effect="allow",
            starts_at=starts_at,
            expires_at=expires_at,
        )
    )

    def as_utc(value):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    for row in (
        role_assignment,
        permission_assignment,
        supervisor_relationship,
        policy,
    ):
        assert as_utc(row.starts_at) == expected_start
        assert as_utc(row.expires_at) == expected_end
    assert service.check_permission(actor.id, permission.stable_key).allowed


@pytest.mark.parametrize(
    ("subject_type", "error_code"),
    [
        ("agent", "AGENT_NOT_FOUND"),
        ("role", "ROLE_NOT_FOUND"),
        ("rank", "RANK_NOT_FOUND"),
        ("team", "TEAM_NOT_FOUND"),
    ],
)
def test_resource_policies_reject_missing_subjects_atomically(service, subject_type, error_code):
    app = create_app(database_url=str(service.sessions.kw["bind"].url))
    before = len(service.audits(0, 100))

    with TestClient(app) as client:
        response = client.post(
            "/api/identity/access-policies",
            json={
                "subject_type": subject_type,
                "subject_id": "missing-subject",
                "resource_type": "artifact",
                "resource_id": "artifact-protected",
                "action": "use",
                "effect": "deny",
            },
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == error_code
    with service.sessions() as session:
        assert not list(session.scalars(select(ResourceAccessPolicyRow)))
        assert len(list(session.scalars(select(IdentityAuditEventRow)))) == before


def relationship(supervisor, subordinate, kind="secondary", **times):
    return SupervisorRequest(
        supervisor_agent_id=supervisor.id,
        subordinate_agent_id=subordinate.id,
        relationship_type=kind,
        **times,
    )


def test_future_overlapping_and_open_ended_cycles_are_rejected(service):
    a, b, c = [agent(service, f"agent.future.{key}") for key in "abc"]
    future = datetime.now(UTC) + timedelta(days=2)
    end = future + timedelta(days=2)
    service.add_supervisor(relationship(a, b, starts_at=future, expires_at=end))
    service.add_supervisor(relationship(b, c, starts_at=future))
    with pytest.raises(DomainError) as error:
        service.add_supervisor(relationship(c, a, starts_at=future + timedelta(hours=1)))
    assert error.value.code == "HIERARCHY_CYCLE"


def test_nonoverlapping_hierarchy_intervals_do_not_form_cycle(service):
    a, b = [agent(service, f"agent.nonoverlap.{key}") for key in "ab"]
    future = datetime.now(UTC) + timedelta(days=2)
    service.add_supervisor(
        relationship(a, b, starts_at=future, expires_at=future + timedelta(days=1))
    )
    assert service.add_supervisor(
        relationship(
            b, a, starts_at=future + timedelta(days=1), expires_at=future + timedelta(days=2)
        )
    )


def test_expired_primary_allows_replacement_but_overlaps_conflict(service):
    first, second, third, child = [agent(service, f"agent.primary.{key}") for key in "abcd"]
    past = datetime.now(UTC) - timedelta(days=3)
    service.add_supervisor(
        relationship(first, child, "primary", starts_at=past, expires_at=past + timedelta(days=1))
    )
    replacement = service.add_supervisor(relationship(second, child, "primary"))
    assert replacement.supervisor_agent_id == second.id
    with pytest.raises(DomainError) as error:
        service.add_supervisor(relationship(third, child, "primary"))
    assert error.value.code == "PRIMARY_SUPERVISOR_EXISTS"


def test_revoked_and_nonoverlapping_future_primary_allow_replacement(service):
    first, second, child = [agent(service, f"agent.primary.replacement.{key}") for key in "abc"]
    future = datetime.now(UTC) + timedelta(days=3)
    original = service.add_supervisor(
        relationship(
            first,
            child,
            "primary",
            starts_at=future,
            expires_at=future + timedelta(days=1),
        )
    )
    service.add_supervisor(
        relationship(second, child, "primary", starts_at=future + timedelta(days=1))
    )
    with service.sessions.begin() as session:
        session.get(SupervisorRelationshipRow, original.id).revoked_at = datetime.now(UTC)
    third = agent(service, "agent.primary.replacement.third")
    assert service.add_supervisor(
        relationship(
            third,
            child,
            "primary",
            starts_at=future,
            expires_at=future + timedelta(hours=12),
        )
    )


@pytest.mark.parametrize("kind", ["primary", "secondary", "temporary", "functional"])
def test_duplicate_hierarchy_is_structured_and_atomic(service, kind):
    supervisor, subordinate = [agent(service, f"agent.duplicate.{kind}.{key}") for key in "ab"]
    request = relationship(supervisor, subordinate, kind)
    service.add_supervisor(request)
    before = len(service.audits(0, 100))
    with pytest.raises(DomainError) as error:
        service.add_supervisor(request)
    assert error.value.code == "DUPLICATE_RELATIONSHIP"
    with service.sessions() as session:
        assert len(list(session.scalars(select(SupervisorRelationshipRow)))) == 1
        assert len(list(session.scalars(select(IdentityAuditEventRow)))) == before


def test_expired_and_revoked_relationships_can_be_recreated(service):
    supervisor, subordinate = [agent(service, f"agent.relationship.recreate.{key}") for key in "ab"]
    past = datetime.now(UTC) - timedelta(days=3)
    expired = service.add_supervisor(
        relationship(
            supervisor,
            subordinate,
            "secondary",
            starts_at=past,
            expires_at=past + timedelta(days=1),
        )
    )
    active = service.add_supervisor(relationship(supervisor, subordinate, "secondary"))
    with service.sessions.begin() as session:
        session.get(SupervisorRelationshipRow, active.id).revoked_at = datetime.now(UTC)
    recreated = service.add_supervisor(relationship(supervisor, subordinate, "secondary"))
    assert recreated.id not in {expired.id, active.id}
    with service.sessions() as session:
        assert len(list(session.scalars(select(SupervisorRelationshipRow)))) == 3


@pytest.mark.parametrize("request_type", [CreateRankRequest, CreateRoleRequest, CreateTeamRequest])
def test_database_backed_definition_keys_reject_more_than_80_characters(request_type):
    values = {"stable_key": "a" * 81, "display_name": "Too long"}
    if request_type is CreateRankRequest:
        values.update(priority_level=1, hierarchy_level=1)
    elif request_type is CreateRoleRequest:
        values["role_scope"] = "global"
    else:
        values["team_type"] = "delivery"
    with pytest.raises(ValueError):
        request_type(**values)
    assert CreatePermissionRequest(
        stable_key="p" * 81,
        display_name="Allowed",
        resource_type="artifact",
        action="read",
    )


def test_permission_attachment_effect_is_validated_at_api_boundary(service):
    app = create_app(database_url=str(service.sessions.kw["bind"].url))
    schema = app.openapi()
    parameter = next(
        item
        for item in schema["paths"]["/api/identity/roles/{role_id}/permissions/{permission_id}"][
            "post"
        ]["parameters"]
        if item["name"] == "effect"
    )
    assert set(parameter["schema"]["enum"]) == {"allow", "deny"}
    with TestClient(app) as client:
        response = client.post(
            "/api/identity/roles/missing/permissions/missing", params={"effect": "invalid"}
        )
    assert response.status_code == 422


def test_global_and_scoped_permission_uniqueness(service):
    actor = agent(service, "agent.permission.unique")
    service.transition(actor.id, "active")
    permission = service.create_definition(
        "permission",
        CreatePermissionRequest(
            stable_key="artifact.read", display_name="Read", resource_type="artifact", action="read"
        ),
    )
    for effect in ("allow", "deny"):
        request = AssignPermissionRequest(permission_id=permission.id, effect=effect)
        service.assign_permission(actor.id, request)
        with pytest.raises(DomainError) as error:
            service.assign_permission(actor.id, request)
        assert error.value.code == "DUPLICATE_ASSIGNMENT"
    for resource_id in ("one", "two"):
        service.assign_permission(
            actor.id,
            AssignPermissionRequest(
                permission_id=permission.id,
                effect="allow",
                resource_type="artifact",
                resource_id=resource_id,
            ),
        )
    with pytest.raises(DomainError):
        service.assign_permission(
            actor.id,
            AssignPermissionRequest(
                permission_id=permission.id,
                effect="allow",
                resource_type="artifact",
                resource_id="one",
            ),
        )
    with service.sessions() as session:
        assert len(list(session.scalars(select(AgentPermissionAssignmentRow)))) == 4


def test_permission_assignment_rejects_mismatched_resource_type_atomically(service):
    actor = agent(service, "agent.permission.scope")
    service.transition(actor.id, "active")
    permission = service.create_definition(
        "permission",
        CreatePermissionRequest(
            stable_key="room.enter",
            display_name="Enter room",
            resource_type="room",
            action="enter",
        ),
    )
    app = create_app(database_url=str(service.sessions.kw["bind"].url))
    before = len(service.audits(0, 100))
    with TestClient(app) as client:
        mismatch = client.post(
            f"/api/identity/agents/{actor.id}/permissions",
            json={
                "permission_id": permission.id,
                "effect": "allow",
                "resource_type": "door",
                "resource_id": "door-a",
            },
        )
        valid = client.post(
            f"/api/identity/agents/{actor.id}/permissions",
            json={
                "permission_id": permission.id,
                "effect": "allow",
                "resource_type": "room",
                "resource_id": "room-a",
            },
        )

    assert mismatch.status_code == 409
    assert mismatch.json()["error"]["code"] == "PERMISSION_SCOPE_MISMATCH"
    assert valid.status_code == 201
    with service.sessions() as session:
        assignments = list(session.scalars(select(AgentPermissionAssignmentRow)))
        assert len(assignments) == 1
        assert assignments[0].resource_type == "room"
        assert len(list(session.scalars(select(IdentityAuditEventRow)))) == before + 1
    assert service.check_permission(actor.id, permission.stable_key, "room", "room-a").allowed


@pytest.mark.parametrize("grant_source", ["direct", "role"])
def test_permission_evaluation_rejects_mismatched_resource_type(service, grant_source):
    actor = agent(service, f"agent.permission.evaluate.{grant_source}")
    service.transition(actor.id, "active")
    permission = service.create_definition(
        "permission",
        CreatePermissionRequest(
            stable_key=f"room.evaluate.{grant_source}",
            display_name="Evaluate room",
            resource_type="room",
            action=f"evaluate-{grant_source}",
        ),
    )
    if grant_source == "direct":
        service.assign_permission(
            actor.id,
            AssignPermissionRequest(permission_id=permission.id, effect="allow"),
        )
    else:
        role = service.create_definition(
            "role",
            CreateRoleRequest(
                stable_key=f"role.permission.evaluate.{grant_source}",
                display_name="Evaluate room",
                role_scope="global",
            ),
        )
        service.attach_permission(role.id, permission.id, "allow")
        service.assign_role(actor.id, AssignRoleRequest(role_id=role.id))

    mismatch = service.check_permission(
        actor.id,
        permission.stable_key,
        resource_type="artifact",
        resource_id="artifact-a",
    )
    matching = service.check_permission(
        actor.id,
        permission.stable_key,
        resource_type="room",
        resource_id="room-a",
    )

    assert not mismatch.allowed
    assert mismatch.matched_grants == []
    assert mismatch.matched_denials == []
    assert mismatch.decisive_rule == "definition"
    assert mismatch.reason_code == "resource_type_mismatch"
    assert matching.allowed


def test_omitted_start_rejects_already_expired_effective_windows_atomically(service):
    target = agent(service, "agent.interval.target")
    service.transition(target.id, "active")
    supervisor = agent(service, "agent.interval.supervisor")
    role = service.create_definition(
        "role",
        CreateRoleRequest(stable_key="role.interval", display_name="Interval", role_scope="global"),
    )
    permission = service.create_definition(
        "permission",
        CreatePermissionRequest(
            stable_key="artifact.interval",
            display_name="Interval",
            resource_type="artifact",
            action="use",
        ),
    )
    expired = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    app = create_app(database_url=str(service.sessions.kw["bind"].url))
    requests = [
        (
            f"/api/identity/agents/{target.id}/roles",
            {"role_id": role.id, "expires_at": expired},
        ),
        (
            f"/api/identity/agents/{target.id}/permissions",
            {
                "permission_id": permission.id,
                "effect": "allow",
                "expires_at": expired,
            },
        ),
        (
            "/api/identity/hierarchy",
            {
                "supervisor_agent_id": supervisor.id,
                "subordinate_agent_id": target.id,
                "relationship_type": "secondary",
                "expires_at": expired,
            },
        ),
        (
            "/api/identity/access-policies",
            {
                "subject_type": "all",
                "resource_type": "artifact",
                "resource_id": "artifact-interval",
                "action": "use",
                "effect": "allow",
                "expires_at": expired,
            },
        ),
    ]
    before = len(service.audits(0, 100))
    with TestClient(app) as client:
        responses = [client.post(path, json=body) for path, body in requests]

    assert {response.status_code for response in responses} == {422}
    assert {response.json()["error"]["code"] for response in responses} == {
        "INVALID_EFFECTIVE_INTERVAL"
    }
    with service.sessions() as session:
        assert not list(session.scalars(select(AgentRoleAssignmentRow)))
        assert not list(session.scalars(select(AgentPermissionAssignmentRow)))
        assert not list(session.scalars(select(SupervisorRelationshipRow)))
        assert not list(session.scalars(select(ResourceAccessPolicyRow)))
        assert len(list(session.scalars(select(IdentityAuditEventRow)))) == before


def test_expired_and_revoked_permission_assignments_can_be_renewed(service):
    actor = agent(service, "agent.permission.renew")
    service.transition(actor.id, "active")
    permission = service.create_definition(
        "permission",
        CreatePermissionRequest(
            stable_key="artifact.renew",
            display_name="Renew",
            resource_type="artifact",
            action="use",
        ),
    )
    past = datetime.now(UTC) - timedelta(days=3)
    service.assign_permission(
        actor.id,
        AssignPermissionRequest(
            permission_id=permission.id,
            effect="allow",
            starts_at=past,
            expires_at=past + timedelta(days=1),
        ),
    )
    active = service.assign_permission(
        actor.id, AssignPermissionRequest(permission_id=permission.id, effect="allow")
    )
    with service.sessions.begin() as session:
        session.get(AgentPermissionAssignmentRow, active.id).revoked_at = datetime.now(UTC)
    renewed = service.assign_permission(
        actor.id, AssignPermissionRequest(permission_id=permission.id, effect="allow")
    )
    assert renewed.id != active.id
    with service.sessions() as session:
        assert len(list(session.scalars(select(AgentPermissionAssignmentRow)))) == 3


def test_identity_openapi_has_typed_success_envelopes(service):
    schema = create_app(database_url=str(service.sessions.kw["bind"].url)).openapi()
    operations = [
        operation
        for path, item in schema["paths"].items()
        if path.startswith("/api/identity")
        for operation in item.values()
        if isinstance(operation, dict) and "responses" in operation
    ]
    assert operations
    for operation in operations:
        success = operation["responses"].get("200") or operation["responses"].get("201")
        response_schema = success["content"]["application/json"]["schema"]
        assert response_schema.get("$ref")
        envelope = schema["components"]["schemas"][response_schema["$ref"].rsplit("/", 1)[-1]]
        assert set(envelope["properties"]) == {"data", "meta"}
    serialized = str(schema)
    assert "AgentIdentity" in serialized
    assert "AuthorizationDecision" in serialized


def test_patch_cors_preflight_and_existing_methods(service):
    app = create_app(database_url=str(service.sessions.kw["bind"].url))

    async def preflight(method):
        messages = []
        request_sent = False

        async def receive():
            nonlocal request_sent
            if not request_sent:
                request_sent = True
                return {"type": "http.request", "body": b"", "more_body": False}
            return {"type": "http.disconnect"}

        async def send(message):
            messages.append(message)

        headers = [
            (b"origin", b"http://localhost:5173"),
            (b"access-control-request-method", method.encode()),
            (b"access-control-request-headers", b"content-type"),
        ]
        await app(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "OPTIONS",
                "scheme": "http",
                "path": "/api/identity/agents/example",
                "raw_path": b"/api/identity/agents/example",
                "query_string": b"",
                "headers": headers,
                "client": ("test", 123),
                "server": ("test", 80),
                "root_path": "",
            },
            receive,
            send,
        )
        start = next(message for message in messages if message["type"] == "http.response.start")
        return start["status"], dict(start["headers"])

    for method in ("GET", "POST", "PATCH"):
        status, headers = asyncio.run(preflight(method))
        assert status == 200
        assert method.encode() in headers[b"access-control-allow-methods"]
