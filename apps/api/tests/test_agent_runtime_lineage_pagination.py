from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app
from app.models.agent_runtime import CreateAgentRunCommand
from tests.agent_runtime_testkit import make_spec, ts
from tests.test_agent_runtime_authorization import create_actor, ensure_permissions, grant
from tests.test_persistence import database_url


def create_runtime_run(
    app,
    run_id: str,
    *,
    task_id: str = "task-1",
    agent_id: str = "agent-1",
    parent_run_id: str | None = None,
    index: int = 0,
) -> None:
    spec = make_spec(
        run_id=run_id,
        task_id=task_id,
        agent_id=agent_id,
        parent_run_id=parent_run_id,
        correlation_id=f"corr-{index}",
        created_at=ts(index),
    )
    app.state.agent_runtime_service.create_run(
        CreateAgentRunCommand(
            specification=spec,
            command_id=f"cmd-create-{run_id}",
            expected_run_version=0,
            timestamp=ts(index),
            actor_reference="seed-runtime",
        )
    )


def test_lineage_authorizes_every_ancestor_and_hides_unauthorized_ids(tmp_path) -> None:
    app = create_app(delay_ms=1, database_url=database_url(tmp_path / "lineage-auth.db"))
    with TestClient(app) as client:
        permissions = ensure_permissions(app)
        child_reader = create_actor(app, "lineage-child-reader")
        all_reader = create_actor(app, "lineage-all-reader")
        grant(app, child_reader, permissions, "runtime.read", task_id="task-child")
        grant(app, all_reader, permissions, "runtime.read", task_id="task-child")
        grant(app, all_reader, permissions, "runtime.read", task_id="task-parent")
        create_runtime_run(app, "run-parent", task_id="task-parent", index=1)
        create_runtime_run(
            app, "run-child", task_id="task-child", parent_run_id="run-parent", index=2
        )

        denied = client.get(
            "/api/agent-runtime/runs/run-child/lineage",
            headers={"X-Jarvis-Actor-Id": child_reader},
        )
        assert denied.status_code == 403
        body = denied.json()
        assert body["error"]["code"] == "runtime_permission_denied"
        assert "run-parent" not in str(body)
        assert "task-parent" not in str(body)

        allowed = client.get(
            "/api/agent-runtime/runs/run-child/lineage",
            headers={"X-Jarvis-Actor-Id": all_reader},
        )
        assert allowed.status_code == 200
        assert allowed.json()["data"]["entries"] == [
            {"run_id": "run-parent", "exists": True, "state": "created"}
        ]


def test_lineage_global_admin_missing_and_cycle_behaviors(tmp_path) -> None:
    app = create_app(delay_ms=1, database_url=database_url(tmp_path / "lineage-misc.db"))
    with TestClient(app) as client:
        permissions = ensure_permissions(app)
        actor = create_actor(app, "lineage-global")
        grant(app, actor, permissions, "runtime.read", task_id="task-a")
        grant(app, actor, permissions, "runtime.read", task_id="task-b")
        create_runtime_run(app, "ancestor", task_id="task-b", index=1)
        create_runtime_run(app, "middle", task_id="task-a", parent_run_id="ancestor", index=2)
        create_runtime_run(app, "leaf", task_id="task-a", parent_run_id="middle", index=3)
        ok = client.get(
            "/api/agent-runtime/runs/leaf/lineage", headers={"X-Jarvis-Actor-Id": actor}
        )
        assert ok.status_code == 200
        assert [entry["run_id"] for entry in ok.json()["data"]["entries"]] == [
            "middle",
            "ancestor",
        ]

        create_runtime_run(app, "missing-child", task_id="task-a", parent_run_id="missing", index=4)
        missing = client.get(
            "/api/agent-runtime/runs/missing-child/lineage",
            headers={"X-Jarvis-Actor-Id": actor},
        )
        assert missing.status_code == 200
        assert missing.json()["data"]["missing_parent_id"] == "missing"

        with app.state.agent_runtime_repository.sessions.begin() as session:
            rows = {
                row.run_id: row
                for row in session.query(
                    __import__("app.db.models", fromlist=["AgentRuntimeRunRow"]).AgentRuntimeRunRow
                ).filter(
                    __import__(
                        "app.db.models", fromlist=["AgentRuntimeRunRow"]
                    ).AgentRuntimeRunRow.run_id.in_(["ancestor", "leaf"])
                )
            }
            import json

            snap = json.loads(rows["ancestor"].snapshot_json)
            snap["specification"]["parent_run_id"] = "leaf"
            rows["ancestor"].snapshot_json = json.dumps(snap, sort_keys=True, separators=(",", ":"))
        cycle = client.get(
            "/api/agent-runtime/runs/leaf/lineage", headers={"X-Jarvis-Actor-Id": actor}
        )
        assert cycle.status_code == 400
        assert cycle.json()["error"]["code"] == "invalid_runtime_metadata"


def create_numbered_runs(app, count: int, *, task_cycle: tuple[str, ...] = ("task-1",)) -> None:
    for idx in range(count):
        task_id = task_cycle[idx % len(task_cycle)]
        create_runtime_run(
            app, f"run-page-{idx:02d}", task_id=task_id, agent_id=f"agent-{idx % 3}", index=idx + 1
        )


def run_ids(response) -> list[str]:
    return [item["specification"]["run_id"] for item in response.json()["data"]["items"]]


