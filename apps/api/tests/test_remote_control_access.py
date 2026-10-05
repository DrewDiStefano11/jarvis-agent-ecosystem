"""Core authentication/authorization only; network integration is not claimed."""

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.agent_runtime.errors import RuntimeActorInactiveError
from app.core.errors import DomainError
from app.main import create_app
from app.models.identity import AssignPermissionRequest, CreatePermissionRequest
from app.remote_control.access import RemoteControlAccess
from tests.test_agent_runtime_authorization import create_actor
from tests.test_persistence import database_url

TOKEN = "fixtureRemoteCredential_" + "x" * 32


@pytest.fixture
def remote_access(tmp_path):
    app = create_app(delay_ms=1, database_url=database_url(tmp_path / "remote-access.db"))
    with TestClient(app):
        actor = create_actor(app, "remote-operator")
        yield app, actor, RemoteControlAccess(app.state.identity_service, actor, SecretStr(TOKEN))


@pytest.mark.parametrize(
    "header",
    [
        None,
        "Bearer wrong",
        "Basic " + TOKEN,
        "Bearer  " + TOKEN,
        "Bearer " + "x" * 129,
        "Bearer " + "z" * len(TOKEN),
    ],
)
def test_remote_credentials_fail_closed(remote_access, header):
    _, _, access = remote_access
    with pytest.raises(DomainError) as failure:
        access.authenticate(header, secure_transport=True)
    assert failure.value.code == "REMOTE_AUTHENTICATION_REQUIRED"
    assert TOKEN not in str(failure.value)


def test_remote_tls_and_actor_binding(remote_access):
    _, actor, access = remote_access
    header = "Bearer " + TOKEN
    with pytest.raises(DomainError, match="requires HTTPS"):
        access.authenticate(header, secure_transport=False)
    with pytest.raises(DomainError, match="must match"):
        access.authenticate(header, secure_transport=True, claimed_actor_id="other-operator")
    assert (
        access.authenticate(header, secure_transport=True, claimed_actor_id=actor).actor_id == actor
    )


def test_remote_authority_uses_live_native_rbac_and_denials(remote_access, monkeypatch):
    app, actor, access = remote_access
    identity = app.state.identity_service
    principal = access.authenticate("Bearer " + TOKEN, secure_transport=True)
    with pytest.raises(DomainError, match="denied"):
        access.authorize(principal, "control")
    permission = identity.create_definition(
        "permission",
        CreatePermissionRequest(
            stable_key="remote.control",
            display_name="Remote control",
            resource_type="administrative_function",
            action="remote_control",
        ),
    )
    scope = dict(
        permission_id=permission.id,
        resource_type="administrative_function",
        resource_id="remote_control",
    )
    identity.assign_permission(actor, AssignPermissionRequest(effect="allow", **scope))
    assert access.authorize(principal, "control").permission_key == "remote.control"
    # The mutation path must consume its caller's transaction rather than open
    # an independent authorization session with a different recovery position.
    with app.state.task_leases.session_factory() as session, session.begin():
        with monkeypatch.context() as patch:

            def forbidden_session():
                raise AssertionError("authorization opened a separate session")

            patch.setattr(identity, "sessions", forbidden_session)
            assert access.authorize_in_session(principal, "control", session).actor == principal
    with pytest.raises(DomainError, match="denied"):
        access.authorize(principal, "submit")
    identity.assign_permission(actor, AssignPermissionRequest(effect="deny", **scope))
    with pytest.raises(DomainError, match="denied"):
        access.authorize(principal, "control")
    with app.state.task_leases.session_factory() as session, session.begin():
        with pytest.raises(DomainError, match="denied"):
            access.authorize_in_session(principal, "control", session)


def test_remote_suspension_invalidates_previously_authenticated_principal(remote_access):
    app, actor, access = remote_access
    principal = access.authenticate("Bearer " + TOKEN, secure_transport=True)
    app.state.identity_service.transition(actor, "suspended")
    with pytest.raises(RuntimeActorInactiveError):
        access.authenticate("Bearer " + TOKEN, secure_transport=True)
    with pytest.raises(RuntimeActorInactiveError):
        access.authorize(principal, "read")
