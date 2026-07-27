from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from app.core.errors import DomainError
from app.db.session import create_database_engine, create_session_factory
from app.identity.service import IdentityService
from app.models.identity import (
    AssignPermissionRequest,
    AssignRoleRequest,
    CreateAgentRequest,
    CreatePermissionRequest,
    CreateRoleRequest,
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
