"""Deterministic regressions for durable planning/review orchestration.

Every scenario drives the existing Phase 2C worker with a deterministic local
fake provider, a file-backed SQLite database, and explicit repository state.  No
sleeps, timing races, or probabilistic retries are used.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select, update

import app.autonomous_worker.service as worker_service
from app.autonomous_worker.__main__ import _run_once_resilient
from app.autonomous_worker.errors import AutonomousWorkerError
from app.autonomous_worker.plan_review import (
    PLAN_REVIEW_POLICY_VERSION,
    PlanReviewOutcome,
    decision_from_metadata,
    evaluate_plan,
)
from app.db.models import ModelExecutionRow, OutboxEventRow, TaskLeaseRow
from app.models.agent_runtime import AgentRuntimeEventType
from app.models.autonomous_worker import PlanningReviewResult
from tests.agent_runtime_testkit import ts
from tests.test_autonomous_worker import VALID_RESULT, FakeRouter, worker_fixture

INCOMPLETE_RESULT: dict[str, Any] = {**VALID_RESULT, "recommendations": []}
REVIEW_REQUIRED_RESULT: dict[str, Any] = {**VALID_RESULT, "requiresHumanReview": True}


def review_records(app, run_id: str) -> list:
    return [
        checkpoint
        for checkpoint in app.state.agent_runtime_service.repository.list_checkpoints(run_id)
        if "outcome" in checkpoint.metadata
    ]


def checkpoint_event_ids(app, run_id: str) -> list[str]:
    return [
        event.payload["checkpoint"]["checkpoint_id"]
        for event in app.state.agent_runtime_service.repository.list_events(run_id)
        if event.event_type == AgentRuntimeEventType.CHECKPOINT_RECORDED
    ]


def execution_rows(app, run_id: str) -> list[ModelExecutionRow]:
    with app.state.model_execution_repository.sessions() as session:
        return list(
            session.scalars(
                select(ModelExecutionRow)
                .where(ModelExecutionRow.runtime_run_id == run_id)
                .order_by(ModelExecutionRow.created_at, ModelExecutionRow.execution_id)
            )
        )


def outbox_count(app, event_type: str, run_id: str) -> int:
    with app.state.model_execution_repository.sessions() as session:
        return int(
            session.scalar(
                select(func.count())
                .select_from(OutboxEventRow)
                .where(
                    OutboxEventRow.event_type == event_type,
                    OutboxEventRow.correlation_id == run_id,
                )
            )
            or 0
        )


def expire_lease(app, task_id: str = "task-demo") -> None:
    """Simulate a crashed worker process by expiring its durable lease."""

    with app.state.model_execution_repository.sessions.begin() as session:
        session.execute(
            update(TaskLeaseRow).where(TaskLeaseRow.task_id == task_id).values(expires_at=ts(0))
        )
    assert app.state.task_leases.recover_expired_leases() == 1


def test_review_policy_is_deterministic_and_structural() -> None:
    accepted = evaluate_plan(PlanningReviewResult.model_validate(VALID_RESULT))
    assert accepted.outcome is PlanReviewOutcome.ACCEPTED
    assert accepted.reason_code == "plan_review_accepted"

    revision = evaluate_plan(PlanningReviewResult.model_validate(INCOMPLETE_RESULT))
    assert revision.outcome is PlanReviewOutcome.REVISION_REQUESTED
    assert revision.findings == ("plan_missing_recommendations",)

    escalated = evaluate_plan(PlanningReviewResult.model_validate(REVIEW_REQUIRED_RESULT))
    assert escalated.outcome is PlanReviewOutcome.ESCALATED

    # The same plan always produces the same decision, and a persisted record
    # round-trips exactly.
    assert evaluate_plan(PlanningReviewResult.model_validate(VALID_RESULT)) == accepted
    assert decision_from_metadata(accepted.as_metadata()) == accepted
    for corrupt in (
        {**accepted.as_metadata(), "outcome": "approved-by-model"},
        {**accepted.as_metadata(), "policyVersion": "9.9"},
        {**accepted.as_metadata(), "findings": "plan_missing_recommendations"},
    ):
        with pytest.raises(ValueError):
            decision_from_metadata(corrupt)


@pytest.mark.asyncio
async def test_accepted_plan_reaches_terminal_success_in_one_cycle(tmp_path: Path) -> None:
    run_id = "run-review-accepted"
    router = FakeRouter([json.dumps(VALID_RESULT)])
    app, client, _, worker = worker_fixture(tmp_path, router=router, run_id=run_id)
    try:
        result = await app.state.autonomous_worker_service.run_once(worker.id)
        assert result is not None
        assert result.stage == "completed"
        assert len(router.requests) == 1

        records = review_records(app, run_id)
        assert len(records) == 1
        assert records[0].metadata["outcome"] == PlanReviewOutcome.ACCEPTED.value
        assert records[0].metadata["policyVersion"] == PLAN_REVIEW_POLICY_VERSION
        # The review is bound to the exact attempt that produced the plan.
        assert records[0].attempt_id == result.runtimeAttemptId

        runtime = app.state.agent_runtime_service.repository.load_run(run_id)
        assert runtime is not None
        assert runtime.state == "succeeded"
        assert app.state.repository.tasks["task-demo"].status == "completed"
        assert len(execution_rows(app, run_id)) == 1
    finally:
        client.__exit__(None, None, None)


@pytest.mark.asyncio
async def test_recovery_after_plan_persistence_reviews_without_replanning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = "run-review-plan-replay"
    router = FakeRouter([json.dumps(VALID_RESULT)])
    app, client, _, worker = worker_fixture(tmp_path, router=router, run_id=run_id)
    service = app.state.autonomous_worker_service
    original_checkpoint = service._checkpoint

    def crash_after_result(snapshot, actor, execution, attempt_id, name):
        if name == "result-persisted":
            raise RuntimeError("injected process crash")
        return original_checkpoint(snapshot, actor, execution, attempt_id, name)

    monkeypatch.setattr(service, "_checkpoint", crash_after_result)
    try:
        with pytest.raises(RuntimeError, match="injected process crash"):
            await service.run_once(worker.id)
        stored = execution_rows(app, run_id)
        assert [row.stage for row in stored] == ["result_persisted"]
        assert review_records(app, run_id) == []

        monkeypatch.setattr(service, "_checkpoint", original_checkpoint)
        expire_lease(app)

        recovered = await service.run_once(worker.id)
        assert recovered is not None
        assert recovered.stage == "completed"
        # The durable plan is reused: no second provider call, no second row, and
        # no duplicate durable result event.
        assert len(router.requests) == 1
        assert len(execution_rows(app, run_id)) == 1
        assert outbox_count(app, "model.result.persisted", run_id) == 1
        assert outbox_count(app, "model.execution.completed", run_id) == 1
        records = review_records(app, run_id)
        assert len(records) == 1
        assert records[0].metadata["outcome"] == PlanReviewOutcome.ACCEPTED.value
        assert checkpoint_event_ids(app, run_id).count(records[0].checkpoint_id) == 1
    finally:
        client.__exit__(None, None, None)


@pytest.mark.asyncio
async def test_recovery_after_review_persistence_reuses_the_durable_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = "run-review-record-replay"
    router = FakeRouter([json.dumps(VALID_RESULT)])
    app, client, _, worker = worker_fixture(tmp_path, router=router, run_id=run_id)
    service = app.state.autonomous_worker_service
    evaluations: list[str] = []

    def counting_evaluate(result):
        decision = evaluate_plan(result)
        evaluations.append(decision.outcome.value)
        return decision

    monkeypatch.setattr(worker_service, "evaluate_plan", counting_evaluate)
    original_finalize = service._finalize

    def crash_after_review(snapshot, actor, execution, worker_id, lease_token):
        raise RuntimeError("injected process crash")

    monkeypatch.setattr(service, "_finalize", crash_after_review)
    try:
        with pytest.raises(RuntimeError, match="injected process crash"):
            await service.run_once(worker.id)
        records = review_records(app, run_id)
        assert len(records) == 1
        assert evaluations == [PlanReviewOutcome.ACCEPTED.value]

        monkeypatch.setattr(service, "_finalize", original_finalize)
        expire_lease(app)

        recovered = await service.run_once(worker.id)
        assert recovered is not None
        assert recovered.stage == "completed"
        # Recovery consumed the durable review record instead of reviewing again.
        assert evaluations == [PlanReviewOutcome.ACCEPTED.value]
        assert len(review_records(app, run_id)) == 1
        assert checkpoint_event_ids(app, run_id).count(records[0].checkpoint_id) == 1
        assert len(router.requests) == 1
    finally:
        client.__exit__(None, None, None)


@pytest.mark.asyncio
async def test_bounded_revision_retry_terminates_on_exhaustion(tmp_path: Path) -> None:
    run_id = "run-review-revision-budget"
    router = FakeRouter([json.dumps(INCOMPLETE_RESULT) for _ in range(4)])
    app, client, _, worker = worker_fixture(tmp_path, router=router, run_id=run_id)
    service = app.state.autonomous_worker_service
    try:
        first = await service.run_once(worker.id)
        assert first is not None
        assert first.failureCode == "review_revision_requested"
        runtime = app.state.agent_runtime_service.repository.load_run(run_id)
        assert runtime is not None
        assert runtime.state == "blocked"
        assert app.state.repository.tasks["task-demo"].status == "retrying"

        second = await service.run_once(worker.id)
        assert second is not None
        assert second.failureCode == "review_revision_requested"
        assert second.executionId != first.executionId
        assert second.runtimeAttemptId != first.runtimeAttemptId

        third = await service.run_once(worker.id)
        assert third is not None
        # The runtime attempt budget (3) terminates the loop deterministically.
        assert third.failureCode == "review_revision_exhausted"

        runtime = app.state.agent_runtime_service.repository.load_run(run_id)
        assert runtime is not None
        assert runtime.state == "failed"
        assert runtime.attempt_count == 3
        assert app.state.repository.tasks["task-demo"].status == "failed"

        rows = execution_rows(app, run_id)
        assert len(rows) == 3
        assert [row.runtime_attempt_id for row in rows] == [
            service._attempt_id(run_id, 0),
            service._attempt_id(run_id, 1),
            service._attempt_id(run_id, 2),
        ]
        assert [record.metadata["outcome"] for record in review_records(app, run_id)] == [
            PlanReviewOutcome.REVISION_REQUESTED.value
        ] * 3
        assert len(router.requests) == 3

        # No infinite progression: the terminal run is never replanned.
        assert await _run_once_resilient(service, worker.id) is None
        assert len(router.requests) == 3
        assert len(execution_rows(app, run_id)) == 3
    finally:
        client.__exit__(None, None, None)


@pytest.mark.asyncio
async def test_revision_cycle_uses_the_exact_prior_attempt_record(tmp_path: Path) -> None:
    run_id = "run-review-exact-lineage"
    router = FakeRouter([json.dumps(INCOMPLETE_RESULT), json.dumps(VALID_RESULT)])
    app, client, _, worker = worker_fixture(tmp_path, router=router, run_id=run_id)
    service = app.state.autonomous_worker_service
    try:
        first = await service.run_once(worker.id)
        assert first is not None
        assert first.failureCode == "review_revision_requested"

        second = await service.run_once(worker.id)
        assert second is not None
        assert second.stage == "completed"

        rows = execution_rows(app, run_id)
        assert len(rows) == 2
        assert rows[1].runtime_attempt_id == service._attempt_id(run_id, 1)
        # The revision request derives from the exact prior attempt, so the second
        # deterministic request differs from the first.
        assert rows[0].execution_request_hash != rows[1].execution_request_hash
        directive = router.requests[1].messages[-1].content
        assert "plan_missing_recommendations" in directive
        assert PLAN_REVIEW_POLICY_VERSION in directive

        records = review_records(app, run_id)
        assert len(records) == 2
        assert [record.attempt_id for record in records] == [
            rows[0].runtime_attempt_id,
            rows[1].runtime_attempt_id,
        ]
        assert [record.metadata["outcome"] for record in records] == [
            PlanReviewOutcome.REVISION_REQUESTED.value,
            PlanReviewOutcome.ACCEPTED.value,
        ]
        # The second attempt resumed from the exact first-attempt review record.
        attempts = app.state.agent_runtime_service.repository.load_attempt_history(run_id)
        assert attempts[1].resumed_from_checkpoint_id == records[0].checkpoint_id

        runtime = app.state.agent_runtime_service.repository.load_run(run_id)
        assert runtime is not None
        assert runtime.state == "succeeded"
        assert app.state.repository.tasks["task-demo"].status == "completed"
    finally:
        client.__exit__(None, None, None)


@pytest.mark.asyncio
async def test_cancelled_task_during_a_revision_cycle_is_not_stranded(tmp_path: Path) -> None:
    run_id = "run-review-revision-cancelled"
    router = FakeRouter([json.dumps(INCOMPLETE_RESULT), json.dumps(VALID_RESULT)])
    app, client, _, worker = worker_fixture(tmp_path, router=router, run_id=run_id)
    service = app.state.autonomous_worker_service
    try:
        first = await service.run_once(worker.id)
        assert first is not None
        assert first.failureCode == "review_revision_requested"
        app.state.task_leases.cancel_task("task-demo")

        assert await _run_once_resilient(service, worker.id) is None
        runtime = app.state.agent_runtime_service.repository.load_run(run_id)
        assert runtime is not None
        assert runtime.state == "cancelled"
        # No replacement planning cycle was started for the cancelled task.
        assert len(router.requests) == 1
        assert len(execution_rows(app, run_id)) == 1
    finally:
        client.__exit__(None, None, None)


@pytest.mark.asyncio
async def test_stale_lease_immediately_before_review_commit_cannot_advance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = "run-review-stale-lease"
    router = FakeRouter([json.dumps(VALID_RESULT)])
    app, client, _, worker = worker_fixture(tmp_path, router=router, run_id=run_id)
    service = app.state.autonomous_worker_service
    original_record = service._record_review_checkpoint
    rotated = threading.Event()

    def rotate_lease_then_record(snapshot, actor, execution, decision, worker_id, lease_token):
        if not rotated.is_set():
            rotated.set()
            with app.state.model_execution_repository.sessions.begin() as session:
                session.execute(
                    update(TaskLeaseRow)
                    .where(TaskLeaseRow.task_id == execution.taskId)
                    .values(lease_token="rotated-by-another-worker")
                )
        return original_record(snapshot, actor, execution, decision, worker_id, lease_token)

    monkeypatch.setattr(service, "_record_review_checkpoint", rotate_lease_then_record)
    try:
        assert await _run_once_resilient(service, worker.id) is None
        assert rotated.is_set()
        assert review_records(app, run_id) == []
        rows = execution_rows(app, run_id)
        assert [row.stage for row in rows] == ["result_persisted"]
        runtime = app.state.agent_runtime_service.repository.load_run(run_id)
        assert runtime is not None
        assert runtime.state != "succeeded"
        assert app.state.repository.tasks["task-demo"].status != "completed"
    finally:
        client.__exit__(None, None, None)


@pytest.mark.asyncio
async def test_emergency_stop_immediately_before_review_commit_blocks_advance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = "run-review-emergency-stop"
    router = FakeRouter([json.dumps(VALID_RESULT)])
    app, client, _, worker = worker_fixture(tmp_path, router=router, run_id=run_id)
    service = app.state.autonomous_worker_service
    original_record = service._record_review_checkpoint
    stopped = threading.Event()

    def stop_then_record(snapshot, actor, execution, decision, worker_id, lease_token):
        if not stopped.is_set():
            stopped.set()
            app.state.repository.emergency_stop = True
            app.state.repository.persist()
        return original_record(snapshot, actor, execution, decision, worker_id, lease_token)

    monkeypatch.setattr(service, "_record_review_checkpoint", stop_then_record)
    try:
        assert await _run_once_resilient(service, worker.id) is None
        assert stopped.is_set()
        assert review_records(app, run_id) == []
        assert [row.stage for row in execution_rows(app, run_id)] == ["result_persisted"]
        runtime = app.state.agent_runtime_service.repository.load_run(run_id)
        assert runtime is not None
        assert runtime.state != "succeeded"
    finally:
        client.__exit__(None, None, None)


@pytest.mark.asyncio
async def test_target_suspension_immediately_before_review_commit_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = "run-review-target-suspended"
    router = FakeRouter([json.dumps(VALID_RESULT)])
    app, client, _, worker = worker_fixture(tmp_path, router=router, run_id=run_id)
    service = app.state.autonomous_worker_service
    runtime = app.state.agent_runtime_service.repository.load_run(run_id)
    assert runtime is not None
    target_id = runtime.specification.agent_id
    suspended = threading.Event()

    def suspend_then_evaluate(result):
        if not suspended.is_set():
            suspended.set()
            app.state.identity_service.transition(target_id, "suspended")
        return evaluate_plan(result)

    monkeypatch.setattr(worker_service, "evaluate_plan", suspend_then_evaluate)
    try:
        with pytest.raises(AutonomousWorkerError) as raised:
            await service.run_once(worker.id)
        # The suspended target is also this run's actor, so either fail-closed
        # signal is acceptable; neither may advance durable state.
        assert raised.value.code in {
            "RUNTIME_EXECUTION_NOT_ELIGIBLE",
            "EXECUTION_AUTHORIZATION_REVOKED",
        }
        assert suspended.is_set()
        assert review_records(app, run_id) == []
        rows = execution_rows(app, run_id)
        assert [row.stage for row in rows] == ["result_persisted"]
        # The durable advance fence itself refuses the ineligible target while
        # holding the target row lock.
        with app.state.model_execution_repository.sessions() as session:
            lease = session.get(TaskLeaseRow, rows[0].task_id)
            assert lease is not None
            lease_token = lease.lease_token
        with pytest.raises(AutonomousWorkerError) as fenced:
            app.state.model_execution_repository.assert_advance_allowed(
                rows[0].execution_id,
                worker_id=rows[0].worker_id,
                lease_token=lease_token,
            )
        assert fenced.value.code == "RUNTIME_EXECUTION_NOT_ELIGIBLE"
        current = app.state.agent_runtime_service.repository.load_run(run_id)
        assert current is not None
        assert current.state != "succeeded"
        assert app.state.repository.tasks["task-demo"].status != "completed"
    finally:
        client.__exit__(None, None, None)


@pytest.mark.asyncio
async def test_terminal_workflow_replay_is_a_no_op(tmp_path: Path) -> None:
    run_id = "run-review-terminal-replay"
    router = FakeRouter([json.dumps(VALID_RESULT)])
    app, client, _, worker = worker_fixture(tmp_path, router=router, run_id=run_id)
    service = app.state.autonomous_worker_service
    runtime_repository = app.state.agent_runtime_service.repository
    try:
        completed = await service.run_once(worker.id)
        assert completed is not None
        assert completed.stage == "completed"
        events = len(runtime_repository.list_events(run_id))
        checkpoints = len(runtime_repository.list_checkpoints(run_id))
        outbox = outbox_count(app, "model.execution.completed", run_id)

        assert await _run_once_resilient(service, worker.id) is None
        assert await _run_once_resilient(service, worker.id) is None

        assert len(runtime_repository.list_events(run_id)) == events
        assert len(runtime_repository.list_checkpoints(run_id)) == checkpoints
        assert outbox_count(app, "model.execution.completed", run_id) == outbox
        assert len(router.requests) == 1
        assert len(execution_rows(app, run_id)) == 1
        runtime = runtime_repository.load_run(run_id)
        assert runtime is not None
        assert runtime.state == "succeeded"
        assert app.state.repository.tasks["task-demo"].status == "completed"

        # A replayed review command against the terminal run stays a no-op.
        actor = app.state.agent_runtime_service.authenticate_actor(
            app.state.settings.autonomous_worker_actor_id
        )
        execution = app.state.model_execution_repository.get(completed.executionId)
        assert execution is not None
        decision = service._durable_review_decision(execution, actor)
        assert decision is not None
        assert decision.outcome is PlanReviewOutcome.ACCEPTED
        assert len(runtime_repository.list_events(run_id)) == events
    finally:
        client.__exit__(None, None, None)


@pytest.mark.asyncio
async def test_corrupt_review_record_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = "run-review-corrupt-record"
    router = FakeRouter([json.dumps(VALID_RESULT)])
    app, client, _, worker = worker_fixture(tmp_path, router=router, run_id=run_id)
    service = app.state.autonomous_worker_service
    try:
        completed = await service.run_once(worker.id)
        assert completed is not None
        actor = app.state.agent_runtime_service.authenticate_actor(
            app.state.settings.autonomous_worker_actor_id
        )
        execution = app.state.model_execution_repository.get(completed.executionId)
        assert execution is not None
        original = app.state.agent_runtime_service.checkpoints_authorized(run_id, actor)
        tampered = [
            checkpoint.model_copy(
                update={"metadata": {**checkpoint.metadata, "outcome": "accepted_by_model_text"}}
            )
            if "outcome" in checkpoint.metadata
            else checkpoint
            for checkpoint in original
        ]
        monkeypatch.setattr(
            app.state.agent_runtime_service,
            "checkpoints_authorized",
            lambda run, actor_context: tampered,
        )
        with pytest.raises(AutonomousWorkerError) as raised:
            service._durable_review_decision(execution, actor)
        assert raised.value.code == "PLAN_REVIEW_RECORD_CORRUPT"
    finally:
        client.__exit__(None, None, None)


@pytest.mark.asyncio
async def test_concurrent_review_handling_creates_one_durable_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = "run-review-concurrent"
    router = FakeRouter([json.dumps(VALID_RESULT)])
    app, client, _, worker = worker_fixture(tmp_path, router=router, run_id=run_id)
    service = app.state.autonomous_worker_service
    original_resolve = service._resolve_review

    def crash_before_review(snapshot, actor, execution, worker_id, lease_token):
        raise RuntimeError("injected process crash")

    monkeypatch.setattr(service, "_resolve_review", crash_before_review)
    try:
        with pytest.raises(RuntimeError, match="injected process crash"):
            await service.run_once(worker.id)
        monkeypatch.setattr(service, "_resolve_review", original_resolve)

        actor = app.state.agent_runtime_service.authenticate_actor(
            app.state.settings.autonomous_worker_actor_id
        )
        snapshot = app.state.agent_runtime_service.read_run_authorized(run_id, actor)
        execution = app.state.model_execution_repository.get_by_run(run_id)
        assert execution is not None
        with app.state.model_execution_repository.sessions() as session:
            lease = session.get(TaskLeaseRow, execution.taskId)
            assert lease is not None
            lease_token = lease.lease_token

        barrier = threading.Barrier(2)
        outcomes: list[str] = []
        failures: list[Exception] = []

        def resolve_once() -> None:
            barrier.wait(timeout=30)
            try:
                _, decision = original_resolve(snapshot, actor, execution, worker.id, lease_token)
            except Exception as exc:  # noqa: BLE001 - recorded for the assertion below
                failures.append(exc)
                return
            outcomes.append(decision.outcome.value)

        threads = [threading.Thread(target=resolve_once) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
            assert not thread.is_alive()

        assert outcomes, f"no handler succeeded: {failures}"
        assert all(outcome == PlanReviewOutcome.ACCEPTED.value for outcome in outcomes)
        records = review_records(app, run_id)
        assert len(records) == 1
        assert checkpoint_event_ids(app, run_id).count(records[0].checkpoint_id) == 1
        assert len(execution_rows(app, run_id)) == 1
    finally:
        client.__exit__(None, None, None)
