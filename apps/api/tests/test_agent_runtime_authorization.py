from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import (
    IdentityPermissionRow,
    OutboxEventRow,
    ResourceAccessPolicyRow,
    TeamMembershipRow,
)
from app.main import create_app
from app.models.identity import (
    AssignPermissionRequest,
    AssignRoleRequest,
    CreateAgentRequest,
    CreatePermissionRequest,
    CreateRankRequest,
    CreateRoleRequest,
    CreateTeamRequest,
)
from tests.agent_runtime_testkit import make_spec, ts
from tests.test_persistence import database_url

RUNTIME_KEYS = {
    "runtime.read": "read",
    "runtime.create": "create",
    "runtime.queue": "queue",
    "runtime.pause": "pause",
    "runtime.cancel": "cancel",
    "runtime.complete": "complete",
    "runtime.recover": "recover",
}


def create_actor(app, key: str, *, active: bool = True) -> str:
    actor = app.state.identity_service.create_agent(
        CreateAgentRequest(stable_key=key, display_name=key, agent_type="coordinator")
    )
    if active:
        app.state.identity_service.transition(actor.id, "active")
    return actor.id


def ensure_permissions(app) -> dict[str, str]:
    identity = app.state.identity_service
    ids: dict[str, str] = {}
    for index, (stable_key, action) in enumerate(RUNTIME_KEYS.items()):
        row = identity.create_definition(
            "permission",
            CreatePermissionRequest(
                stable_key=stable_key,
                display_name=stable_key,
                resource_type="task",
                action=f"runtime_{action}_{index}",
            ),
        )
        ids[stable_key] = row.id
    return ids


def grant(
    app, actor_id: str, permission_ids: dict[str, str], *keys: str, task_id: str = "task-1"
) -> None:
    for key in keys:
        app.state.identity_service.assign_permission(
            actor_id,
            AssignPermissionRequest(
                permission_id=permission_ids[key],
                effect="allow",
                resource_type="task",
                resource_id=task_id,
            ),
        )


def command_body(actor_id: str, *, run_id: str = "run-auth-1", task_id: str = "task-1") -> dict:
    return {
        "command_type": "create",
        "specification": make_spec(run_id=run_id, task_id=task_id).model_dump(mode="json"),
        "command_id": f"cmd-create-{run_id}",
        "expected_run_version": 0,
        "timestamp": ts(0).isoformat(),
        "actor_reference": actor_id,
    }


def test_runtime_authorization_rejects_missing_unknown_inactive_and_impersonation(tmp_path) -> None:
    app = create_app(delay_ms=1, database_url=database_url(tmp_path / "auth-basic.db"))
    with TestClient(app) as client:
        permissions = ensure_permissions(app)
        actor_id = create_actor(app, "runtime-auth-good")
        inactive_id = create_actor(app, "runtime-auth-inactive", active=False)
        grant(app, actor_id, permissions, "runtime.create")
        body = command_body(actor_id)

        missing = client.post("/api/agent-runtime/commands", json=body)
        assert missing.status_code == 401
        assert missing.json()["error"]["code"] == "runtime_authentication_required"

        unknown = client.post(
            "/api/agent-runtime/commands", json=body, headers={"X-Jarvis-Actor-Id": "missing"}
        )
        assert unknown.status_code == 404
        assert unknown.json()["error"]["code"] == "runtime_actor_not_found"

        inactive = client.post(
            "/api/agent-runtime/commands",
            json=command_body(inactive_id, run_id="run-inactive"),
            headers={"X-Jarvis-Actor-Id": inactive_id},
        )
        assert inactive.status_code == 403
        assert inactive.json()["error"]["code"] == "runtime_actor_inactive"

        mismatch = client.post(
            "/api/agent-runtime/commands",
            json=body | {"actor_reference": inactive_id},
            headers={"X-Jarvis-Actor-Id": actor_id},
        )
        assert mismatch.status_code == 400
        assert mismatch.json()["error"]["code"] == "runtime_actor_mismatch"


