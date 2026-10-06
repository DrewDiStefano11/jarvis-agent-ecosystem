"""Opt-in correction through native worker/runtime/checkpoints with fixture inference."""

import asyncio
import json
from copy import deepcopy

import pytest

from app.autonomous_worker.errors import AutonomousWorkerError
from app.models.correction import PlanningCorrectionPolicy
from tests.test_autonomous_worker import VALID_RESULT, FakeRouter, worker_fixture
from tests.test_independent_verification import FIELD, deny_completion
from tests.test_planning_review_orchestration import execution_rows, review_records


def test_policy_requires_criteria_and_bounded_frozen_deadline():
    from datetime import UTC, datetime, timedelta

    from pydantic import ValidationError

    from app.models.agent_runtime import AgentRunSpecification, AutonomousExecutionSpecification
    from tests.agent_runtime_testkit import make_spec

    with pytest.raises(ValidationError):
        AutonomousExecutionSpecification(
            execution_type="planning_review",
            context_assembly_id="assembly-1",
            correction_policy=PlanningCorrectionPolicy(),
        )
    request = AutonomousExecutionSpecification(
        execution_type="planning_review",
        context_assembly_id="assembly-1",
        verification_criteria=(FIELD,),
        correction_policy=PlanningCorrectionPolicy(),
    )
    now = datetime.now(UTC)
    specification = make_spec().model_copy(
        update={"created_at": now, "autonomous_execution": request}
    )
    for deadline in (None, now + timedelta(seconds=601)):
        with pytest.raises(ValidationError):
            AgentRunSpecification.model_validate_json(
                specification.model_copy(update={"deadline": deadline}).model_dump_json()
            )
    assert (
        AgentRunSpecification.model_validate_json(
            specification.model_copy(
                update={"deadline": now + timedelta(seconds=600)}
            ).model_dump_json()
        ).autonomous_execution.correction_policy
        == request.correction_policy
    )


