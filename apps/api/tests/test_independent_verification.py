"""Real worker/SQLite/RBAC path; model responses are deterministic mocks."""

import json

import pytest
from pydantic import ValidationError

from app.autonomous_worker.errors import AutonomousWorkerError
from app.autonomous_worker.verification import deterministic_checks, parse_review
from app.models.agent_runtime import AutonomousExecutionSpecification, AutonomousExecutionType
from app.models.autonomous_worker import PlanningReviewResult
from app.models.verification import CompletionCriterion
from tests.test_autonomous_worker import VALID_RESULT, FakeRouter, worker_fixture

FIELD = CompletionCriterion(
    id="recommendations",
    description="Deliver an actionable plan",
    mode="field_nonempty",
    field="recommendations",
)
SEMANTIC = CompletionCriterion(
    id="objective", description="Recommendations address the grounded objective", mode="semantic"
)


def deny_completion(app, actor_id, task_id):
    from app.models.identity import AssignPermissionRequest

    permission = next(
        item
        for item in app.state.identity_service.list_definitions("permission", 0, 100)
        if item.stable_key == "runtime.complete"
    )
    app.state.identity_service.assign_permission(
        actor_id,
        AssignPermissionRequest(
            permission_id=permission.id,
            effect="deny",
            resource_type="task",
            resource_id=task_id,
        ),
    )


@pytest.mark.parametrize(
    "field,attribute",
    [
        ("assumptions", None),
        ("recommendations", "title"),
        ("recommendations", "description"),
        ("risks", "description"),
        ("risks", "mitigation"),
    ],
)
@pytest.mark.parametrize("empty", [" ", "\t\n", "\u2003"])
def test_nonempty_collection_requires_meaningful_entries(field, attribute, empty):
    from copy import deepcopy

    body = deepcopy(VALID_RESULT)
    if attribute is None:
        body[field] = [empty]
    else:
        body[field][0][attribute] = empty
    result = PlanningReviewResult.model_validate(body)
    criterion = CompletionCriterion(
        id="deliverable",
        description="Deliver substantive entries",
        mode="field_nonempty",
        field=field,
    )
    assert deterministic_checks((criterion,), result, "result:1")[0].outcome == "needs_correction"