def test_runtime_authorization_scope_replay_and_read_boundaries(tmp_path) -> None:
    app = create_app(delay_ms=1, database_url=database_url(tmp_path / "auth-scope.db"))
    with TestClient(app) as client:
        permissions = ensure_permissions(app)
        owner = create_actor(app, "runtime-auth-owner")
        reader = create_actor(app, "runtime-auth-reader")
        wrong_scope = create_actor(app, "runtime-auth-wrong-scope")
        grant(app, owner, permissions, "runtime.create", "runtime.read", task_id="task-1")
        grant(app, reader, permissions, "runtime.read", "runtime.create", task_id="task-1")
        grant(app, wrong_scope, permissions, "runtime.create", "runtime.read", task_id="other-task")
        body = command_body(owner)

        created = client.post(
            "/api/agent-runtime/commands", json=body, headers={"X-Jarvis-Actor-Id": owner}
        )
        assert created.status_code == 200

        replay = client.post(
            "/api/agent-runtime/commands", json=body, headers={"X-Jarvis-Actor-Id": owner}
        )
        assert replay.status_code == 200
        assert replay.json()["data"]["idempotent_replay"] is True

        replay_other = client.post(
            "/api/agent-runtime/commands",
            json=body | {"actor_reference": reader},
            headers={"X-Jarvis-Actor-Id": reader},
        )
        assert replay_other.status_code == 403
        assert replay_other.json()["error"]["code"] == "runtime_replay_actor_mismatch"

        assert (
            client.get(
                "/api/agent-runtime/runs/run-auth-1", headers={"X-Jarvis-Actor-Id": reader}
            ).status_code
            == 200
        )
        hidden = client.get(
            "/api/agent-runtime/runs/run-auth-1", headers={"X-Jarvis-Actor-Id": wrong_scope}
        )
        assert hidden.status_code == 403
        assert hidden.json()["error"]["code"] == "runtime_permission_denied"
        listed = client.get(
            "/api/agent-runtime/runs",
            headers={"X-Jarvis-Actor-Id": wrong_scope},
            params={"limit": 10},
        )
        assert listed.status_code == 200
        assert listed.json()["data"]["items"] == []
        assert listed.json()["data"]["total_count"] == 0
        for suffix in ("events", "attempts", "checkpoints", "lineage"):
            assert (
                client.get(
                    f"/api/agent-runtime/runs/run-auth-1/{suffix}",
                    headers={"X-Jarvis-Actor-Id": wrong_scope},
                ).status_code
                == 403
            )


def test_unauthorized_runtime_command_writes_no_artifacts(tmp_path) -> None:
    app = create_app(delay_ms=1, database_url=database_url(tmp_path / "auth-no-artifacts.db"))
    with TestClient(app) as client:
        ensure_permissions(app)
        actor_id = create_actor(app, "runtime-auth-denied")
        body = command_body(actor_id)
        denied = client.post(
            "/api/agent-runtime/commands", json=body, headers={"X-Jarvis-Actor-Id": actor_id}
        )
        assert denied.status_code == 403
        assert app.state.agent_runtime_repository.load_run("run-auth-1") is None
        assert (
            app.state.agent_runtime_repository.get_processed_command(
                "run-auth-1", body["command_id"]
            )
            is None
        )
        runtime_audits = [
            item
            for item in client.get("/api/audit-events").json()["data"]
            if item["eventType"] == "agent_runtime.command"
        ]
        assert runtime_audits == []
        with app.state.repository.session_factory() as session:
            runtime_outbox = list(
                session.scalars(
                    select(OutboxEventRow).where(OutboxEventRow.event_type.like("agent_runtime.%"))
                )
            )
        assert runtime_outbox == []


def test_runtime_authorization_respects_task_resource_policy_denies(tmp_path) -> None:
    app = create_app(delay_ms=1, database_url=database_url(tmp_path / "auth-resource-policy.db"))
    with TestClient(app) as client:
        permissions = ensure_permissions(app)
        actor_id = create_actor(app, "runtime-auth-policy")
        grant(app, actor_id, permissions, "runtime.create", "runtime.read", task_id="task-1")
        add_policy(
            app,
            subject_type="all",
            resource_id="task-1",
            action=permission_action(app, "runtime.create"),
            effect="deny",
            access_state="blocked",
        )
        body = command_body(actor_id, run_id="policy-denied-create")
        denied_create = client.post(
            "/api/agent-runtime/commands", json=body, headers={"X-Jarvis-Actor-Id": actor_id}
        )
        assert denied_create.status_code == 403
        assert denied_create.json()["error"]["code"] == "runtime_permission_denied"
        assert app.state.agent_runtime_repository.load_run("policy-denied-create") is None

        # Seed a run internally, then block task view to prove protected reads also honor resource policies.
        create_runtime = __import__(
            "tests.test_agent_runtime_lineage_pagination",
            fromlist=["create_runtime_run"],
        ).create_runtime_run
        create_runtime(app, "policy-read-run", task_id="task-1", index=1)
        add_policy(
            app,
            subject_type="all",
            resource_id="task-1",
            action=permission_action(app, "runtime.read"),
            effect="deny",
            access_state="blocked",
        )
        denied_read = client.get(
            "/api/agent-runtime/runs/policy-read-run",
            headers={"X-Jarvis-Actor-Id": actor_id},
        )
        assert denied_read.status_code == 403
        assert denied_read.json()["error"]["code"] == "runtime_permission_denied"


