from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db.models import (
    AgentRuntimeAttemptRow,
    AgentRuntimeCheckpointRow,
    AgentRuntimeEventRow,
    AgentRuntimeProcessedCommandRow,
    AgentRuntimeRunRow,
    AuditEventRow,
    OutboxEventRow,
)
from app.main import create_app
from app.models.identity import AssignPermissionRequest, CreatePermissionRequest
from tests.agent_runtime_testkit import make_spec, ts
from tests.test_agent_runtime_authorization import create_actor, ensure_permissions, grant
from tests.test_agent_runtime_lineage_pagination import create_runtime_run
from tests.test_persistence import database_url


def artifact_counts(app) -> dict[str, int]:
    with app.state.repository.session_factory() as session:
        return {
            "runs": session.scalar(select(func.count()).select_from(AgentRuntimeRunRow)) or 0,
            "events": session.scalar(select(func.count()).select_from(AgentRuntimeEventRow)) or 0,
            "attempts": session.scalar(select(func.count()).select_from(AgentRuntimeAttemptRow))
            or 0,
            "checkpoints": session.scalar(
                select(func.count()).select_from(AgentRuntimeCheckpointRow)
            )
            or 0,
            "processed": session.scalar(
                select(func.count()).select_from(AgentRuntimeProcessedCommandRow)
            )
            or 0,
            "audit": session.scalar(
                select(func.count())
                .select_from(AuditEventRow)
                .where(AuditEventRow.event_type == "agent_runtime.command")
            )
            or 0,
            "outbox": session.scalar(
                select(func.count())
                .select_from(OutboxEventRow)
                .where(OutboxEventRow.event_type.like("agent_runtime.%"))
            )
            or 0,
        }


def create_body(actor_id: str, run_id: str, task_id: str, parent_run_id: str | None) -> dict:
    return {
        "command_type": "create",
        "specification": make_spec(
            run_id=run_id,
            task_id=task_id,
            parent_run_id=parent_run_id,
            correlation_id=f"corr-{run_id}",
        ).model_dump(mode="json"),
        "command_id": f"cmd-{run_id}",
        "expected_run_version": 0,
        "timestamp": ts(20).isoformat(),
        "actor_reference": actor_id,
    }


def assert_no_child_artifacts(app, before: dict[str, int], child_run_id: str) -> None:
    assert artifact_counts(app) == before
    assert app.state.agent_runtime_repository.load_run(child_run_id) is None


def assert_no_leak(response, *protected_values: str) -> None:
    payload = str(response.json())
    for value in protected_values:
        assert value not in payload
    for marker in ("SELECT", "Traceback", ".db", "permission-", "role-"):
        assert marker not in payload


def test_create_with_authorized_parent_same_task_and_admin_paths(tmp_path) -> None:
    app = create_app(delay_ms=1, database_url=database_url(tmp_path / "parent-ok.db"))
    with TestClient(app) as client:
        permissions = ensure_permissions(app)
        actor = create_actor(app, "parent-authorized")
        admin = create_actor(app, "parent-admin")
        grant(app, actor, permissions, "runtime.create", "runtime.read", task_id="task-parent")
        grant(app, actor, permissions, "runtime.create", "runtime.read", task_id="task-child")
        grant(app, admin, permissions, "runtime.create", task_id="task-child")
        admin_permission = app.state.identity_service.create_definition(
            "permission",
            CreatePermissionRequest(
                stable_key="runtime.admin",
                display_name="Runtime admin",
                resource_type="administrative_function",
                action="runtime_admin",
            ),
        )
        app.state.identity_service.assign_permission(
            admin,
            AssignPermissionRequest(
                permission_id=admin_permission.id,
                effect="allow",
                resource_type="administrative_function",
                resource_id="agent_runtime",
            ),
        )
        create_runtime_run(app, "parent-same", task_id="task-parent", index=1)
        create_runtime_run(app, "grandparent", task_id="task-parent", index=2)
        create_runtime_run(
            app, "parent-chain", task_id="task-parent", parent_run_id="grandparent", index=3
        )

        same_task = client.post(
            "/api/agent-runtime/commands",
            json=create_body(actor, "child-same", "task-parent", "parent-same"),
            headers={"X-Jarvis-Actor-Id": actor},
        )
        assert same_task.status_code == 200
        cross_task = client.post(
            "/api/agent-runtime/commands",
            json=create_body(actor, "child-cross", "task-child", "parent-chain"),
            headers={"X-Jarvis-Actor-Id": actor},
        )
        assert cross_task.status_code == 200
        admin_create = client.post(
            "/api/agent-runtime/commands",
            json=create_body(admin, "child-admin", "task-child", "parent-chain"),
            headers={"X-Jarvis-Actor-Id": admin},
        )
        assert admin_create.status_code == 200
        audit_payload = next(
            row
            for row in client.get("/api/audit-events").json()["data"]
            if row["payload"].get("runId") == "child-admin"
        )["payload"]
        assert audit_payload["authorization"]["parentCheckRequired"] is True
        assert audit_payload["authorization"]["parentCheckPerformed"] is True
        assert audit_payload["authorization"]["parentCheckAllowedByAdmin"] is True