@pytest.mark.asyncio
async def test_cancellation_recovery_does_not_require_completion_permission(tmp_path, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    from app.db.models import TaskLeaseRow
    from app.models.agent_runtime import RequestCancellationCommand

    router = CriticRouter()
    app, client, actor_id, worker = worker_fixture(
        tmp_path, router=router, verification_criteria=(SEMANTIC,)
    )
    try:
        service = app.state.autonomous_worker_service
        complete = app.state.task_leases.complete_task

        def crash(*args, **kwargs):
            raise RuntimeError("before task completion")

        monkeypatch.setattr(app.state.task_leases, "complete_task", crash)
        with pytest.raises(RuntimeError, match="before task completion"):
            await service.run_once(worker.id)
        monkeypatch.setattr(app.state.task_leases, "complete_task", complete)
        snapshot = service.runtime.repository.load_run("run-autonomous-1")
        service.runtime.handle_authorized(
            RequestCancellationCommand(
                run_id=snapshot.specification.run_id,
                command_id="cancel-crashed-completion",
                expected_run_version=snapshot.version,
                timestamp=datetime.now(UTC),
                reason_code="operator_cancelled",
                requester_reference=actor_id,
                detail="Cancel after persisted passing verification",
            ),
            service.runtime.authenticate_actor(actor_id),
        )
        deny_completion(app, actor_id, "task-demo")
        with app.state.repository.session_factory.begin() as session:
            session.execute(
                update(TaskLeaseRow)
                .where(TaskLeaseRow.task_id == "task-demo")
                .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
        app.state.task_leases.recover_expired_leases()
        assert await service.run_once(worker.id) is None
        assert service.runtime.repository.load_run("run-autonomous-1").state.value == "cancelled"
        assert app.state.task_leases.task_status("task-demo") == "cancelled"
        assert app.state.model_execution_repository.get_by_run("run-autonomous-1").stage == "failed"
        assert len(router.requests) == len(router.critic_requests) == 1
    finally:
        client.__exit__(None, None, None)


def reviewer(outcome="passed", evidence="result:model-execution-placeholder"):
    return json.dumps(
        {
            "checks": [
                {
                    "criterionId": "objective",
                    "outcome": outcome,
                    "evidenceIds": [evidence],
                    "reason": "Grounded plan addresses the requested objective",
                }
            ]
        }
    )


class CriticRouter(FakeRouter):
    def __init__(self, *, outcome="passed", malformed=0, invented=False, callback=None):
        super().__init__([json.dumps(VALID_RESULT)])
        self.critic_requests = []
        self.outcome, self.malformed, self.invented = outcome, malformed, invented
        self.critic_callback = callback

    async def execute(self, *, request, requirements, budget, pricing=None):
        if request.output_schema and request.output_schema.name == "independent_verdict":
            self.critic_requests.append(request)
            payload = json.loads(request.messages[-1].content)
            if self.critic_callback:
                self.critic_callback()
            evidence = "invented:test:green" if self.invented else next(iter(payload["evidence"]))
            content = (
                "malformed"
                if len(self.critic_requests) <= self.malformed
                else reviewer(self.outcome, evidence)
            )
            from app.model_providers.contracts import ModelExecutionResponse

            return ModelExecutionResponse(
                content=content, provider="local-fake", model="fixture-model", latency_ms=0
            )
        return await super().execute(request=request, requirements=requirements, budget=budget)


@pytest.mark.parametrize(
    "mode,field,expected",
    [
        ("semantic", "summary", None),
        ("field_contains", "risks", "x"),
        ("field_nonempty", None, None),
    ],
)
def test_incoherent_policy_rejected(mode, field, expected):
    with pytest.raises(ValidationError):
        CompletionCriterion(
            id="criterion", description="frozen", mode=mode, field=field, expected=expected
        )


@pytest.mark.parametrize("blank", [" ", "\t", "\n", "\u2003"])
@pytest.mark.parametrize("text_field", ["expected", "description"])
def test_blank_completion_text_rejected(blank, text_field):
    policy = dict(
        id="deliverable",
        description="Required report",
        mode="field_contains",
        field="summary",
        expected="report",
    )
    policy[text_field] = blank
    with pytest.raises(ValidationError, match="must not be blank"):
        CompletionCriterion(**policy)


def test_completion_text_is_normalized_before_policy_is_frozen():
    criterion = CompletionCriterion(
        id="deliverable",
        description="  Required report\n",
        mode="field_contains",
        field="summary",
        expected="\tcomplete report  ",
    )
    assert criterion.description == "Required report"
    assert criterion.expected == "complete report"
    assert criterion.model_dump()["expected"] == "complete report"


@pytest.mark.parametrize("json_input", [False, True])
def test_planning_request_rejects_artifact_policy_before_queueing(json_input):
    criterion = CompletionCriterion(
        id="report",
        description="Deliver the report",
        mode="artifact",
        artifactId="artifact-tool-existing-0",
        expectedPath="reports/result.md",
        expectedHash="a" * 64,
    )
    policy = {
        "execution_type": AutonomousExecutionType.PLANNING_REVIEW,
        "context_assembly_id": "assembly-existing",
        "verification_criteria": (FIELD, criterion),
    }
    with pytest.raises(ValidationError, match="artifact criteria require post-tool verification"):
        if json_input:
            AutonomousExecutionSpecification.model_validate_json(
                json.dumps(
                    {
                        **policy,
                        "verification_criteria": [
                            item.model_dump() for item in policy["verification_criteria"]
                        ],
                    }
                )
            )
        else:
            AutonomousExecutionSpecification(**policy)


def test_claiming_success_does_not_satisfy_missing_deliverable():
    result = PlanningReviewResult.model_validate(
        {**VALID_RESULT, "summary": "Everything succeeded", "recommendations": []}
    )
    assert deterministic_checks((FIELD,), result, "result:1")[0].outcome == "needs_correction"


@pytest.mark.parametrize(
    "content",
    [
        "not JSON",
        '{"checks": []}',
        reviewer(evidence="nonexistent"),
        '{"checks": [{"criterionId":"objective","outcome":"passed","evidenceIds":[],"reason":"success"}]}',
    ],
)
def test_malformed_changed_criteria_or_invented_evidence_rejected(content):
    with pytest.raises((ValidationError, ValueError)):
        parse_review(content, (SEMANTIC,), {"result:1"})


@pytest.mark.asyncio
@pytest.mark.parametrize("criteria", [(FIELD,), (FIELD, SEMANTIC)])
async def test_real_worker_gates_completion_and_replays_one_verdict(tmp_path, criteria):
    router = CriticRouter()
    app, client, actor_id, worker = worker_fixture(
        tmp_path, router=router, verification_criteria=criteria
    )
    try:
        service = app.state.autonomous_worker_service
        result = await service.run_once(worker.id)
        assert result.stage == "completed"
        actor = service.runtime.authenticate_actor(actor_id)
        verdict = service.verifier.read(result, actor)
        assert verdict.outcome == "passed"
        assert verdict.resultHash == result.resultHash
        assert verdict.requestCount == (1 if SEMANTIC in criteria else 0)
        assert await service.run_once(worker.id) is None
        assert service.verifier.read(result, actor) == verdict
        records = service.runtime.repository.list_checkpoints(result.runtimeRunId)
        assert len([r for r in records if r.state_reference.endswith(":verdict")]) == 1
        response = client.get(
            f"/api/model-executions/{result.executionId}/verification",
            headers={"X-Jarvis-Actor-Id": actor_id},
        )
        assert response.status_code == 200
        assert response.json()["data"]["digest"] == verdict.digest
        denied = client.get(f"/api/model-executions/{result.executionId}/verification")
        assert denied.status_code in {401, 403}
        if SEMANTIC in criteria:
            request = router.critic_requests[0]
            assert request.correlation_id.startswith("critic:")
            assert request.messages[0].content != router.requests[0].messages[0].content
    finally:
        client.__exit__(None, None, None)


@pytest.mark.asyncio
async def test_real_local_router_and_http_adapter_transport_fixture(tmp_path):
    """Production transport/policy/runtime exercised; inference remains a fixture."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from app.model_providers.ollama import OllamaProvider
    from app.model_providers.registry import ProviderRegistry
    from app.model_providers.retry import RetryExecutor, RetryPolicy
    from app.model_providers.router import ModelRouter

    calls = []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, payload):
            encoded = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):
            self.reply({"models": [{"name": "fixture-model"}]})

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(payload)
            if payload.get("format", {}).get("title") == "ReviewerVerdict":
                evidence = next(iter(json.loads(payload["messages"][-1]["content"])["evidence"]))
                content = reviewer(evidence=evidence)
            else:
                content = json.dumps(VALID_RESULT)
            self.reply(
                {
                    "model": "fixture-model",
                    "message": {"role": "assistant", "content": content},
                    "done": True,
                    "done_reason": "stop",
                    "prompt_eval_count": 20,
                    "eval_count": 40,
                }
            )

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    app, client, actor_id, worker = worker_fixture(
        tmp_path, router=CriticRouter(), verification_criteria=(SEMANTIC,)
    )
    try:
        service = app.state.autonomous_worker_service
        service.router = ModelRouter(
            ProviderRegistry(
                [
                    OllamaProvider(
                        name="local-fake",
                        base_url=f"http://127.0.0.1:{server.server_port}",
                        default_model="fixture-model",
                        execution_mode="local_only",
                    )
                ]
            ),
            RetryExecutor(RetryPolicy(maximum_attempts=1)),
        )
        result = await service.run_once(worker.id)
        assert result.stage == "completed"
        verdict = service.verifier.read(result, service.runtime.authenticate_actor(actor_id))
        assert verdict.outcome == "passed"
        assert verdict.provider == "local-fake"
        assert len(calls) == 2
        assert calls[1]["format"]["title"] == "ReviewerVerdict"
        assert await service.run_once(worker.id) is None
        assert len(calls) == 2
    finally:
        client.__exit__(None, None, None)
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["needs_correction", "failed", "unverifiable"])
async def test_nonpass_never_completes_task(tmp_path, outcome):
    router = CriticRouter(outcome=outcome)
    app, client, actor_id, worker = worker_fixture(
        tmp_path, router=router, verification_criteria=(SEMANTIC,)
    )
    try:
        service = app.state.autonomous_worker_service
        result = await service.run_once(worker.id)
        verdict = service.verifier.read(result, service.runtime.authenticate_actor(actor_id))
        assert verdict.outcome == outcome
        assert app.state.task_leases.task_status(result.taskId) == "under_review"
        assert result.stage == "human_review_required"
    finally:
        client.__exit__(None, None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("revoked_permission", ["runtime.complete", "runtime.pause"])
@pytest.mark.parametrize("crash_boundary", ["result", "verdict", "review"])
@pytest.mark.parametrize("outcome", ["passed", "needs_correction", "failed", "unverifiable"])
async def test_escalation_recovery_uses_live_pause_permission(
    tmp_path, monkeypatch, revoked_permission, crash_boundary, outcome
):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    from app.db.models import TaskLeaseRow
    from app.models.identity import AssignPermissionRequest

    router = CriticRouter(outcome=outcome)
    app, client, actor_id, worker = worker_fixture(
        tmp_path, router=router, verification_criteria=(SEMANTIC,)
    )
    try:
        service = app.state.autonomous_worker_service
        pause = service._pause_for_review
        resolve = service._resolve_review
        verify = service.verifier.verify
        finalize = service._finalize

        def crash_before_pause(*args, **kwargs):
            raise RuntimeError("nonpassing evidence persisted before pause")

        async def crash_before_verification(*args, **kwargs):
            crash_before_pause()

        if crash_boundary == "result":
            monkeypatch.setattr(service.verifier, "verify", crash_before_verification)
        else:
            monkeypatch.setattr(
                service,
                "_resolve_review"
                if crash_boundary == "verdict"
                else "_finalize"
                if outcome == "passed"
                else "_pause_for_review",
                crash_before_pause,
            )
        with pytest.raises(RuntimeError, match="nonpassing evidence persisted before pause"):
            await service.run_once(worker.id)
        execution = app.state.model_execution_repository.get_by_run("run-autonomous-1")
        actor = service.runtime.authenticate_actor(actor_id)
        assert not execution.requiresHumanReview
        verdict = service.verifier.read(execution, actor)
        if crash_boundary == "result":
            assert verdict is None and len(router.critic_requests) == 0
        else:
            assert verdict.outcome == outcome
        decision = service._durable_review_decision(execution, actor)
        if crash_boundary != "review":
            assert decision is None
        else:
            assert decision.outcome.value == ("accepted" if outcome == "passed" else "escalated")
        permission = next(
            item
            for item in app.state.identity_service.list_definitions("permission", 0, 100)
            if item.stable_key == revoked_permission
        )
        app.state.identity_service.assign_permission(
            actor_id,
            AssignPermissionRequest(
                permission_id=permission.id,
                effect="deny",
                resource_type="task",
                resource_id=execution.taskId,
            ),
        )
        with app.state.repository.session_factory.begin() as session:
            session.execute(
                update(TaskLeaseRow)
                .where(TaskLeaseRow.task_id == execution.taskId)
                .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
        app.state.task_leases.recover_expired_leases()
        monkeypatch.setattr(service, "_pause_for_review", pause)
        monkeypatch.setattr(service, "_resolve_review", resolve)
        monkeypatch.setattr(service.verifier, "verify", verify)
        monkeypatch.setattr(service, "_finalize", finalize)
        recovered = await service.run_once(worker.id)
        snapshot = service.runtime.read_run_authorized(execution.runtimeRunId, actor)
        if outcome == "passed" and revoked_permission == "runtime.pause":
            assert recovered is not None and recovered.stage == "completed"
            assert snapshot.state.value == "succeeded"
            assert app.state.task_leases.task_status(execution.taskId) == "completed"
        elif outcome != "passed" and revoked_permission == "runtime.complete":
            assert recovered is not None and recovered.stage == "human_review_required"
            assert snapshot.state.value == "paused"
            assert app.state.task_leases.task_status(execution.taskId) == "under_review"
        else:
            assert recovered is None
            assert snapshot.state.value == "running"
            assert app.state.task_leases.task_status(execution.taskId) != "completed"
        assert len(router.requests) == len(router.critic_requests) == 1
        recovered_verdict = service.verifier.read(execution, actor)
        assert recovered_verdict.outcome == outcome
        if verdict is not None:
            assert recovered_verdict == verdict
    finally:
        client.__exit__(None, None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["deny", "suspend"])
async def test_completion_rechecks_live_authority_inside_task_transaction(
    tmp_path, monkeypatch, change
):
    from app.models.identity import AssignPermissionRequest

    router = CriticRouter()
    app, client, actor_id, worker = worker_fixture(
        tmp_path, router=router, verification_criteria=(SEMANTIC,)
    )
    try:
        service = app.state.autonomous_worker_service
        complete = app.state.task_leases.complete_task
        guarded = []

        def revoke_then_complete(task_id, *args, **kwargs):
            guarded.append(kwargs.get("completion_guard"))
            if change == "suspend":
                app.state.identity_service.transition(actor_id, "suspended")
            else:
                permission = next(
                    item
                    for item in app.state.identity_service.list_definitions("permission", 0, 100)
                    if item.stable_key == "runtime.complete"
                )
                app.state.identity_service.assign_permission(
                    actor_id,
                    AssignPermissionRequest(
                        permission_id=permission.id,
                        effect="deny",
                        resource_type="task",
                        resource_id=task_id,
                    ),
                )
            return complete(task_id, *args, **kwargs)

        monkeypatch.setattr(app.state.task_leases, "complete_task", revoke_then_complete)
        with pytest.raises(AutonomousWorkerError) as error:
            await service.run_once(worker.id)
        assert error.value.code == "EXECUTION_AUTHORIZATION_REVOKED"
        execution = app.state.model_execution_repository.get_by_run("run-autonomous-1")
        assert guarded and all(guard is not None for guard in guarded)
        assert app.state.task_leases.task_status(execution.taskId) == "in_progress"
        assert service.runtime.repository.load_run(execution.runtimeRunId).state.value == "running"
        assert execution.stage == "finalization_pending"
        assert len(router.requests) == len(router.critic_requests) == 1
    finally:
        client.__exit__(None, None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["cancel", "cancel_and_revoke", "pause", "target_suspended"])
async def test_task_completion_fences_late_native_runtime_change(tmp_path, monkeypatch, change):
    from datetime import UTC, datetime

    from app.models.agent_runtime import RequestCancellationCommand, RequestPauseCommand

    router = CriticRouter()
    app, client, actor_id, worker = worker_fixture(
        tmp_path, router=router, verification_criteria=(SEMANTIC,)
    )
    try:
        service = app.state.autonomous_worker_service
        complete = app.state.task_leases.complete_task

        def cancel_then_complete(task_id, *args, **kwargs):
            snapshot = service.runtime.repository.load_run("run-autonomous-1")
            if change == "target_suspended":
                app.state.identity_service.transition(snapshot.specification.agent_id, "suspended")
                return complete(task_id, *args, **kwargs)
            cancelling = change in {"cancel", "cancel_and_revoke"}
            command = RequestCancellationCommand if cancelling else RequestPauseCommand
            service.runtime.handle_authorized(
                command(
                    run_id=snapshot.specification.run_id,
                    command_id="cancel-at-task-completion",
                    expected_run_version=snapshot.version,
                    timestamp=datetime.now(UTC),
                    reason_code="operator_cancelled" if cancelling else "operator_pause",
                    **({"requester_reference": actor_id} if cancelling else {}),
                    detail="Cancel after completion precheck",
                ),
                service.runtime.authenticate_actor(actor_id),
            )
            if change == "cancel_and_revoke":
                deny_completion(app, actor_id, task_id)
            try:
                return complete(task_id, *args, **kwargs)
            except AutonomousWorkerError:
                # The guarded transaction rolled back before native cancellation
                # reconciliation runs outside it.
                assert app.state.task_leases.task_status(task_id) == "in_progress"
                raise

        monkeypatch.setattr(app.state.task_leases, "complete_task", cancel_then_complete)
        with pytest.raises(AutonomousWorkerError) as error:
            await service.run_once(worker.id)
        assert error.value.code == (
            "EXECUTION_CANCELLED"
            if change in {"cancel", "cancel_and_revoke"}
            else "EXECUTION_AUTHORIZATION_REVOKED"
            if change == "target_suspended"
            else "EXECUTION_COMPLETION_BLOCKED"
        )
        assert app.state.task_leases.task_status("task-demo") == (
            "cancelled" if change in {"cancel", "cancel_and_revoke"} else "in_progress"
        )
        execution = app.state.model_execution_repository.get_by_run("run-autonomous-1")
        assert execution.stage == (
            "failed" if change in {"cancel", "cancel_and_revoke"} else "finalization_pending"
        )
        assert len(router.requests) == len(router.critic_requests) == 1
    finally:
        client.__exit__(None, None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "malformed,invented,expected,count",
    [(1, False, "passed", 2), (2, False, "unverifiable", 2), (0, True, "unverifiable", 2)],
)
async def test_reviewer_repair_is_bounded_and_cannot_invent_evidence(
    tmp_path, malformed, invented, expected, count
):
    router = CriticRouter(malformed=malformed, invented=invented)
    app, client, actor_id, worker = worker_fixture(
        tmp_path, router=router, verification_criteria=(SEMANTIC,)
    )
    try:
        service = app.state.autonomous_worker_service
        result = await service.run_once(worker.id)
        verdict = service.verifier.read(result, service.runtime.authenticate_actor(actor_id))
        assert verdict.outcome == expected
        assert len(router.critic_requests) == count == verdict.requestCount
    finally:
        client.__exit__(None, None, None)


@pytest.mark.asyncio
async def test_lost_acknowledgement_recovers_without_second_reviewer_call(tmp_path, monkeypatch):
    router = CriticRouter()
    app, client, actor_id, worker = worker_fixture(
        tmp_path, router=router, verification_criteria=(SEMANTIC,)
    )
    try:
        service = app.state.autonomous_worker_service
        save = service.verifier._save

        def crash(*args, **kwargs):
            if args[5] == "response-0":
                raise RuntimeError("lost acknowledgement")
            return save(*args, **kwargs)

        monkeypatch.setattr(service.verifier, "_save", crash)
        with pytest.raises(RuntimeError, match="lost acknowledgement"):
            await service.run_once(worker.id)
        monkeypatch.setattr(service.verifier, "_save", save)
        # Expire the held lease deterministically, then use the normal recovery path.
        from datetime import UTC, datetime, timedelta

        from sqlalchemy import update

        from app.db.models import TaskLeaseRow

        with app.state.repository.session_factory.begin() as session:
            session.execute(
                update(TaskLeaseRow).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
        app.state.task_leases.recover_expired_leases()
        result = await service.run_once(worker.id)
        assert result is not None
        verdict = service.verifier.read(result, service.runtime.authenticate_actor(actor_id))
        assert verdict.outcome == "unverifiable"
        assert len(router.critic_requests) == 1
        assert verdict.checks[0].reason == "reviewer_acknowledgement_uncertain"
    finally:
        client.__exit__(None, None, None)


@pytest.mark.asyncio
async def test_persisted_response_survives_restart_without_second_call(tmp_path, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    from app.autonomous_worker.verification import IndependentVerifier
    from app.db.models import TaskLeaseRow

    router = CriticRouter()
    app, client, actor_id, worker = worker_fixture(
        tmp_path, router=router, verification_criteria=(SEMANTIC,)
    )
    try:
        service = app.state.autonomous_worker_service
        save = service.verifier._save

        def crash(*args, **kwargs):
            saved = save(*args, **kwargs)
            if args[5] == "response-0":
                raise RuntimeError("response committed before crash")
            return saved

        monkeypatch.setattr(service.verifier, "_save", crash)
        with pytest.raises(RuntimeError, match="response committed"):
            await service.run_once(worker.id)
        service.verifier = IndependentVerifier(service)
        with app.state.repository.session_factory.begin() as session:
            session.execute(
                update(TaskLeaseRow)
                .where(TaskLeaseRow.task_id == "task-demo")
                .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
        app.state.task_leases.recover_expired_leases()
        result = await service.run_once(worker.id)
        assert result.stage == "completed"
        verdict = service.verifier.read(result, service.runtime.authenticate_actor(actor_id))
        assert verdict.outcome == "passed"
        assert verdict.requestCount == 1
        assert len(router.critic_requests) == 1
        from fastapi.testclient import TestClient

        from app.main import create_app
        from tests.test_persistence import database_url

        restarted = create_app(database_url=database_url(tmp_path / "run-autonomous-1.db"))
        with TestClient(restarted) as restarted_client:
            response = restarted_client.get(
                f"/api/model-executions/{result.executionId}/verification",
                headers={"X-Jarvis-Actor-Id": actor_id},
            )
            assert response.status_code == 200
            assert response.json()["data"]["digest"] == verdict.digest
    finally:
        client.__exit__(None, None, None)


@pytest.mark.asyncio
async def test_concurrent_verifier_under_same_lease_cannot_dispatch_twice(tmp_path, monkeypatch):
    from app.db.models import TaskLeaseRow

    router = CriticRouter()
    app, client, actor_id, worker = worker_fixture(
        tmp_path, router=router, verification_criteria=(SEMANTIC,)
    )
    try:
        service = app.state.autonomous_worker_service
        original = router.execute
        rejected = []

        async def interleave(*, request, **kwargs):
            if request.output_schema and request.output_schema.name == "independent_verdict":
                execution = service.executions.get_by_run("run-autonomous-1")
                snapshot = service.runtime.repository.load_run("run-autonomous-1")
                with app.state.repository.session_factory() as session:
                    lease = session.get(TaskLeaseRow, "task-demo")
                    lease_token = lease.lease_token
                with pytest.raises(AutonomousWorkerError) as error:
                    await service.verifier.verify(
                        snapshot,
                        execution,
                        service.runtime.authenticate_actor(actor_id),
                        worker.id,
                        lease_token,
                    )
                assert error.value.code == "VERIFICATION_IN_PROGRESS"
                rejected.append(True)
            return await original(request=request, **kwargs)

        monkeypatch.setattr(router, "execute", interleave)
        result = await service.run_once(worker.id)
        assert result.stage == "completed"
        assert rejected == [True]
        assert len(router.critic_requests) == 1
    finally:
        client.__exit__(None, None, None)


@pytest.mark.asyncio
async def test_provenance_mismatch_fails_closed(tmp_path):
    router = CriticRouter()
    app, client, actor_id, worker = worker_fixture(
        tmp_path, router=router, verification_criteria=(FIELD,)
    )
    try:
        service = app.state.autonomous_worker_service
        result = await service.run_once(worker.id)
        with pytest.raises(AutonomousWorkerError) as error:
            service.verifier.read(
                result.model_copy(update={"resultHash": "0" * 64}),
                service.runtime.authenticate_actor(actor_id),
            )
        assert error.value.code == "VERIFICATION_PROVENANCE_MISMATCH"
    finally:
        client.__exit__(None, None, None)


def test_real_workspace_artifact_hash_scope_and_provenance(tmp_path):
    import asyncio

    from tests.test_tool_execution import authorize, prepared

    with prepared(tmp_path) as (app, client, actor_id, worker, workspace, source, body):
        authorize(client, actor_id, body)
        result = asyncio.run(app.state.autonomous_worker_service.run_once(worker.id))
        artifact = result.artifacts[0]
        criterion = CompletionCriterion(
            id="report",
            description="Expected report is durably delivered",
            mode="artifact",
            artifactId=artifact.artifactId,
            expectedPath=artifact.relativePath,
            expectedHash=artifact.contentHash,
        )
        service = app.state.autonomous_worker_service
        actor = service.runtime.authenticate_actor(actor_id)
        assert service.verifier.artifact_check(criterion, source, actor).outcome == "passed"
        for invalid in (
            criterion.model_copy(update={"expectedHash": "0" * 64}),
            criterion.model_copy(update={"expectedPath": "reports/other.md"}),
            criterion.model_copy(update={"artifactId": "artifact-tool-" + "0" * 32 + "-0"}),
        ):
            assert service.verifier.artifact_check(invalid, source, actor).outcome == "failed"
        assert (
            service.verifier.artifact_check(
                criterion, source.model_copy(update={"taskId": "foreign-task"}), actor
            ).outcome
            == "failed"
        )
        assert (workspace / artifact.relativePath).exists()


@pytest.mark.asyncio
async def test_test_claim_is_unverifiable_without_command_journal(tmp_path):
    criterion = CompletionCriterion(id="tests", description="Tests passed", mode="test_evidence")
    router = CriticRouter()
    app, client, actor_id, worker = worker_fixture(
        tmp_path, router=router, verification_criteria=(criterion,)
    )
    try:
        service = app.state.autonomous_worker_service
        result = await service.run_once(worker.id)
        verdict = service.verifier.read(result, service.runtime.authenticate_actor(actor_id))
        assert verdict.outcome == "unverifiable"
        assert verdict.requestCount == 0
        assert app.state.task_leases.task_status(result.taskId) == "under_review"
    finally:
        client.__exit__(None, None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["cancel", "stop", "lease", "revoke"])
async def test_critic_cannot_commit_after_safety_boundary_changes(tmp_path, boundary):
    from datetime import UTC, datetime

    from sqlalchemy import update

    from app.autonomous_worker.__main__ import _run_once_resilient
    from app.db.models import TaskLeaseRow
    from app.models.agent_runtime import RequestCancellationCommand

    router = CriticRouter()
    app, client, actor_id, worker = worker_fixture(
        tmp_path, router=router, verification_criteria=(SEMANTIC,)
    )
    service = app.state.autonomous_worker_service

    def change():
        if boundary == "stop":
            app.state.repository.emergency_stop = True
            app.state.repository.persist()
        elif boundary == "lease":
            with app.state.repository.session_factory.begin() as session:
                session.execute(
                    update(TaskLeaseRow)
                    .where(TaskLeaseRow.task_id == "task-demo")
                    .values(lease_token="rotated")
                )
        elif boundary == "revoke":
            app.state.identity_service.transition(actor_id, "suspended")
        else:
            snapshot = service.runtime.repository.load_run("run-autonomous-1")
            service.runtime.handle_authorized(
                RequestCancellationCommand(
                    run_id=snapshot.specification.run_id,
                    command_id="cancel-critic",
                    expected_run_version=snapshot.version,
                    timestamp=datetime.now(UTC),
                    reason_code="operator_cancelled",
                    requester_reference=actor_id,
                    detail="Cancel during independent review",
                ),
                service.runtime.authenticate_actor(actor_id),
            )

    router.critic_callback = change
    try:
        await _run_once_resilient(service, worker.id)
        snapshot = service.runtime.repository.load_run("run-autonomous-1")
        assert snapshot.state != "succeeded"
        records = service.runtime.repository.list_checkpoints(snapshot.specification.run_id)
        assert not any(r.state_reference.endswith(":verdict") for r in records)
        assert app.state.task_leases.task_status("task-demo") != "completed"
        assert len(router.critic_requests) == 1
    finally:
        client.__exit__(None, None, None)