def permission_action(app, stable_key: str) -> str:
    with app.state.identity_service.sessions() as session:
        return session.scalar(
            select(IdentityPermissionRow.action).where(
                IdentityPermissionRow.stable_key == stable_key
            )
        )


def add_policy(
    app,
    *,
    subject_type: str,
    resource_id: str,
    action: str,
    effect: str = "deny",
    access_state: str | None = None,
    subject_id: str | None = None,
    starts_at=None,
    expires_at=None,
    revoked_at=None,
) -> None:
    now = datetime.now(UTC)
    with app.state.identity_service.sessions.begin() as session:
        session.add(
            ResourceAccessPolicyRow(
                id=f"policy-{uuid4().hex}",
                subject_type=subject_type,
                subject_id=subject_id,
                resource_type="task",
                resource_id=resource_id,
                action=action,
                effect=effect,
                access_state=access_state,
                starts_at=starts_at or now - timedelta(minutes=1),
                expires_at=expires_at,
                reason="runtime policy test",
                created_by=None,
                created_at=now,
                revoked_at=revoked_at,
            )
        )


def test_runtime_resource_policy_uses_permission_definition_action(tmp_path) -> None:
    app = create_app(delay_ms=1, database_url=database_url(tmp_path / "auth-policy-action.db"))
    with TestClient(app) as client:
        permissions = ensure_permissions(app)
        actor_id = create_actor(app, "runtime-policy-action")
        grant(app, actor_id, permissions, "runtime.create", task_id="task-1")
        actual_action = permission_action(app, "runtime.create")
        assert actual_action not in {"create", "runtime.create", "manage"}
        for wrong in ("create", "runtime.create", "manage"):
            add_policy(app, subject_type="all", resource_id="task-1", action=wrong, effect="deny")
        allowed = client.post(
            "/api/agent-runtime/commands",
            json=command_body(actor_id, run_id="policy-action-allowed"),
            headers={"X-Jarvis-Actor-Id": actor_id},
        )
        assert allowed.status_code == 200
        add_policy(
            app, subject_type="all", resource_id="task-1", action=actual_action, effect="deny"
        )
        denied = client.post(
            "/api/agent-runtime/commands",
            json=command_body(actor_id, run_id="policy-action-denied"),
            headers={"X-Jarvis-Actor-Id": actor_id},
        )
        assert denied.status_code == 403
        assert app.state.agent_runtime_repository.load_run("policy-action-denied") is None


def test_runtime_resource_policy_subject_types_and_admin_override(tmp_path) -> None:
    app = create_app(delay_ms=1, database_url=database_url(tmp_path / "auth-policy-subjects.db"))
    with TestClient(app) as client:
        permissions = ensure_permissions(app)
        identity = app.state.identity_service
        rank = identity.create_definition(
            "rank",
            CreateRankRequest(
                stable_key="runtime.policy.rank",
                display_name="Runtime Rank",
                priority_level=42,
                hierarchy_level=42,
            ),
        )
        actor = identity.create_agent(
            CreateAgentRequest(
                stable_key="runtime-policy-subject",
                display_name="runtime-policy-subject",
                agent_type="coordinator",
                rank_id=rank.id,
            )
        )
        identity.transition(actor.id, "active")
        role = identity.create_definition(
            "role",
            CreateRoleRequest(
                stable_key="runtime.policy.role",
                display_name="Runtime Role",
                role_scope="resource",
            ),
        )
        identity.assign_role(
            actor.id,
            AssignRoleRequest(role_id=role.id, scope_type="resource", scope_id="task-1"),
        )
        team = identity.create_definition(
            "team",
            CreateTeamRequest(
                stable_key="runtime.policy.team",
                display_name="Runtime Team",
                team_type="runtime",
            ),
        )
        with identity.sessions.begin() as session:
            session.add(
                TeamMembershipRow(
                    id=f"tm-{uuid4().hex}",
                    team_id=team.id,
                    agent_id=actor.id,
                    membership_role="member",
                    starts_at=datetime.now(UTC) - timedelta(minutes=1),
                    expires_at=None,
                    assigned_by=None,
                    created_at=datetime.now(UTC),
                    revoked_at=None,
                )
            )
        grant(app, actor.id, permissions, "runtime.create", task_id="task-1")
        action = permission_action(app, "runtime.create")
        for subject_type, subject_id, run_id in (
            ("agent", actor.id, "policy-agent-deny"),
            ("role", role.id, "policy-role-deny"),
            ("rank", rank.id, "policy-rank-deny"),
            ("team", team.id, "policy-team-deny"),
            ("all", None, "policy-all-deny"),
        ):
            add_policy(
                app,
                subject_type=subject_type,
                subject_id=subject_id,
                resource_id="task-1",
                action=action,
                effect="deny",
                access_state="blocked",
            )
            response = client.post(
                "/api/agent-runtime/commands",
                json=command_body(actor.id, run_id=run_id),
                headers={"X-Jarvis-Actor-Id": actor.id},
            )
            assert response.status_code == 403
            assert app.state.agent_runtime_repository.load_run(run_id) is None
            with app.state.identity_service.sessions.begin() as session:
                for policy in session.scalars(select(ResourceAccessPolicyRow)):
                    policy.revoked_at = datetime.now(UTC)

        admin = create_actor(app, "runtime-policy-admin")
        grant(app, admin, permissions, "runtime.create", task_id="task-1")
        admin_permission = identity.create_definition(
            "permission",
            CreatePermissionRequest(
                stable_key="runtime.admin",
                display_name="Runtime admin",
                resource_type="administrative_function",
                action="runtime_admin",
            ),
        )
        identity.assign_permission(
            admin,
            AssignPermissionRequest(
                permission_id=admin_permission.id,
                effect="allow",
                resource_type="administrative_function",
                resource_id="agent_runtime",
            ),
        )
        add_policy(
            app,
            subject_type="all",
            resource_id="task-1",
            action=action,
            effect="deny",
            access_state="blocked",
        )
        allowed = client.post(
            "/api/agent-runtime/commands",
            json=command_body(admin, run_id="policy-admin-allowed"),
            headers={"X-Jarvis-Actor-Id": admin},
        )
        assert allowed.status_code == 200


