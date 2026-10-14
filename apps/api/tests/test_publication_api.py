"""Publication routes expose contracts and refuse an unconfigured host."""

from fastapi.testclient import TestClient

from app.agent_runtime.authorization import RuntimeActorContext
from app.autonomous_worker.router import actor
from app.main import create_app


def test_publication_api_is_registered_and_unconfigured_host_fails_closed(tmp_path):
    app = create_app(
        database_url="sqlite:///" + (tmp_path / "publication-api.db").as_posix(), delay_ms=1
    )
    with TestClient(app) as client:
        paths = client.get("/openapi.json").json()["paths"]
        assert (
            "/api/self-build/workspaces/{workspace_id}/publication/{operation_id}/correction-triage"
            in paths
        )
        assert (
            "/api/self-build/workspaces/{workspace_id}/publication/{operation_id}/source-link"
            in paths
        )
        assert "/api/self-build/workspaces/{workspace_id}/publication" in paths
        assert (
            "/api/self-build/workspaces/{workspace_id}/publication/{operation_id}/observe" in paths
        )
        app.dependency_overrides[actor] = lambda: RuntimeActorContext(
            actor_id="agent-engineer", stable_key="test-identity"
        )
        response = client.post(
            "/api/self-build/workspaces/workspace/publication/preview",
            json=dict(
                native_command_id="native-command", validation_journal_id="validation-journal"
            ),
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "GITHUB_PUBLICATION_UNCONFIGURED"
