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


def test_reconfiguration_clears_native_validation_and_replaces_it_safely(tmp_path, monkeypatch):
    from app.core.errors import DomainError
    from app.github_publication.deployment import configure
    from app.github_publication.transport import RepositoryTarget
    from app.self_build.commit_journal import NativeCommitJournal

    app = create_app(database_url="sqlite:///" + (tmp_path / "reconfigure.db").as_posix())
    configuration = dict(
        target=RepositoryTarget(
            owner="example",
            name="jarvis",
            repository_id=1,
            base_branch="main",
            branch_prefix="codex/test/",
        ),
        publisher_ids=(10,),
    )
    calls, intents = [], []

    class Consumer:
        def __init__(self, name):
            self.name = name

        def require_commit(self, principal, workspace_id, request):
            calls.append(self.name)

    def missing_intent(*args):
        raise DomainError("GIT_COMMIT_EXECUTION_NOT_FOUND", "Fixture has no intent.", 404)

    def stop_before_intent(*args):
        intents.append("intent boundary")
        raise DomainError("TEST_INTENT_BOUNDARY", "Stop before any native effects.", 409)

    # Exercise the real HTTP route, service and runner consumer guard. Only the
    # journal lookup/intent boundary is a fixture; no commit effects are executed.
    monkeypatch.setattr(NativeCommitJournal, "history", missing_intent)
    monkeypatch.setattr(NativeCommitJournal, "begin", stop_before_intent)
    app.dependency_overrides[actor] = lambda: RuntimeActorContext(
        actor_id="agent-engineer", stable_key="test-identity"
    )
    body = dict(
        preparation_command_id="prepared",
        inspection_approval_id="inspection",
        expected_inspection_plan_hash="1" * 64,
        command_id="commit",
        expected_plan_hash="2" * 64,
        approval_id="approval",
        worker_id="worker",
        lease_token="test-token",
    )
    previous, replacement = Consumer("previous"), Consumer("replacement")
    with TestClient(app) as client:

        def execute():
            response = client.post("/api/self-build/workspaces/workspace/native-commit", json=body)
            assert response.status_code == 409
            return response.json()["error"]["code"]

        configure(app, **configuration, native_commit_validation_consumer=previous)
        assert app.state.native_commit_validation_consumer is previous
        assert execute() == "TEST_INTENT_BOUNDARY"
        assert calls == ["previous"] and len(intents) == 1

        configure(app, **configuration, native_commit_validation_consumer=None)
        assert app.state.native_commit_validation_consumer is None
        assert execute() == "GIT_COMMIT_VALIDATION_UNAVAILABLE"
        assert calls == ["previous"] and len(intents) == 1

        configure(app, **configuration, native_commit_validation_consumer=replacement)
        assert app.state.native_commit_validation_consumer is replacement
        assert execute() == "TEST_INTENT_BOUNDARY"
        assert calls == ["previous", "replacement"] and len(intents) == 2

        # Omitting the optional consumer also explicitly disables the integration.
        configure(app, **configuration)
        assert execute() == "GIT_COMMIT_VALIDATION_UNAVAILABLE"
        assert calls == ["previous", "replacement"] and len(intents) == 2