def test_runtime_resource_policy_lifecycle_and_allow_precedence(tmp_path) -> None:
    app = create_app(delay_ms=1, database_url=database_url(tmp_path / "auth-policy-life.db"))
    with TestClient(app) as client:
        permissions = ensure_permissions(app)
        actor_id = create_actor(app, "runtime-policy-life")
        grant(app, actor_id, permissions, "runtime.create", task_id="task-1")
        action = permission_action(app, "runtime.create")
        now = datetime.now(UTC)
        add_policy(
            app,
            subject_type="all",
            resource_id="task-1",
            action=action,
            effect="deny",
            starts_at=now + timedelta(days=1),
        )
        add_policy(
            app,
            subject_type="all",
            resource_id="task-1",
            action=action,
            effect="deny",
            expires_at=now - timedelta(minutes=1),
        )
        add_policy(
            app,
            subject_type="all",
            resource_id="task-2",
            action=action,
            effect="deny",
        )
        add_policy(
            app,
            subject_type="all",
            resource_id="task-1",
            action="unrelated_action",
            effect="deny",
        )
        add_policy(
            app,
            subject_type="all",
            resource_id="task-1",
            action=action,
            effect="deny",
            revoked_at=now,
        )
        allowed = client.post(
            "/api/agent-runtime/commands",
            json=command_body(actor_id, run_id="policy-life-allowed"),
            headers={"X-Jarvis-Actor-Id": actor_id},
        )
        assert allowed.status_code == 200

        # Existing IdentityService semantics: policy allow can grant resource access even without a direct assignment,
        # explicit assignment deny wins before policy allow, and policy deny/blocked wins over allows.
        allow_actor = create_actor(app, "runtime-policy-allow")
        add_policy(
            app,
            subject_type="agent",
            subject_id=allow_actor,
            resource_id="task-1",
            action=action,
            effect="allow",
        )
        decision = app.state.identity_service.check_permission_resource_access(
            allow_actor, "runtime.create", "task", "task-1"
        )
        assert decision.allowed is True
        app.state.identity_service.assign_permission(
            allow_actor,
            AssignPermissionRequest(permission_id=permissions["runtime.create"], effect="deny"),
        )
        denied_by_assignment = app.state.identity_service.check_permission_resource_access(
            allow_actor, "runtime.create", "task", "task-1"
        )
        assert denied_by_assignment.allowed is False
        assert denied_by_assignment.reason_code == "explicit_denial"
        add_policy(
            app,
            subject_type="agent",
            subject_id=allow_actor,
            resource_id="task-1",
            action=action,
            effect="deny",
        )
        denied_by_policy = app.state.identity_service.check_permission_resource_access(
            allow_actor, "runtime.create", "task", "task-1"
        )
        assert denied_by_policy.allowed is False
        assert denied_by_policy.reason_code == "resource_denial"