def test_authorized_pagination_exact_final_page_and_next_page(tmp_path) -> None:
    app = create_app(delay_ms=1, database_url=database_url(tmp_path / "page-final.db"))
    with TestClient(app) as client:
        permissions = ensure_permissions(app)
        actor = create_actor(app, "pager-exact")
        grant(app, actor, permissions, "runtime.read", task_id="task-1")
        create_numbered_runs(app, 10)
        first = client.get(
            "/api/agent-runtime/runs",
            headers={"X-Jarvis-Actor-Id": actor},
            params={"limit": 10},
        )
        assert len(run_ids(first)) == 10
        assert first.json()["data"]["next_offset"] is None

        create_runtime_run(app, "run-page-10", index=11)
        page1 = client.get(
            "/api/agent-runtime/runs",
            headers={"X-Jarvis-Actor-Id": actor},
            params={"limit": 10},
        )
        assert len(run_ids(page1)) == 10
        assert page1.json()["data"]["next_offset"] > 0
        page2 = client.get(
            "/api/agent-runtime/runs",
            headers={"X-Jarvis-Actor-Id": actor},
            params={"limit": 10, "offset": page1.json()["data"]["next_offset"]},
        )
        assert run_ids(page2) == ["run-page-10"]
        assert page2.json()["data"]["next_offset"] is None


def test_authorized_pagination_mixed_rows_scan_limit_offsets_and_filters(tmp_path) -> None:
    app = create_app(delay_ms=1, database_url=database_url(tmp_path / "page-mixed.db"))
    with TestClient(app) as client:
        permissions = ensure_permissions(app)
        actor = create_actor(app, "pager-mixed")
        grant(app, actor, permissions, "runtime.read", task_id="visible")
        create_numbered_runs(app, 18, task_cycle=("hidden", "visible", "hidden"))
        seen: list[str] = []
        offset = 0
        for _ in range(10):
            page = client.get(
                "/api/agent-runtime/runs",
                headers={"X-Jarvis-Actor-Id": actor},
                params={"limit": 2, "offset": offset},
            ).json()["data"]
            ids = [item["specification"]["run_id"] for item in page["items"]]
            assert not (set(ids) & set(seen))
            seen.extend(ids)
            if page["next_offset"] is None:
                break
            assert page["next_offset"] > offset
            offset = page["next_offset"]
        assert seen == [f"run-page-{idx:02d}" for idx in (1, 4, 7, 10, 13, 16)]

        empty = client.get(
            "/api/agent-runtime/runs",
            headers={"X-Jarvis-Actor-Id": actor},
            params={"limit": 2, "task_id": "hidden"},
        ).json()["data"]
        assert empty["items"] == []
        assert empty["total_count"] == 0

        filtered = client.get(
            "/api/agent-runtime/runs",
            headers={"X-Jarvis-Actor-Id": actor},
            params={
                "limit": 5,
                "task_id": "visible",
                "agent_id": "agent-1",
                "state": "created",
                "terminal": False,
                "correlation_id": "corr-5",
            },
        ).json()["data"]
        assert [item["specification"]["run_id"] for item in filtered["items"]] == ["run-page-04"]


def test_authorized_pagination_returns_forward_cursor_when_scan_limit_reached(tmp_path) -> None:
    app = create_app(delay_ms=1, database_url=database_url(tmp_path / "page-scan-limit.db"))
    with TestClient(app) as client:
        permissions = ensure_permissions(app)
        actor = create_actor(app, "pager-scan-limit")
        grant(app, actor, permissions, "runtime.read", task_id="visible")
        create_numbered_runs(app, 12, task_cycle=("hidden",))
        create_runtime_run(app, "run-page-visible", task_id="visible", index=30)
        page = client.get(
            "/api/agent-runtime/runs",
            headers={"X-Jarvis-Actor-Id": actor},
            params={"limit": 1},
        ).json()["data"]
        assert page["items"] == []
        assert page["next_offset"] is not None
        assert page["next_offset"] > 0


def test_lineage_parent_expired_permission_deny_and_admin_override(tmp_path) -> None:
    from datetime import UTC, datetime, timedelta

    from app.models.identity import AssignPermissionRequest, CreatePermissionRequest

    app = create_app(delay_ms=1, database_url=database_url(tmp_path / "lineage-policy.db"))
    with TestClient(app) as client:
        permissions = ensure_permissions(app)
        expired = create_actor(app, "lineage-expired")
        denied_actor = create_actor(app, "lineage-denied")
        admin = create_actor(app, "lineage-admin")
        grant(app, expired, permissions, "runtime.read", task_id="task-child")
        grant(app, denied_actor, permissions, "runtime.read", task_id="task-child")
        grant(app, denied_actor, permissions, "runtime.read", task_id="task-parent")
        read_permission_id = permissions["runtime.read"]
        now = datetime.now(UTC)
        app.state.identity_service.assign_permission(
            expired,
            AssignPermissionRequest(
                permission_id=read_permission_id,
                effect="allow",
                resource_type="task",
                resource_id="task-parent",
                starts_at=now - timedelta(days=2),
                expires_at=now - timedelta(days=1),
            ),
        )
        app.state.identity_service.assign_permission(
            denied_actor,
            AssignPermissionRequest(
                permission_id=read_permission_id,
                effect="deny",
                resource_type="task",
                resource_id="task-parent",
            ),
        )
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
        create_runtime_run(app, "policy-parent", task_id="task-parent", index=1)
        create_runtime_run(
            app,
            "policy-child",
            task_id="task-child",
            parent_run_id="policy-parent",
            index=2,
        )
        for actor_id in (expired, denied_actor):
            response = client.get(
                "/api/agent-runtime/runs/policy-child/lineage",
                headers={"X-Jarvis-Actor-Id": actor_id},
            )
            assert response.status_code == 403
            assert "policy-parent" not in str(response.json())
        allowed = client.get(
            "/api/agent-runtime/runs/policy-child/lineage",
            headers={"X-Jarvis-Actor-Id": admin},
        )
        assert allowed.status_code == 200
        assert allowed.json()["data"]["entries"][0]["run_id"] == "policy-parent"