@pytest.mark.asyncio
async def test_elapsed_policy_blocks_model_access_after_deadline(tmp_path, monkeypatch):
    from datetime import datetime, timedelta

    import app.autonomous_worker.service as worker_service

    router = FakeRouter([json.dumps(VALID_RESULT)])
    app, client, _, worker = worker_fixture(
        tmp_path,
        router=router,
        verification_criteria=(FIELD,),
        correction_policy=PlanningCorrectionPolicy(),
    )
    try:
        deadline = app.state.agent_runtime_service.repository.load_run(
            "run-autonomous-1"
        ).specification.deadline

        class AfterDeadline(datetime):
            @classmethod
            def now(cls, tz=None):
                return (deadline + timedelta(seconds=1)).astimezone(tz)

        monkeypatch.setattr(worker_service, "datetime", AfterDeadline)
        with pytest.raises(AutonomousWorkerError) as timeout:
            await app.state.autonomous_worker_service.run_once(worker.id)
        assert timeout.value.code == "MODEL_EXECUTION_TIMEOUT"
        assert router.requests == []
        assert app.state.task_leases.task_status("task-demo") != "completed"
    finally:
        client.__exit__(None, None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("protected", ["unverifiable", "human_review"])
async def test_policy_cannot_correct_unverifiable_or_operator_review_results(tmp_path, protected):
    from app.models.verification import CompletionCriterion

    result = {**VALID_RESULT, "requiresHumanReview": protected == "human_review"}
    criterion = (
        FIELD
        if protected == "human_review"
        else CompletionCriterion(
            id="test-proof", description="Require recorded test execution", mode="test_evidence"
        )
    )
    router = FakeRouter([json.dumps(result)])
    app, client, _, worker = worker_fixture(
        tmp_path,
        router=router,
        verification_criteria=(criterion,),
        correction_policy=PlanningCorrectionPolicy(),
    )
    try:
        service = app.state.autonomous_worker_service
        assert (await service.run_once(worker.id)).stage == "human_review_required"
        assert await service.run_once(worker.id) is None
        assert len(router.requests) == 1
        assert app.state.task_leases.task_status("task-demo") == "under_review"
    finally:
        client.__exit__(None, None, None)


@pytest.mark.asyncio
async def test_structural_correction_binds_passing_verdict_and_frozen_policy(tmp_path):
    from app.models.verification import CompletionCriterion

    initial = {**VALID_RESULT, "recommendations": []}
    criterion = CompletionCriterion(
        id="summary",
        description="Provide substantive summary",
        mode="field_nonempty",
        field="summary",
    )
    router = FakeRouter([json.dumps(initial), json.dumps(VALID_RESULT)])
    app, client, actor_id, worker = worker_fixture(
        tmp_path,
        router=router,
        verification_criteria=(criterion,),
        correction_policy=PlanningCorrectionPolicy(),
    )
    try:
        service = app.state.autonomous_worker_service
        first = await service.run_once(worker.id)
        assert first.failureCode == "review_revision_requested"
        record = review_records(app, "run-autonomous-1")[0]
        actor = service.runtime.authenticate_actor(actor_id)
        verdict = service.verifier.read(first, actor)
        assert verdict.outcome == "passed"
        assert record.metadata["verificationDigest"] == verdict.digest
        assert record.metadata["correctionPolicyDigest"]
        assert (await service.run_once(worker.id)).stage == "completed"
        assert len(router.requests) == 2
    finally:
        client.__exit__(None, None, None)


@pytest.mark.asyncio
async def test_correction_exhaustion_pauses_without_third_dispatch(tmp_path):
    initial = deepcopy(VALID_RESULT)
    initial["recommendations"][0]["description"] = " "
    router = FakeRouter([json.dumps(initial)] * 2)
    app, client, _, worker = worker_fixture(
        tmp_path,
        router=router,
        verification_criteria=(FIELD,),
        correction_policy=PlanningCorrectionPolicy(),
    )
    try:
        service = app.state.autonomous_worker_service
        assert (await service.run_once(worker.id)).failureCode == "review_revision_requested"
        assert (await service.run_once(worker.id)).stage == "human_review_required"
        assert (
            app.state.agent_runtime_service.repository.load_run("run-autonomous-1").state
            == "paused"
        )
        assert await service.run_once(worker.id) is None
        assert len(router.requests) == 2
        assert app.state.task_leases.task_status("task-demo") == "under_review"
        assert [
            record.metadata["outcome"] for record in review_records(app, "run-autonomous-1")
        ] == ["revision_requested", "escalated"]
    finally:
        client.__exit__(None, None, None)


@pytest.mark.asyncio
async def test_correction_review_lost_ack_reuses_verdict_and_result(tmp_path, monkeypatch):
    from tests.test_planning_review_orchestration import expire_lease

    initial = deepcopy(VALID_RESULT)
    initial["recommendations"][0]["description"] = " "
    router = FakeRouter([json.dumps(initial), json.dumps(VALID_RESULT)])
    app, client, _, worker = worker_fixture(
        tmp_path,
        router=router,
        verification_criteria=(FIELD,),
        correction_policy=PlanningCorrectionPolicy(),
    )
    try:
        service = app.state.autonomous_worker_service
        original = service._record_review_checkpoint

        def lost_ack(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("committed correction review acknowledgement lost")

        monkeypatch.setattr(service, "_record_review_checkpoint", lost_ack)
        with pytest.raises(RuntimeError, match="acknowledgement lost"):
            await service.run_once(worker.id)
        assert len(router.requests) == 1
        monkeypatch.setattr(service, "_record_review_checkpoint", original)
        expire_lease(app)
        for _ in range(3):
            await service.run_once(worker.id)
        assert len(router.requests) == 2
        assert len(review_records(app, "run-autonomous-1")) == 2
        assert app.state.task_leases.task_status("task-demo") == "completed"
    finally:
        client.__exit__(None, None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("control", ["stop", "cancel"])
async def test_operator_control_blocks_correction_dispatch(tmp_path, control):
    initial = deepcopy(VALID_RESULT)
    initial["recommendations"][0]["description"] = " "
    router = FakeRouter([json.dumps(initial), json.dumps(VALID_RESULT)])
    app, client, _, worker = worker_fixture(
        tmp_path,
        router=router,
        verification_criteria=(FIELD,),
        correction_policy=PlanningCorrectionPolicy(),
    )
    try:
        service = app.state.autonomous_worker_service
        assert (await service.run_once(worker.id)).failureCode == "review_revision_requested"
        if control == "stop":
            # The API lifespan owns simulator/broker locks on TestClient's loop.
            # Invoke the operator action there, as a real HTTP request does.
            response = await asyncio.to_thread(client.post, "/api/system/emergency-stop")
            assert response.status_code == 200, response.text
            with pytest.raises(AutonomousWorkerError) as stopped:
                await service.run_once(worker.id)
            assert stopped.value.code == "EXECUTION_EMERGENCY_STOPPED"
        else:
            app.state.task_leases.cancel_task("task-demo")
            assert await service.run_once(worker.id) is None
        assert len(router.requests) == 1
        assert len(execution_rows(app, "run-autonomous-1")) == 1
    finally:
        client.__exit__(None, None, None)


@pytest.mark.asyncio
async def test_worker_and_critic_repairs_consume_at_most_eight_dispatches(tmp_path):
    from tests.test_independent_verification import SEMANTIC, CriticRouter

    class RepairingCritic(CriticRouter):
        def __init__(self):
            super().__init__(outcome="needs_correction")
            self.contents = ["invalid", json.dumps(VALID_RESULT)] * 2

        async def execute(self, *, request, requirements, budget, pricing=None):
            if request.output_schema and request.output_schema.name == "independent_verdict":
                count = len(self.critic_requests)
                self.malformed = count + 1 if count % 2 == 0 else 0
                self.outcome = "needs_correction" if count < 2 else "passed"
            return await super().execute(request=request, requirements=requirements, budget=budget)

    router = RepairingCritic()
    app, client, _, worker = worker_fixture(
        tmp_path,
        router=router,
        verification_criteria=(SEMANTIC,),
        correction_policy=PlanningCorrectionPolicy(),
    )
    try:
        service = app.state.autonomous_worker_service
        assert (await service.run_once(worker.id)).failureCode == "review_revision_requested"
        assert (await service.run_once(worker.id)).stage == "completed"
        assert len(router.requests) == len(router.critic_requests) == 4
        assert await service.run_once(worker.id) is None
        assert len(router.requests) + len(router.critic_requests) == 8
        reservations = [
            item
            for item in service.runtime.repository.list_checkpoints("run-autonomous-1")
            if item.metadata.get("schemaName") == "planning-dispatch-1"
        ]
        assert len(reservations) == 8
        assert sorted(item.metadata["ordinal"] for item in reservations) == list(range(1, 9))
    finally:
        client.__exit__(None, None, None)


@pytest.mark.asyncio
async def test_failed_verification_corrects_once_without_rewriting_criteria(tmp_path):
    initial = deepcopy(VALID_RESULT)
    initial["recommendations"][0]["description"] = " "
    router = FakeRouter([json.dumps(initial), json.dumps(VALID_RESULT)])
    app, client, _, worker = worker_fixture(
        tmp_path,
        router=router,
        verification_criteria=(FIELD,),
        correction_policy=PlanningCorrectionPolicy(),
    )
    try:
        service = app.state.autonomous_worker_service
        first = await service.run_once(worker.id)
        assert first.failureCode == "review_revision_requested"
        second = await service.run_once(worker.id)
        assert second.stage == "completed"
        assert len(router.requests) == 2
        records = review_records(app, "run-autonomous-1")
        assert [record.metadata["outcome"] for record in records] == [
            "revision_requested",
            "accepted",
        ]
        assert records[0].metadata["verificationDigest"]
        assert records[0].metadata["correctionPolicyDigest"]
        rows = execution_rows(app, "run-autonomous-1")
        assert len(rows) == 2 and rows[0].result_hash != rows[1].result_hash
        assert app.state.task_leases.task_status("task-demo") == "completed"
        assert await service.run_once(worker.id) is None
        assert len(router.requests) == 2
    finally:
        client.__exit__(None, None, None)


@pytest.mark.asyncio
async def test_correction_permission_denial_at_task_commit_cannot_queue_retry(
    tmp_path, monkeypatch
):
    initial = deepcopy(VALID_RESULT)
    initial["recommendations"][0]["description"] = " "
    router = FakeRouter([json.dumps(initial)])
    app, client, actor_id, worker = worker_fixture(
        tmp_path,
        router=router,
        verification_criteria=(FIELD,),
        correction_policy=PlanningCorrectionPolicy(),
    )
    try:
        original = app.state.task_leases.fail_task

        def revoke_then_fail(*args, **kwargs):
            deny_completion(app, actor_id, "task-demo")
            return original(*args, **kwargs)

        monkeypatch.setattr(app.state.task_leases, "fail_task", revoke_then_fail)
        with pytest.raises(AutonomousWorkerError) as denial:
            await app.state.autonomous_worker_service.run_once(worker.id)
        assert denial.value.code == "EXECUTION_AUTHORIZATION_REVOKED"
        assert app.state.task_leases.task_status("task-demo") == "in_progress"
        assert (
            app.state.agent_runtime_service.repository.load_run("run-autonomous-1").state
            == "running"
        )
        assert len(router.requests) == 1
    finally:
        client.__exit__(None, None, None)
