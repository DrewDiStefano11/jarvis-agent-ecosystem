"""Reproduce review findings through native repositories and worker recovery."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.autonomous_worker.errors import AutonomousWorkerError
from app.core.errors import DomainError
from app.db.models import CoordinationRow
from app.models.correction import PlanningCorrectionPolicy
from app.models.verification import CompletionCriterion
from tests.test_autonomous_worker import VALID_RESULT, FakeRouter, worker_fixture
from tests.test_coordination import claim_fixture
from tests.test_coordination_verification import critic_router
from tests.test_independent_verification import FIELD
from tests.test_planning_review_orchestration import expire_lease, review_records

pytest_plugins = ["tests.test_coordination"]


@pytest.mark.parametrize("prepared", [{"verify": True}], indirect=True)
def test_native_dispatch_cap_does_not_persist_invalid_counter(app, prepared):
    coordinator, actor, _, record, fence = claim_fixture(app, prepared)
    with app.state.repository.session_factory() as session, session.begin():
        row = session.get(CoordinationRow, record.id)
        row.payload = row.payload | {"modelDispatchCount": 38}
    with pytest.raises(DomainError) as exhausted:
        coordinator.repository.record_dispatch(record.id, fence, coordinator.live_validator(actor))
    assert exhausted.value.code == "COORDINATION_MODEL_BUDGET_EXCEEDED"
    assert coordinator._required(record.id).modelDispatchCount == 38


@pytest.mark.asyncio
async def test_uncertain_worker_dispatch_is_not_repeated_with_correction_policy(tmp_path):
    def crash():
        raise RuntimeError("lost provider acknowledgement")

    router = FakeRouter([json.dumps(VALID_RESULT)], callback=crash)
    app, client, _, worker = worker_fixture(
        tmp_path,
        router=router,
        verification_criteria=(FIELD,),
        correction_policy=PlanningCorrectionPolicy(),
    )
    try:
        service = app.state.autonomous_worker_service
        with pytest.raises(RuntimeError, match="lost provider acknowledgement"):
            await service.run_once(worker.id)
        expire_lease(app)
        router.callback = None
        recovered = await service.run_once(worker.id)
        assert recovered.failureCode == "model_dispatch_outcome_unknown"
        assert recovered.stage == "human_review_required"
        assert app.state.task_leases.task_status("task-demo") == "under_review"
        assert service.runtime.repository.load_run("run-autonomous-1").state == "paused"
        assert len(router.requests) == 1
        assert await service.run_once(worker.id) is None
    finally:
        client.__exit__(None, None, None)


@pytest.mark.asyncio
async def test_structural_exhaustion_retains_exact_verifier_binding(tmp_path, monkeypatch):
    result = {**VALID_RESULT, "recommendations": []}
    criterion = CompletionCriterion(
        id="summary", description="Provide summary", mode="field_nonempty", field="summary"
    )
    router = FakeRouter([json.dumps(result)] * 2)
    app, client, actor_id, worker = worker_fixture(
        tmp_path,
        router=router,
        verification_criteria=(criterion,),
        correction_policy=PlanningCorrectionPolicy(),
    )
    try:
        service = app.state.autonomous_worker_service
        await service.run_once(worker.id)
        exhausted = await service.run_once(worker.id)
        actor = service.runtime.authenticate_actor(actor_id)
        verdict = service.verifier.read(exhausted, actor)
        record = review_records(app, "run-autonomous-1")[-1]
        assert record.metadata["verificationDigest"] == verdict.digest
        assert service._durable_review_decision(exhausted, actor) is not None
        monkeypatch.setattr(service.verifier, "read", lambda execution, context: None)
        with pytest.raises(AutonomousWorkerError) as corrupt:
            service._durable_review_decision(exhausted, actor)
        assert corrupt.value.code == "PLAN_REVIEW_RECORD_CORRUPT"
        assert len(router.requests) == 2
    finally:
        client.__exit__(None, None, None)


@pytest.mark.parametrize("prepared", [{"verify": True}], indirect=True)
def test_concurrent_reservations_cannot_cross_last_coordinator_slot(app, prepared):
    coordinator, actor, _, record, fence = claim_fixture(app, prepared)
    with app.state.repository.session_factory() as session, session.begin():
        row = session.get(CoordinationRow, record.id)
        row.payload = row.payload | {"modelDispatchCount": 37}
    barrier = threading.Barrier(2)

    def reserve():
        barrier.wait(timeout=5)
        try:
            coordinator.repository.record_dispatch(
                record.id, fence, coordinator.live_validator(actor)
            )
            return "reserved"
        except DomainError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(reserve) for _ in range(2)]
        outcomes = [future.result(timeout=10) for future in futures]
    assert sorted(outcomes) == ["COORDINATION_MODEL_BUDGET_EXCEEDED", "reserved"]
    assert coordinator._required(record.id).modelDispatchCount == 38


@pytest.mark.asyncio
@pytest.mark.parametrize("prepared", [{"verify": True}], indirect=True)
async def test_exhausted_critic_budget_terminates_without_inference(app, prepared, monkeypatch):
    coordinator, actor, _, record, fence = claim_fixture(app, prepared)
    router = critic_router(app, coordinator)
    original = coordinator._verify_node

    async def consume_budget(*args):
        with app.state.repository.session_factory() as session, session.begin():
            row = session.get(CoordinationRow, record.id)
            row.payload = row.payload | {"modelDispatchCount": 38}
        return await original(*args)

    monkeypatch.setattr(coordinator, "_verify_node", consume_budget)
    rejected = await coordinator.run_once(record.id, fence, actor)
    assert rejected.status == "blocked"
    assert rejected.blockedReason == "COORDINATION_MODEL_BUDGET_EXCEEDED"
    assert rejected.modelDispatchCount == 38
    assert len(router.critic_requests) == 0
    assert len(router.coordinator_calls) == 1
