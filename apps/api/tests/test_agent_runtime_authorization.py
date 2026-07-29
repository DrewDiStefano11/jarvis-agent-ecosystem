from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import OutboxEventRow
from app.main import create_app
from app.models.identity import AssignPermissionRequest, CreateAgentRequest, CreatePermissionRequest
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
