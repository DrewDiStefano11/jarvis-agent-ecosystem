"""Browser approval remains a native exact-plan decision without source effects."""

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.models.identity import AssignPermissionRequest, CreatePermissionRequest

pytest_plugins = ["tests.test_self_build_workspaces"]


def test_browser_workspace_preview_approval_rechecks_hash_and_stop(
    workspace, tmp_path, monkeypatch
):
    original, _, _, reservation, workspace_scope, policy = workspace
    operator_id = original.state.remote_control_service.access.actor_id
    permission = original.state.identity_service.create_definition(
        "permission",
        CreatePermissionRequest(
            stable_key="remote.read",
            display_name="Read remote",
            resource_type="administrative_function",
            action="remote_read",
        ),
    )
    original.state.identity_service.assign_permission(
        operator_id,
        AssignPermissionRequest(
            permission_id=permission.id,
            effect="allow",
            resource_type="administrative_function",
            resource_id="remote_control",
        ),
    )
    assets = tmp_path / "browser"
    assets.mkdir()
    (assets / "remote.html").write_text("<!doctype html><title>fixture</title>")
    for name, value in {
        "JARVIS_REMOTE_CONTROL_ENABLED": "true",
        "JARVIS_REMOTE_BROWSER_ENABLED": "true",
        "JARVIS_REMOTE_BROWSER_ASSETS": str(assets),
        "JARVIS_REMOTE_OPERATOR_ID": operator_id,
        "JARVIS_REMOTE_OPERATOR_TOKEN": "x" * 48,
        "JARVIS_REMOTE_ORIGIN": "https://remote.test:8443",
        "JARVIS_SELF_BUILD_ENABLED": "true",
        "JARVIS_SELF_BUILD_REPOSITORIES_JSON": original.state.settings.self_build_repositories_json,
    }.items():
        monkeypatch.setenv(name, value)
    app = create_app(database_url=original.state.settings.database_url)
    with TestClient(app, base_url="https://remote.test:8443") as client:
        signed = client.post(
            "/api/remote/session",
            headers={"Origin": "https://remote.test:8443", "Authorization": "Bearer " + "x" * 48},
        )
        assert signed.status_code == 200, signed.text
        headers = {
            "Origin": "https://remote.test:8443",
            "X-Jarvis-CSRF": signed.json()["data"]["csrfToken"],
        }
        intent = {
            name: getattr(reservation, name)
            for name in ("repository_id", "runtime_run_id", "base_sha")
        }
        preview = client.post(
            "/api/remote/self-build/workspaces/preview", json=intent, headers=headers
        )
        assert preview.status_code == 200, preview.text
        plan = preview.json()["data"]
        assert plan["plan_hash"] == reservation.expected_plan_hash
        bad = client.post(
            "/api/remote/self-build/workspaces/approve",
            json=intent | {"expected_plan_hash": "0" * 64},
            headers=headers,
        )
        assert bad.status_code == 409
        approved = client.post(
            "/api/remote/self-build/workspaces/approve",
            json=intent | {"expected_plan_hash": plan["plan_hash"]},
            headers=headers,
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["data"]["approved_by"] == operator_id
        app.state.repository.emergency_stop = True
        app.state.repository.persist()
        stopped = client.post(
            "/api/remote/self-build/workspaces/approve",
            json=intent | {"expected_plan_hash": plan["plan_hash"]},
            headers=headers,
        )
        assert stopped.status_code == 423
        app.state.repository.emergency_stop = False
        app.state.repository.persist()
        app.state.identity_service.assign_permission(
            operator_id, AssignPermissionRequest(effect="deny", **workspace_scope)
        )
        denied = client.post(
            "/api/remote/self-build/workspaces/approve",
            json=intent | {"expected_plan_hash": plan["plan_hash"]},
            headers=headers,
        )
        assert denied.status_code == 403
        malformed = client.post(
            "/api/remote/self-build/workspaces/preview",
            json=intent | {"execute": True},
            headers=headers,
        )
        assert malformed.status_code == 422
        assert list(Path(policy["worktree_root"]).iterdir()) == []
