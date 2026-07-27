from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select

from app.core.errors import DomainError
from app.db.models import (
    AgentRoleAssignmentRow,
    SupervisorRelationshipRow,
    TeamMembershipRow,
)
from app.db.session import create_database_engine, create_session_factory
from app.identity.service import IdentityService
from app.main import create_app
from app.models.identity import (
    AssignPermissionRequest,
    AssignRoleRequest,
    CreateAgentRequest,
    CreatePermissionRequest,
    CreateRankRequest,
    CreateRoleRequest,
    CreateTeamRequest,
    ResourcePolicyRequest,
    SupervisorRequest,
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
            resource_type="asset",
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