def test_create_with_unauthorized_parent_is_403_bounded_and_zero_artifact(tmp_path) -> None:
    app = create_app(delay_ms=1, database_url=database_url(tmp_path / "parent-denied.db"))
    with TestClient(app) as client:
        permissions = ensure_permissions(app)
        actor = create_actor(app, "parent-denied")
        grant(app, actor, permissions, "runtime.create", task_id="task-child")
        create_runtime_run(
            app, "secret-parent", task_id="secret-task", agent_id="secret-agent", index=7
        )
        before = artifact_counts(app)
        response = client.post(
            "/api/agent-runtime/commands",
            json=create_body(actor, "denied-child", "task-child", "secret-parent"),
            headers={"X-Jarvis-Actor-Id": actor},
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "runtime_parent_unavailable"
        assert_no_leak(response, "secret-parent", "secret-task", "secret-agent", "corr-7")
        assert_no_child_artifacts(app, before, "denied-child")


def test_create_parent_expired_and_explicit_deny_are_zero_artifact(tmp_path) -> None:
    app = create_app(delay_ms=1, database_url=database_url(tmp_path / "parent-policy.db"))
    with TestClient(app) as client:
        permissions = ensure_permissions(app)
        expired = create_actor(app, "parent-expired")
        denied = create_actor(app, "parent-explicit-deny")
        for actor in (expired, denied):
            grant(app, actor, permissions, "runtime.create", task_id="task-child")
        now = datetime.now(UTC)
        app.state.identity_service.assign_permission(
            expired,
            AssignPermissionRequest(
                permission_id=permissions["runtime.read"],
                effect="allow",
                resource_type="task",
                resource_id="task-parent",
                starts_at=now - timedelta(days=2),
                expires_at=now - timedelta(days=1),
            ),
        )
        grant(app, denied, permissions, "runtime.read", task_id="task-parent")
        app.state.identity_service.assign_permission(
            denied,
            AssignPermissionRequest(
                permission_id=permissions["runtime.read"],
                effect="deny",
                resource_type="task",
                resource_id="task-parent",
            ),
        )
        create_runtime_run(app, "policy-parent", task_id="task-parent", index=1)
        for actor, child in ((expired, "child-expired"), (denied, "child-denied")):
            before = artifact_counts(app)
            response = client.post(
                "/api/agent-runtime/commands",
                json=create_body(actor, child, "task-child", "policy-parent"),
                headers={"X-Jarvis-Actor-Id": actor},
            )
            assert response.status_code == 404
            assert response.json()["error"]["code"] == "runtime_parent_unavailable"
            assert_no_leak(response, "policy-parent", "task-parent")
            assert_no_child_artifacts(app, before, child)


def test_create_parent_replay_requires_current_parent_authorization_and_same_actor(
    tmp_path,
) -> None:
    app = create_app(delay_ms=1, database_url=database_url(tmp_path / "parent-replay.db"))
    with TestClient(app) as client:
        permissions = ensure_permissions(app)
        actor = create_actor(app, "parent-replay-owner")
        other = create_actor(app, "parent-replay-other")
        for actor_id in (actor, other):
            grant(app, actor_id, permissions, "runtime.create", task_id="task-child")
            grant(app, actor_id, permissions, "runtime.read", task_id="task-parent")
        create_runtime_run(app, "replay-parent", task_id="task-parent", index=1)
        body = create_body(actor, "replay-child", "task-child", "replay-parent")
        created = client.post(
            "/api/agent-runtime/commands", json=body, headers={"X-Jarvis-Actor-Id": actor}
        )
        assert created.status_code == 200
        before = artifact_counts(app)
        replay = client.post(
            "/api/agent-runtime/commands", json=body, headers={"X-Jarvis-Actor-Id": actor}
        )
        assert replay.status_code == 200
        assert replay.json()["data"]["idempotent_replay"] is True
        assert artifact_counts(app) == before

        other_replay = client.post(
            "/api/agent-runtime/commands",
            json=body | {"actor_reference": other},
            headers={"X-Jarvis-Actor-Id": other},
        )
        assert other_replay.status_code == 403
        assert other_replay.json()["error"]["code"] == "runtime_replay_actor_mismatch"
        assert artifact_counts(app) == before

        app.state.identity_service.assign_permission(
            actor,
            AssignPermissionRequest(
                permission_id=permissions["runtime.read"],
                effect="deny",
                resource_type="task",
                resource_id="task-parent",
            ),
        )
        lost_permission = client.post(
            "/api/agent-runtime/commands", json=body, headers={"X-Jarvis-Actor-Id": actor}
        )
        assert lost_permission.status_code == 404
        assert lost_permission.json()["error"]["code"] == "runtime_parent_unavailable"
        assert artifact_counts(app) == before


def test_parent_unavailable_responses_are_indistinguishable_and_zero_artifact(
    tmp_path,
) -> None:
    app = create_app(delay_ms=1, database_url=database_url(tmp_path / "parent-enumeration.db"))
    with TestClient(app) as client:
        permissions = ensure_permissions(app)
        actor = create_actor(app, "parent-enumeration")
        grant(app, actor, permissions, "runtime.create", task_id="task-child")
        now = datetime.now(UTC)

        create_runtime_run(
            app, "secret-parent", task_id="secret-task", agent_id="secret-agent", index=1
        )
        create_runtime_run(
            app, "denied-parent", task_id="denied-task", agent_id="denied-agent", index=2
        )
        create_runtime_run(
            app, "expired-parent", task_id="expired-task", agent_id="expired-agent", index=3
        )
        create_runtime_run(
            app,
            "missing-grandparent-parent",
            task_id="task-child",
            parent_run_id="missing-grandparent",
            index=4,
        )
        create_runtime_run(
            app, "secret-grandparent", task_id="grand-secret", agent_id="grand-agent", index=5
        )
        create_runtime_run(
            app,
            "unreadable-grandparent-parent",
            task_id="task-child",
            parent_run_id="secret-grandparent",
            index=6,
        )

        grant(app, actor, permissions, "runtime.read", task_id="denied-task")
        app.state.identity_service.assign_permission(
            actor,
            AssignPermissionRequest(
                permission_id=permissions["runtime.read"],
                effect="deny",
                resource_type="task",
                resource_id="denied-task",
            ),
        )
        app.state.identity_service.assign_permission(
            actor,
            AssignPermissionRequest(
                permission_id=permissions["runtime.read"],
                effect="allow",
                resource_type="task",
                resource_id="expired-task",
                starts_at=now - timedelta(days=2),
                expires_at=now - timedelta(days=1),
            ),
        )
        grant(app, actor, permissions, "runtime.read", task_id="task-child")

        cases = (
            ("missing-parent-child", "missing-parent"),
            ("unreadable-parent", "secret-parent"),
            ("denied-parent-child", "denied-parent"),
            ("expired-parent-child", "expired-parent"),
            ("missing-grandparent-child", "missing-grandparent-parent"),
            ("unreadable-grandparent-child", "unreadable-grandparent-parent"),
        )
        bodies = []
        before = artifact_counts(app)
        for child_run_id, parent_run_id in cases:
            response = client.post(
                "/api/agent-runtime/commands",
                json=create_body(actor, child_run_id, "task-child", parent_run_id),
                headers={"X-Jarvis-Actor-Id": actor},
            )
            assert response.status_code == 404
            bodies.append(response.json())
            assert_no_leak(
                response,
                parent_run_id,
                "secret-parent",
                "secret-task",
                "secret-agent",
                "denied-task",
                "expired-task",
                "secret-grandparent",
                "grand-secret",
                "grand-agent",
            )
            assert_no_child_artifacts(app, before, child_run_id)
        assert all(body == bodies[0] for body in bodies)
        assert bodies[0]["error"]["code"] == "runtime_parent_unavailable"


def test_child_authorization_precedence_hides_parent_state(tmp_path) -> None:
    app = create_app(delay_ms=1, database_url=database_url(tmp_path / "parent-child-precedence.db"))
    with TestClient(app) as client:
        permissions = ensure_permissions(app)
        no_create = create_actor(app, "parent-no-create")
        grant(app, no_create, permissions, "runtime.read", task_id="task-parent")
        create_runtime_run(app, "readable-parent", task_id="task-parent", index=2)
        before = artifact_counts(app)
        missing = client.post(
            "/api/agent-runtime/commands",
            json=create_body(no_create, "child-no-create-missing", "task-child", "missing-parent"),
            headers={"X-Jarvis-Actor-Id": no_create},
        )
        existing = client.post(
            "/api/agent-runtime/commands",
            json=create_body(
                no_create, "child-no-create-existing", "task-child", "readable-parent"
            ),
            headers={"X-Jarvis-Actor-Id": no_create},
        )
        assert missing.status_code == existing.status_code == 403
        assert missing.json() == existing.json()
        assert missing.json()["error"]["code"] == "runtime_permission_denied"
        assert_no_leak(missing, "missing-parent", "readable-parent", "task-parent")
        assert_no_leak(existing, "missing-parent", "readable-parent", "task-parent")
        assert_no_child_artifacts(app, before, "child-no-create-missing")
        assert app.state.agent_runtime_repository.load_run("child-no-create-existing") is None


def test_create_parent_structural_failures_are_zero_artifact(tmp_path) -> None:
    app = create_app(delay_ms=1, database_url=database_url(tmp_path / "parent-structural.db"))
    with TestClient(app) as client:
        permissions = ensure_permissions(app)
        actor = create_actor(app, "parent-structural")
        grant(app, actor, permissions, "runtime.create", "runtime.read", task_id="task-1")
        self_parent_body = create_body(actor, "self-parent", "task-1", None)
        self_parent_body["specification"]["parent_run_id"] = "self-parent"
        before = artifact_counts(app)
        self_parent = client.post(
            "/api/agent-runtime/commands",
            json=self_parent_body,
            headers={"X-Jarvis-Actor-Id": actor},
        )
        assert self_parent.status_code in {400, 422}
        assert_no_child_artifacts(app, before, "self-parent")

        create_runtime_run(app, "cycle-a", task_id="task-1", index=1)
        create_runtime_run(app, "cycle-b", task_id="task-1", parent_run_id="cycle-a", index=2)
        import json

        with app.state.agent_runtime_repository.sessions.begin() as session:
            row = session.get(AgentRuntimeRunRow, "cycle-a")
            snapshot = json.loads(row.snapshot_json)
            snapshot["specification"]["parent_run_id"] = "cycle-b"
            row.snapshot_json = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
        before_cycle = artifact_counts(app)
        cycle = client.post(
            "/api/agent-runtime/commands",
            json=create_body(actor, "cycle-child", "task-1", "cycle-b"),
            headers={"X-Jarvis-Actor-Id": actor},
        )
        assert cycle.status_code == 400
        assert cycle.json()["error"]["code"] == "invalid_runtime_metadata"
        assert_no_child_artifacts(app, before_cycle, "cycle-child")


def test_parent_deleted_between_authorization_and_commit_is_zero_artifact(tmp_path) -> None:
    app = create_app(delay_ms=1, database_url=database_url(tmp_path / "parent-race.db"))
    with TestClient(app) as client:
        permissions = ensure_permissions(app)
        actor = create_actor(app, "parent-race")
        grant(app, actor, permissions, "runtime.create", task_id="task-child")
        grant(app, actor, permissions, "runtime.read", task_id="task-parent")
        create_runtime_run(app, "race-parent", task_id="task-parent", index=1)
        original = app.state.agent_runtime_repository._awaiting_run_lock

        def delete_parent(run_id: str, command_id: str) -> None:
            if run_id == "race-child":
                with app.state.agent_runtime_repository.sessions.begin() as session:
                    parent = session.get(AgentRuntimeRunRow, "race-parent")
                    session.delete(parent)
            original(run_id, command_id)

        app.state.agent_runtime_repository._awaiting_run_lock = delete_parent
        response = client.post(
            "/api/agent-runtime/commands",
            json=create_body(actor, "race-child", "task-child", "race-parent"),
            headers={"X-Jarvis-Actor-Id": actor},
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "runtime_parent_unavailable"
        # The parent deletion is the simulated race; no child artifacts are added.
        assert app.state.agent_runtime_repository.load_run("race-child") is None
        with app.state.repository.session_factory() as session:
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(AgentRuntimeEventRow)
                    .where(AgentRuntimeEventRow.run_id == "race-child")
                )
                == 0
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(AgentRuntimeProcessedCommandRow)
                    .where(AgentRuntimeProcessedCommandRow.run_id == "race-child")
                )
                == 0
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(AuditEventRow)
                    .where(AuditEventRow.payload.contains("race-child"))
                )
                == 0
            )
