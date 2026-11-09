"""Explicit Home authority and native admission; no real provider calls."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db.models import (
    AgentPermissionAssignmentRow,
    AgentRuntimeRunRow,
    ContextAssemblyRow,
    ModelExecutionRow,
    TaskRow,
)
from app.main import create_app


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_AUTONOMOUS_WORKER_ENABLED", "false")
    monkeypatch.setenv("JARVIS_MODEL_EXECUTION_MODE", "disabled")
    app = create_app(database_url=f"sqlite:///{(tmp_path / 'home-planning.db').as_posix()}")
    with TestClient(app) as client:
        yield app, client, {"Origin": app.state.settings.web_origin}


def setup_operator(client, origin):
    response = client.post(
        "/api/goal-planning/operator/setup",
        headers=origin,
        json={"confirmation": "configure_bounded_goal_planning"},
    )
    assert response.status_code == 200, response.text
    return {**origin, "X-Jarvis-Actor-Id": response.json()["data"]["actorId"]}


def ready_native_fixture(app):
    from app.autonomous_worker.provisioning import configure_task_actor
    from app.model_providers.contracts import ModelCapability
    from app.model_providers.ollama import OllamaProvider

    actor = configure_task_actor(app, "task-demo", "home-fixture-worker")
    app.state.settings.autonomous_worker_actor_id = actor
    app.state.settings.autonomous_worker_enabled = True
    app.state.settings.model_execution_mode = "local_only"
    provider = OllamaProvider(
        name="fixture",
        default_model="fixture-model",
        base_url="http://127.0.0.1:9",
        execution_mode="local_only",
        capabilities=frozenset({ModelCapability.CHAT, ModelCapability.STRUCTURED_OUTPUT}),
    )
    app.state.model_router.registry._providers = {provider.name: provider}
    return actor


def allow_planning(client, headers, *, maximum_goals=2):
    response = client.post(
        "/api/goal-planning/policy",
        headers={**headers, "Idempotency-Key": "configure-policy"},
        json={
            "confirmation": "allow_bounded_goal_planning",
            "expectedRevision": 0,
            "maximumGoals": maximum_goals,
            "expiresAt": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            "profile": "direct-v1",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_default_policy_and_navigation_never_admit_work(local):
    app, client, origin = local
    policy = client.get("/api/goal-planning/policy", headers=origin)
    assert policy.status_code == 200
    assert policy.json()["data"]["enabled"] is False
    for _ in range(2):
        assert client.get("/api/goal-planning/policy", headers=origin).status_code == 200
        assert client.get("/api/goals").status_code == 200
    response = client.post(
        "/api/goals",
        headers={**origin, "Idempotency-Key": "draft-only"},
        json={"request": "Prepare a bounded quality-control checklist."},
    )
    assert response.status_code == 201
    assert "planning" not in response.json()["data"]
    with app.state.repository.session_factory() as session:
        for row_type in (AgentRuntimeRunRow, ContextAssemblyRow, ModelExecutionRow):
            assert session.scalar(select(func.count()).select_from(row_type)) == 0


def test_setup_and_policy_do_not_activate_execution_or_accept_forged_scope(local):
    app, client, origin = local
    headers = setup_operator(client, origin)
    response = client.post(
        "/api/goal-planning/policy",
        headers={**headers, "Idempotency-Key": "configure-policy"},
        json={
            "confirmation": "allow_bounded_goal_planning",
            "expectedRevision": 0,
            "maximumGoals": 2,
            "expiresAt": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            "profile": "direct-v1",
        },
    )
    assert response.status_code == 409
    assert app.state.settings.autonomous_worker_enabled is False
    assert app.state.settings.model_execution_mode == "disabled"
    response = client.post(
        "/api/goals",
        headers={**headers, "Idempotency-Key": "forged-policy"},
        json={
            "request": "Prepare a checklist.",
            "planningConsent": {"policyId": "invented", "policyHash": "0" * 64},
        },
    )
    assert response.status_code == 409


def test_captured_consent_creates_one_bounded_native_run_and_timed_task_grants(local):
    app, client, origin = local
    worker = ready_native_fixture(app)
    headers = setup_operator(client, origin)
    policy = allow_planning(client, headers)
    body = {
        "request": "Prepare a Level 1 trapeze prefab QC checklist, explicitly flagging missing project schedules.",
        "planningConsent": {"policyId": policy["id"], "policyHash": policy["policyHash"]},
    }
    response = client.post(
        "/api/goals", headers={**headers, "Idempotency-Key": "planned-goal"}, json=body
    )
    assert response.status_code == 201, response.text
    goal = response.json()["data"]
    assert goal["planning"]["policyHash"] == policy["policyHash"]
    assert (
        client.post(
            "/api/goals", headers={**headers, "Idempotency-Key": "planned-goal"}, json=body
        ).json()["data"]
        == goal
    )
    with app.state.repository.session_factory() as session:
        runs = session.scalars(
            select(AgentRuntimeRunRow).where(AgentRuntimeRunRow.task_id == goal["taskId"])
        ).all()
        assert len(runs) == 1 and runs[0].state == "queued"
        assert runs[0].run_id == goal["planning"]["runId"]
        assert session.scalar(select(func.count()).select_from(ModelExecutionRow)) == 0
        grants = session.scalars(
            select(AgentPermissionAssignmentRow).where(
                AgentPermissionAssignmentRow.agent_id == worker,
                AgentPermissionAssignmentRow.resource_id == goal["taskId"],
            )
        ).all()
        assert grants and all(row.expires_at is not None for row in grants)
        assembly = session.scalar(
            select(ContextAssemblyRow).where(ContextAssemblyRow.task_id == goal["taskId"])
        )
        assert assembly.payload["executionBudget"]["maximumRequests"] == 1
    assert (
        client.get("/api/goal-planning/policy", headers=origin).json()["data"]["admittedGoals"] == 1
    )


def test_stale_or_exhausted_consent_rolls_back_goal_task_and_grants(local):
    app, client, origin = local
    ready_native_fixture(app)
    headers = setup_operator(client, origin)
    policy = allow_planning(client, headers, maximum_goals=1)

    def submit(key, digest):
        return client.post(
            "/api/goals",
            headers={**headers, "Idempotency-Key": key},
            json={
                "request": "Prepare a bounded checklist.",
                "planningConsent": {"policyId": policy["id"], "policyHash": digest},
            },
        )

    assert submit("stale", "0" * 64).status_code == 409
    assert submit("first", policy["policyHash"]).status_code == 201
    with app.state.repository.session_factory() as session:
        count = session.scalar(select(func.count()).select_from(TaskRow))
    assert submit("exhausted", policy["policyHash"]).status_code == 409
    with app.state.repository.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(TaskRow)) == count


def captured_goal(app, client, origin):
    worker = ready_native_fixture(app)
    headers = setup_operator(client, origin)
    policy = allow_planning(client, headers)
    body = {
        "request": "Prepare a bounded QC checklist.",
        "planningConsent": {"policyId": policy["id"], "policyHash": policy["policyHash"]},
    }
    response = client.post(
        "/api/goals", headers={**headers, "Idempotency-Key": "captured"}, json=body
    )
    assert response.status_code == 201, response.text
    return worker, headers, policy, response.json()["data"], body


def test_revocation_blocks_dispatch_and_forged_alternative_run_without_metadata(local):
    from app.core.errors import DomainError
    from app.goal_planning.guards import require_goal_consent
    from app.models.agent_runtime import CreateAgentRunCommand
    from tests.agent_runtime_testkit import make_spec

    app, client, origin = local
    worker, headers, policy, goal, body = captured_goal(app, client, origin)
    response = client.post(
        f"/api/goal-planning/policy/{policy['id']}/revoke",
        headers=headers,
        json={"expectedPolicyHash": policy["policyHash"]},
    )
    assert response.status_code == 200 and response.json()["data"]["enabled"] is False
    with (
        app.state.repository.session_factory() as session,
        pytest.raises(DomainError, match="consent"),
    ):
        require_goal_consent(
            session, goal["taskId"], run_id=goal["planning"]["runId"], actor_id=worker
        )
    spec = make_spec(run_id="forged-extra-run", task_id=goal["taskId"], agent_id=worker)
    with pytest.raises(DomainError):
        app.state.agent_runtime_service.create_run(
            CreateAgentRunCommand(
                specification=spec,
                command_id="forged",
                timestamp=spec.created_at,
                actor_reference=worker,
            )
        )
    # An already accepted creation receipt remains replayable, without new admission.
    assert (
        client.post(
            "/api/goals", headers={**headers, "Idempotency-Key": "captured"}, json=body
        ).json()["data"]
        == goal
    )
    with app.state.repository.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ModelExecutionRow)) == 0


def test_failed_capture_rolls_back_context_permissions_and_authority_slot(local, monkeypatch):
    app, client, origin = local
    ready_native_fixture(app)
    headers = setup_operator(client, origin)
    policy = allow_planning(client, headers)
    original = app.state.identity_service.assign_permission_in_session
    calls = []

    def interrupted(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(result)
        if len(calls) == 2:
            raise RuntimeError("fixture capture interruption")
        return result

    monkeypatch.setattr(app.state.identity_service, "assign_permission_in_session", interrupted)
    with app.state.repository.session_factory() as session:
        tables = (TaskRow, AgentPermissionAssignmentRow, ContextAssemblyRow)
        before = {
            table: session.scalar(select(func.count()).select_from(table)) for table in tables
        }
    with pytest.raises(RuntimeError, match="capture interruption"):
        client.post(
            "/api/goals",
            headers={**headers, "Idempotency-Key": "interrupted"},
            json={
                "request": "Prepare a bounded checklist.",
                "planningConsent": {"policyId": policy["id"], "policyHash": policy["policyHash"]},
            },
        )
    with app.state.repository.session_factory() as session:
        assert {
            table: session.scalar(select(func.count()).select_from(table)) for table in before
        } == before
    assert (
        client.get("/api/goal-planning/policy", headers=origin).json()["data"]["admittedGoals"] == 0
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("model_requests_review", [True, False])
async def test_native_worker_uses_one_fixture_request_for_captured_goal(
    local, model_requests_review
):
    import json

    import httpx

    from tests.test_autonomous_worker import VALID_RESULT

    app, client, origin = local
    worker_actor, headers, policy, goal, _ = captured_goal(app, client, origin)
    calls = []

    def transport(request):
        if request.method == "GET":
            return httpx.Response(
                200, json={"models": [{"name": "fixture-model"}], "version": "fixture"}
            )
        calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "fixture-model",
                "message": {
                    "role": "assistant",
                    "content": json.dumps(
                        VALID_RESULT | {"requiresHumanReview": model_requests_review}
                    ),
                },
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 20,
                "eval_count": 40,
            },
        )

    provider = app.state.model_router.registry.list()[0]
    provider.execution_mode = "local_only"
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:9", transport=httpx.MockTransport(transport)
    ) as fake_http:
        provider._client = fake_http
        worker = app.state.task_leases.register_worker("home-fixture", "home-fixture", 60)
        result = await app.state.autonomous_worker_service.run_once(
            worker.id, task_id=goal["taskId"]
        )
    assert result.stage == "human_review_required", result
    assert result.requiresHumanReview is model_requests_review
    assert len(calls) == 1 and calls[0]["options"]["num_predict"] == 1536
    assert result.targetAgentId == worker_actor
    assert client.get(f"/api/tasks/{goal['taskId']}").json()["data"]["status"] == "under_review"
    assert (
        client.get(f"/api/results/tasks/{goal['taskId']}/executions", headers=headers).status_code
        == 403
    )
    reviewer = client.post(
        "/api/results/operator/setup",
        headers=origin,
        json={"confirmation": "enable_local_result_review"},
    ).json()["data"]["actorId"]
    review_headers = {**origin, "X-Jarvis-Actor-Id": reviewer}
    output = client.get(f"/api/results/tasks/{goal['taskId']}/executions", headers=review_headers)
    assert (
        output.status_code == 200
        and output.json()["data"][0]["result"]["requiresHumanReview"] is model_requests_review
    )
    from tests.test_result_review import decision_body

    review_item = client.get("/api/results/reviews", headers=review_headers).json()["data"][0]
    receipt = client.post(
        f"/api/results/executions/{result.executionId}/review",
        headers=review_headers,
        json=decision_body(review_item, reviewer),
    )
    assert receipt.status_code == 200, receipt.text
    artifact = client.get(
        f"/api/results/artifacts/{receipt.json()['data']['artifactId']}/content",
        headers=review_headers,
    )
    assert (
        artifact.status_code == 200
        and artifact.json()["requiresHumanReview"] is model_requests_review
    )
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("consent_change", ["revoke", "renew"])
async def test_inflight_consent_refusal_keeps_worker_polling(local, consent_change):
    import json

    import httpx

    from app.autonomous_worker.__main__ import _run_once_resilient
    from tests.test_autonomous_worker import VALID_RESULT

    app, client, origin = local
    _, headers, policy, goal, _ = captured_goal(app, client, origin)
    calls = []
    renewed = None

    def renew():
        response = client.post(
            "/api/goal-planning/policy",
            headers={**headers, "Idempotency-Key": "renew-policy"},
            json={
                "confirmation": "allow_bounded_goal_planning",
                "expectedRevision": policy["revision"],
                "maximumGoals": 2,
                "expiresAt": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                "profile": "direct-v1",
            },
        )
        assert response.status_code == 200, response.text
        return response.json()["data"]

    def transport(request):
        nonlocal renewed
        if request.method == "GET":
            return httpx.Response(
                200, json={"models": [{"name": "fixture-model"}], "version": "fixture"}
            )
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            # Change persisted operator authority after dispatch, before the
            # provider response reaches the fenced result write.
            if consent_change == "revoke":
                response = client.post(
                    f"/api/goal-planning/policy/{policy['id']}/revoke",
                    headers=headers,
                    json={"expectedPolicyHash": policy["policyHash"]},
                )
                assert response.status_code == 200, response.text
            else:
                renewed = renew()
        return httpx.Response(
            200,
            json={
                "model": "fixture-model",
                "message": {"role": "assistant", "content": json.dumps(VALID_RESULT)},
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 20,
                "eval_count": 40,
            },
        )

    provider = app.state.model_router.registry.list()[0]
    service = app.state.autonomous_worker_service
    worker = app.state.task_leases.register_worker("consent-fixture", "consent-fixture", 60)
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:9", transport=httpx.MockTransport(transport)
    ) as fake_http:
        provider._client = fake_http
        assert await _run_once_resilient(service, worker.id) is None
        assert len(calls) == 1
        with app.state.repository.session_factory() as session:
            execution = session.scalar(
                select(ModelExecutionRow).where(ModelExecutionRow.task_id == goal["taskId"])
            )
            assert execution is not None and execution.result_json is None
        if renewed is None:
            renewed = renew()
        response = client.post(
            "/api/goals",
            headers={**headers, "Idempotency-Key": "fresh-consent-goal"},
            json={
                "request": "Prepare another bounded QC checklist.",
                "planningConsent": {
                    "policyId": renewed["id"],
                    "policyHash": renewed["policyHash"],
                },
            },
        )
        assert response.status_code == 201, response.text
        next_goal = response.json()["data"]
        result = await _run_once_resilient(service, worker.id)
        assert result.taskId == next_goal["taskId"]
        assert result.stage == "human_review_required"
        assert len(calls) == 2


def test_changed_configured_worker_invalidates_capture_and_native_dispatch(local):
    from app.autonomous_worker.provisioning import configure_task_actor
    from app.core.errors import DomainError
    from app.goal_planning.guards import require_goal_consent

    app, client, origin = local
    _, headers, policy, goal, body = captured_goal(app, client, origin)
    different = configure_task_actor(app, "task-demo", "different-configured-worker")
    app.state.settings.autonomous_worker_actor_id = different
    assert (
        client.get("/api/goal-planning/policy", headers=origin).json()["data"]["unavailableReason"]
        == "worker_configuration_changed"
    )
    response = client.post(
        "/api/goals", headers={**headers, "Idempotency-Key": "changed-worker"}, json=body
    )
    assert response.status_code == 409
    with app.state.repository.session_factory() as session, pytest.raises(DomainError):
        require_goal_consent(
            session, goal["taskId"], run_id=goal["planning"]["runId"], settings=app.state.settings
        )
    worker = app.state.task_leases.register_worker("different-fixture", "different-fixture", 60)
    assert app.state.task_leases.acquire_task(worker.id, task_id=goal["taskId"]) is None


def test_legacy_context_route_cannot_expand_captured_goal_into_organizational_calls(
    local, monkeypatch
):
    from app.team_selection.service import TeamSelectionService
    from tests.test_context_integration import context_body

    app, client, origin = local
    _, headers, _, goal, _ = captured_goal(app, client, origin)

    async def forbidden(*args, **kwargs):
        pytest.fail("Captured direct goal entered an unapproved team-selection stage")

    monkeypatch.setattr(TeamSelectionService, "assign_team", forbidden)
    response = client.post(
        "/api/context/assemblies",
        headers={**headers, "Idempotency-Key": "expanded-context"},
        json=context_body(task_id=goal["taskId"]),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "GOAL_PLANNING_SCOPE_MISMATCH"


def test_last_consent_slot_is_atomic_across_two_api_instances(local):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from app.goal_planning.repository import GoalPlanningAdmissionRow

    app, client, origin = local
    ready_native_fixture(app)
    headers = setup_operator(client, origin)
    policy = allow_planning(client, headers, maximum_goals=1)
    second_app = create_app(database_url=app.state.settings.database_url)
    barrier = Barrier(2)
    with TestClient(second_app) as second:
        ready_native_fixture(second_app)

        def submit(api, key):
            barrier.wait(timeout=10)
            return api.post(
                "/api/goals",
                headers={**headers, "Idempotency-Key": key},
                json={
                    "request": "Prepare a bounded checklist.",
                    "planningConsent": {
                        "policyId": policy["id"],
                        "policyHash": policy["policyHash"],
                    },
                },
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            attempts = [
                executor.submit(submit, client, "slot-one"),
                executor.submit(submit, second, "slot-two"),
            ]
            responses = [future.result(timeout=30) for future in attempts]
    assert sorted(response.status_code for response in responses) == [201, 409]
    with app.state.repository.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(GoalPlanningAdmissionRow)) == 1
        assert session.scalar(select(func.count()).select_from(ContextAssemblyRow)) == 1
        assert session.scalar(select(func.count()).select_from(AgentRuntimeRunRow)) == 1


def test_native_worker_denial_rolls_back_the_entire_new_goal_scope(local):
    from app.autonomous_worker.provisioning import _find_permission
    from app.models.identity import AssignPermissionRequest

    app, client, origin = local
    worker = ready_native_fixture(app)
    headers = setup_operator(client, origin)
    policy = allow_planning(client, headers)
    permission = _find_permission(app.state.identity_service, "runtime.execute")
    app.state.identity_service.assign_permission(
        worker, AssignPermissionRequest(permission_id=permission.id, effect="deny")
    )
    with app.state.repository.session_factory() as session:
        before = session.scalar(select(func.count()).select_from(TaskRow))
    response = client.post(
        "/api/goals",
        headers={**headers, "Idempotency-Key": "denied-goal"},
        json={
            "request": "Prepare a bounded checklist.",
            "planningConsent": {"policyId": policy["id"], "policyHash": policy["policyHash"]},
        },
    )
    assert response.status_code == 403
    with app.state.repository.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(TaskRow)) == before
        assert session.scalar(select(func.count()).select_from(ContextAssemblyRow)) == 0
    assert (
        client.get("/api/goal-planning/policy", headers=origin).json()["data"]["admittedGoals"] == 0
    )


def test_expired_consent_blocks_new_goal_and_old_queued_goal_leasing(local, monkeypatch):
    import app.goal_planning.guards as guards
    import app.goal_planning.service as planning_service
    import app.repositories.task_leases as leases

    app, client, origin = local
    _, headers, _, goal, body = captured_goal(app, client, origin)

    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(hours=2)

    for module in (guards, planning_service, leases):
        monkeypatch.setattr(module, "datetime", Later)
    assert (
        client.get("/api/goal-planning/policy", headers=origin).json()["data"]["enabled"] is False
    )
    assert (
        client.post(
            "/api/goals", headers={**headers, "Idempotency-Key": "expired-goal"}, json=body
        ).status_code
        == 409
    )
    worker = app.state.task_leases.register_worker("late-fixture", "late-fixture", 60)
    assert app.state.task_leases.acquire_task(worker.id, task_id=goal["taskId"]) is None
    with app.state.repository.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ModelExecutionRow)) == 0


def test_post_commit_native_refusal_preserves_receipt_and_retry_uses_same_scope(local, monkeypatch):
    from app.agent_runtime.errors import RuntimePermissionDeniedError

    app, client, origin = local
    ready_native_fixture(app)
    headers = setup_operator(client, origin)
    policy = allow_planning(client, headers)
    runtime = app.state.agent_runtime_service
    original = runtime.handle_authorized

    def refuse(*args, **kwargs):
        raise RuntimePermissionDeniedError("Fixture authority changed before runtime admission")

    monkeypatch.setattr(runtime, "handle_authorized", refuse)
    body = {
        "request": "Prepare a bounded checklist.",
        "planningConsent": {"policyId": policy["id"], "policyHash": policy["policyHash"]},
    }
    submit_headers = {**headers, "Idempotency-Key": "refused-after-save"}
    first = client.post("/api/goals", headers=submit_headers, json=body)
    assert first.status_code == 201, first.text
    goal = first.json()["data"]
    view = client.get(f"/api/goal-planning/goals/{goal['id']}", headers=headers).json()["data"]
    assert view["state"] == "blocked" and view["errorCode"] == RuntimePermissionDeniedError.code
    monkeypatch.setattr(runtime, "handle_authorized", original)
    replay = client.post("/api/goals", headers=submit_headers, json=body)
    assert replay.status_code == 201 and replay.json()["data"] == goal
    view = client.get(f"/api/goal-planning/goals/{goal['id']}", headers=headers).json()["data"]
    assert view["state"] == "queued" and view["errorCode"] is None
    assert (
        client.get("/api/goal-planning/policy", headers=origin).json()["data"]["admittedGoals"] == 1
    )


def test_persisted_context_cannot_widen_the_captured_goal_budget(local):
    from app.core.errors import DomainError
    from app.goal_planning.guards import require_goal_consent

    app, client, origin = local
    _, _, _, goal, _ = captured_goal(app, client, origin)
    with app.state.repository.session_factory.begin() as session:
        row = session.scalar(
            select(ContextAssemblyRow).where(ContextAssemblyRow.task_id == goal["taskId"])
        )
        row.payload = {
            **row.payload,
            "executionBudget": {**row.payload["executionBudget"], "maximumTokens": 15000},
        }
    with app.state.repository.session_factory() as session, pytest.raises(DomainError):
        require_goal_consent(session, goal["taskId"], settings=app.state.settings)


def test_populated_consent_refuses_downgrade_and_preserves_goal(local):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    app, client, origin = local
    _, headers, policy, goal, _ = captured_goal(app, client, origin)
    root = Path(__file__).parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    with app.state.repository.session_factory() as session:
        config.set_main_option("sqlalchemy.url", str(session.get_bind().url))
    with pytest.raises(RuntimeError, match="Preserve captured goal-planning authority"):
        command.downgrade(config, "20261106_results")
    with app.state.repository.session_factory() as session:
        assert session.execute(text("PRAGMA foreign_key_check")).all() == []
    assert client.get(f"/api/goals/{goal['id']}").json()["data"] == goal
    assert (
        client.get("/api/goal-planning/policy", headers=headers).json()["data"]["id"]
        == policy["id"]
    )
