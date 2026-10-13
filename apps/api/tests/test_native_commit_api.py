"""Registered native contracts remain effect-free without B's trusted consumer."""

from fastapi.testclient import TestClient

from app.agent_runtime.authorization import RuntimeActorContext
from app.autonomous_worker.router import actor
from app.main import create_app


def test_native_api_contracts_and_default_validation_refusal(tmp_path):
    app = create_app(
        database_url="sqlite:///" + (tmp_path / "native-api.db").as_posix(), delay_ms=1
    )
    with TestClient(app) as client:
        paths = client.get("/openapi.json").json()["paths"]
        assert "/api/self-build/workspaces/{workspace_id}/native-commit" in paths
        assert (
            "/api/self-build/workspaces/{workspace_id}/native-commit/{command_id}/recovery" in paths
        )
        # Override identity only to test the missing consumer boundary. No
        # permission, plan, lease or receipt is fabricated into the production DB.
        app.dependency_overrides[actor] = lambda: RuntimeActorContext(
            actor_id="agent-engineer", stable_key="test-identity"
        )
        response = client.post(
            "/api/self-build/workspaces/workspace/native-commit",
            json=dict(
                preparation_command_id="prepared",
                inspection_approval_id="inspection",
                expected_inspection_plan_hash="1" * 64,
                command_id="commit",
                expected_plan_hash="2" * 64,
                approval_id="approval",
                worker_id="worker",
                lease_token="test-token",
            ),
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "GIT_COMMIT_VALIDATION_UNAVAILABLE"
