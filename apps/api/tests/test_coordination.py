"""Phase A: canonical preparation/claiming over migrated isolated databases."""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.agent_runtime.errors import RuntimePermissionDeniedError
from app.agent_runtime.repository import RuntimeExecutionFence
from app.coordination.repository import CoordinationRepository
from app.coordination.service import CoordinatorService
from app.core.errors import DomainError
from app.db.models import (
    AgentPermissionAssignmentRow,
    AgentRoleAssignmentRow,
    AuditEventRow,
    CatalogActivationRow,
    CoordinationRow,
    IdentityAgentRow,
    OutboxEventRow,
    SystemStateRow,
    TaskDecompositionRow,
    TaskLeaseRow,
    TaskRow,
)
from app.models.coordination import SpecialistResult, SynthesisResult, execution_ready_keys
from tests.test_agent_runtime_sql_control_plane import grant_runtime_permissions
from tests.test_autonomous_worker import queue_autonomous_runtime
from tests.test_task_decomposition import app as decomposition_app
from tests.test_task_decomposition import node, proposal, service, setup


def install_coordinator_router(app, coordinator, *, script=None, mutate=None):
    import json

    from app.model_providers.contracts import ModelExecutionResponse
    from tests.test_task_decomposition import Router

    class CoordinatorRouter(Router):
        def __init__(self):
            super().__init__()
            self.coordinator_calls = []

        async def execute(self, *, request, requirements, budget, pricing=None):
            if request.output_schema.name not in {"SpecialistResult", "SynthesisResult"}:
                return await super().execute(
                    request=request, requirements=requirements, budget=budget
                )
            payload = json.loads(request.messages[0].content.split("\n", 1)[1])
            self.coordinator_calls.append((request.output_schema.name, payload))
            if request.output_schema.name == "SpecialistResult":
                content = dict(
                    subtaskId=payload["subtaskId"],
                    summary="Validated deliverable",
                    evidence=["bounded source evidence"],
                    completionCriteriaSatisfied=payload["completionCriteria"],
                )
            else:
                content = dict(
                    summary="Manager synthesis",
                    contributingSubtaskIds=[item["subtaskId"] for item in payload],
                )
            if script:
                content = script(
                    request.output_schema.name, payload, len(self.coordinator_calls), content
                )
            if mutate:
                mutate()
            return ModelExecutionResponse(
                content=content if isinstance(content, str) else json.dumps(content),
                provider="local-fake",
                model="fixture-model",
                latency_ms=1,
                finish_reason="stop",
            )

    router = CoordinatorRouter()
    app.state.model_router = router
    coordinator.router = router
    coordinator.decomposition.router = router
    return router


@pytest.fixture
def app(tmp_path, monkeypatch):
    yield from decomposition_app.__wrapped__(tmp_path, monkeypatch)


@pytest.fixture
def prepared(app):
    task_id, assembly_id, _ = setup(
        app, proposal([node("a"), node("b"), node("c", deps=["a", "b"])]), auto=True
    )
    graph = service(app).current(task_id)
    sessions = app.state.repository.session_factory
    with sessions() as session, session.begin():
        row = session.get(TaskRow, task_id)
        row.status = "queued"
        row.payload = row.payload | {"status": "queued"}
    actor_id = grant_runtime_permissions(app, "coord-worker-actor", task_id=task_id)
    task = app.state.repository.get_task_durable(task_id)
    queue_autonomous_runtime(
        app,
        actor_id,
        assembly_id=assembly_id,
        run_id="coord-parent",
        task_id=task_id,
        target_agent_id=task.teamSelection.managerId,
    )
    coordinator = CoordinatorService(
        app.state.repository,
        app.state.identity_service,
        app.state.agent_runtime_service,
        app.state.model_router,
    )
    actor = app.state.agent_runtime_service.authenticate_actor(actor_id)
    return coordinator, actor, graph


def claim_fixture(app, prepared):
    coordinator, actor, graph = prepared
    record = coordinator.prepare(graph.taskId, graph.id, "coord-parent", actor)
    worker = app.state.task_leases.register_worker("coord-worker", "Coordinator fixture", 60, {})
    _, lease = app.state.task_leases.acquire_task(worker.id, task_id=graph.taskId)
    fence = RuntimeExecutionFence(
        task_id=graph.taskId, worker_id=worker.id, lease_token=lease.leaseToken
    )
    from app.models.agent_runtime import (
        BeginAttemptCommand,
        ClaimAgentRunCommand,
        CompleteAttemptCommand,
        StartAttemptCommand,
    )

    parent = coordinator.runtime.repository.load_run("coord-parent")
    if parent.state == "queued":
        coordinator._runtime_command(
            ClaimAgentRunCommand, parent, "claim-parent", fence, actor, executor_reference=worker.id
        )
    parent = coordinator.runtime.repository.load_run("coord-parent")
    if parent.attempt_count == 0:
        parent = coordinator._runtime_command(
            BeginAttemptCommand,
            parent,
            "parent-begin",
            fence,
            actor,
            attempt_id="coord-parent-planning",
            executor_reference=worker.id,
        )
        parent = coordinator._runtime_command(
            StartAttemptCommand,
            parent,
            "parent-start",
            fence,
            actor,
            attempt_id="coord-parent-planning",
        )
        coordinator._runtime_command(
            CompleteAttemptCommand,
            parent,
            "parent-planning-complete",
            fence,
            actor,
            attempt_id="coord-parent-planning",
            detail="Accepted fixture planning handoff",
        )
    return coordinator, actor, graph, record, fence


def takeover(app, fence):
    with app.state.repository.session_factory() as session, session.begin():
        session.get(TaskLeaseRow, fence.task_id).expires_at = datetime.now(UTC) - timedelta(
            seconds=1
        )
    app.state.task_leases.recover_expired_leases()
    _, lease = app.state.task_leases.acquire_task(fence.worker_id, task_id=fence.task_id)
    return RuntimeExecutionFence(
        task_id=fence.task_id, worker_id=fence.worker_id, lease_token=lease.leaseToken
    )


def eligible_now(app, record):
    with app.state.repository.session_factory() as session, session.begin():
        row = session.get(CoordinationRow, record.id)
        payload = dict(row.payload)
        payload["synthesis"] = dict(payload["synthesis"], retryEligibleAt=None)
        payload["nodes"] = [dict(node, retryEligibleAt=None) for node in payload["nodes"]]
        row.payload = payload


def test_ready_calculation_preserves_parallel_nodes_and_enforces_dependencies(prepared):
    _, _, graph = prepared
    kwargs = dict(
        eligible_agents=frozenset(n.assignedAgentId for n in graph.subtasks),
        execution_permitted=True,
    )
    assert execution_ready_keys(graph, **kwargs) == ["a", "b"]
    assert execution_ready_keys(graph, completed=frozenset({"a"}), **kwargs) == ["b"]
    assert execution_ready_keys(graph, completed=frozenset({"a", "b"}), **kwargs) == ["c"]
    assert execution_ready_keys(graph, owned=frozenset({"a", "b"}), **kwargs) == []
    assert execution_ready_keys(graph, **(kwargs | {"execution_permitted": False})) == []
    assert execution_ready_keys(graph, **(kwargs | {"eligible_agents": frozenset()})) == []


def authority_snapshot(app):
    with app.state.repository.session_factory() as session:
        return (
            tuple(
                session.scalar(select(func.count()).select_from(model))
                for model in (
                    AgentPermissionAssignmentRow,
                    AgentRoleAssignmentRow,
                    CatalogActivationRow,
                )
            ),
            tuple(
                (a.id, a.rank_id, a.is_system_agent, a.lifecycle_state)
                for a in session.scalars(select(IdentityAgentRow).order_by(IdentityAgentRow.id))
            ),
        )


def test_prepare_and_claim_are_durable_idempotent_and_grant_zero_authority(app, prepared):
    before = authority_snapshot(app)
    coordinator, actor, graph, record, fence = claim_fixture(app, prepared)
    assert coordinator.prepare(graph.taskId, graph.id, "coord-parent", actor) == record
    claimed = coordinator.claim_ready(record.id, fence, actor)
    assert claimed.key == "a"
    assert claimed.assignedAgentId == graph.subtasks[0].assignedAgentId
    assert coordinator.claim_ready(record.id, fence, actor) is None
    restarted = CoordinationRepository(app.state.repository.session_factory).current(graph.taskId)
    assert restarted.nodes[0] == claimed
    assert authority_snapshot(app) == before
    with app.state.repository.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(CoordinationRow)) == 1
        for model in (AuditEventRow, OutboxEventRow):
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(model)
                    .where(model.event_type == "coordination.subtask_claimed")
                )
                == 1
            )


def test_concurrent_coordinators_converge_and_one_canonical_claim(app, prepared):
    coordinator, actor, graph = prepared
    barrier = threading.Barrier(2)

    def prepare_one(_):
        barrier.wait(timeout=5)
        return coordinator.prepare(graph.taskId, graph.id, "coord-parent", actor)

    with ThreadPoolExecutor(max_workers=2) as pool:
        records = list(pool.map(prepare_one, range(2)))
    assert records[0] == records[1]
    _, _, _, record, fence = claim_fixture(app, prepared)
    barrier = threading.Barrier(2)

    def claim_one(_):
        barrier.wait(timeout=5)
        return coordinator.claim_ready(record.id, fence, actor)

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(claim_one, range(2)))
    assert sum(c is not None for c in claims) == 1


@pytest.mark.parametrize("status", ["cancelled", "paused", "under_review", "completed", "failed"])
def test_parent_lifecycle_stops_claim(app, prepared, status):
    coordinator, actor, graph, record, fence = claim_fixture(app, prepared)
    with app.state.repository.session_factory() as session, session.begin():
        row = session.get(TaskRow, graph.taskId)
        row.status = status
        row.payload = row.payload | {"status": status}
    with pytest.raises(DomainError, match="not executable"):
        coordinator.claim_ready(record.id, fence, actor)


def test_emergency_stop_blocks_claim(app, prepared):
    coordinator, actor, _, record, fence = claim_fixture(app, prepared)
    with app.state.repository.session_factory() as session, session.begin():
        session.get(SystemStateRow, 1).emergency_stop = True
    with pytest.raises(DomainError, match="stopped"):
        coordinator.claim_ready(record.id, fence, actor)


def test_expired_lease_blocks_old_owner_and_preserves_preparation(app, prepared):
    coordinator, actor, graph, record, fence = claim_fixture(app, prepared)
    claimed = coordinator.claim_ready(record.id, fence, actor)
    with app.state.repository.session_factory() as session, session.begin():
        session.get(TaskLeaseRow, graph.taskId).expires_at = datetime.now(UTC) - timedelta(
            seconds=1
        )
    with pytest.raises(DomainError, match="task lease expired"):
        coordinator.claim_ready(record.id, fence, actor)
    assert app.state.task_leases.recover_expired_leases() == 1
    _, new_lease = app.state.task_leases.acquire_task(fence.worker_id, task_id=graph.taskId)
    new_fence = RuntimeExecutionFence(
        task_id=graph.taskId, worker_id=fence.worker_id, lease_token=new_lease.leaseToken
    )
    assert coordinator.claim_ready(record.id, new_fence, actor) is None
    assert coordinator.repository.current(graph.taskId).nodes[0] == claimed


def test_stale_decomposition_cannot_claim(app, prepared):
    coordinator, actor, graph, record, fence = claim_fixture(app, prepared)
    with app.state.repository.session_factory() as session, session.begin():
        session.get(TaskDecompositionRow, graph.id).active_task_id = None
    with pytest.raises(DomainError, match="authoritative ready graph"):
        coordinator.claim_ready(record.id, fence, actor)


def test_changed_objective_cannot_claim(app, prepared):
    coordinator, actor, graph, record, fence = claim_fixture(app, prepared)
    with app.state.repository.session_factory() as session, session.begin():
        row = session.get(TaskRow, graph.taskId)
        row.payload = row.payload | {"request": "Operator corrected the objective."}
    with pytest.raises(DomainError, match="Planning inputs changed"):
        coordinator.claim_ready(record.id, fence, actor)


def test_suspended_specialist_cannot_claim(app, prepared):
    coordinator, actor, graph, record, fence = claim_fixture(app, prepared)
    with app.state.repository.session_factory() as session, session.begin():
        session.get(
            IdentityAgentRow, graph.subtasks[0].assignedAgentId
        ).lifecycle_state = "suspended"
    with pytest.raises(DomainError, match="Planning inputs changed"):
        coordinator.claim_ready(record.id, fence, actor)


def test_specialist_identity_cannot_become_manager(app, prepared):
    coordinator, actor, graph = prepared
    task = app.state.repository.get_task_durable(graph.taskId)
    with app.state.repository.session_factory() as session, session.begin():
        session.get(IdentityAgentRow, task.teamSelection.managerId).agent_type = "specialist"
    with pytest.raises(DomainError, match="planning identity"):
        coordinator.prepare(graph.taskId, graph.id, "coord-parent", actor)


def test_revoked_actor_permission_cannot_claim(app, prepared):
    coordinator, actor, _, record, fence = claim_fixture(app, prepared)
    with app.state.repository.session_factory() as session, session.begin():
        for row in session.scalars(
            select(AgentPermissionAssignmentRow).where(
                AgentPermissionAssignmentRow.agent_id == actor.actor_id
            )
        ):
            row.revoked_at = datetime.now(UTC) - timedelta(seconds=1)
    with pytest.raises(RuntimePermissionDeniedError):
        coordinator.claim_ready(record.id, fence, actor)


def test_operator_edit_is_never_silently_overridden(app, prepared):
    coordinator, actor, graph, record, fence = claim_fixture(app, prepared)
    with app.state.repository.session_factory() as session, session.begin():
        row = session.get(TaskDecompositionRow, graph.id)
        row.payload = row.payload | {"operatorProtected": True}
    with pytest.raises(DomainError, match="Operator plan changes"):
        coordinator.claim_ready(record.id, fence, actor)
    assert coordinator.repository.current(graph.taskId) == record


def test_populated_downgrade_preserves_history(app, prepared):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    coordinator, actor, graph = prepared
    record = coordinator.prepare(graph.taskId, graph.id, "coord-parent", actor)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", str(app.state.engine.url))
    with pytest.raises(RuntimeError, match="Export coordination history"):
        command.downgrade(config, "20260906_10")
    assert coordinator.repository.current(graph.taskId) == record


def _success(repository, coordinator, actor, record, fence, node):
    started = repository.begin_attempt(
        record.id,
        node.subtaskId,
        node.runtimeAttemptId,
        fence,
        coordinator.live_validator(actor),
    )
    from app.models.agent_runtime import (
        CompleteAgentRunCommand,
        CompleteAttemptCommand,
        RecordCheckpointCommand,
    )

    planned = next(
        item
        for item in coordinator.decomposition.current(record.taskId).subtasks
        if item.id == node.subtaskId
    )
    result = SpecialistResult(
        subtaskId=node.subtaskId,
        summary=f"Completed {started.key}",
        evidence=[f"evidence:{started.key}"],
        completionCriteriaSatisfied=planned.completionCriteria,
    )
    digest = coordinator._digest(result.model_dump(mode="json"))
    snapshot = coordinator._start_runtime(
        record,
        run_id=node.runtimeRunId,
        attempt_id=node.runtimeAttemptId,
        agent_id=node.assignedAgentId,
        operation="Fixture specialist",
        capabilities=tuple(planned.requiredCapabilities),
        fence=fence,
        actor=actor,
    )
    snapshot = coordinator._runtime_command(
        RecordCheckpointCommand,
        snapshot,
        "checkpoint",
        fence,
        actor,
        checkpoint_id=f"checkpoint-{started.key}",
        attempt_id=node.runtimeAttemptId,
        state_reference=f"coordination:{record.id}:subtask:{node.subtaskId}",
        integrity_digest=digest,
        resume_cursor=node.subtaskId,
        checkpoint_metadata={
            "schemaName": "coordination-specialist-result-v1",
            "resultDigest": digest,
            **coordinator._checkpoint_payload(result),
        },
    )
    snapshot = coordinator._runtime_command(
        CompleteAttemptCommand,
        snapshot,
        "complete-attempt",
        fence,
        actor,
        attempt_id=node.runtimeAttemptId,
        detail="Validated specialist result persisted",
    )
    coordinator._runtime_command(
        CompleteAgentRunCommand,
        snapshot,
        "complete-run",
        fence,
        actor,
        detail="Coordinated specialist execution completed",
    )
    return repository.record_success(
        record.id,
        started.subtaskId,
        started.runtimeAttemptId,
        summary=f"Completed {started.key}",
        result_digest=digest,
        evidence=[f"evidence:{started.key}"],
        checkpoint_id=f"checkpoint-{started.key}",
        fence=fence,
        validate_live=coordinator.live_validator(actor),
    )


def test_durable_success_unlocks_only_satisfied_dependencies(app, prepared):
    coordinator, actor, _, record, fence = claim_fixture(app, prepared)
    first = coordinator.claim_ready(record.id, fence, actor)
    record = _success(coordinator.repository, coordinator, actor, record, fence, first)
    assert [node.status for node in record.nodes] == ["succeeded", "pending", "pending"]
    second = coordinator.claim_ready(record.id, fence, actor)
    assert second.key == "b"
    record = _success(coordinator.repository, coordinator, actor, record, fence, second)
    assert coordinator.claim_ready(record.id, fence, actor).key == "c"


def test_retry_counter_is_durable_bounded_and_replay_does_not_increment(app, prepared):
    coordinator, actor, _, record, fence = claim_fixture(app, prepared)
    node = coordinator.claim_ready(record.id, fence, actor)
    started = coordinator.repository.begin_attempt(
        record.id,
        node.subtaskId,
        node.runtimeAttemptId,
        fence,
        coordinator.live_validator(actor),
    )
    assert (
        coordinator.repository.begin_attempt(
            record.id,
            node.subtaskId,
            node.runtimeAttemptId,
            fence,
            coordinator.live_validator(actor),
        )
        is None
    )
    record = coordinator.repository.record_failure(
        record.id,
        node.subtaskId,
        started.runtimeAttemptId,
        category="provider",
        detail="retryable provider failure",
        retryable=True,
        fence=fence,
        validate_live=coordinator.live_validator(actor),
    )
    replay = coordinator.repository.record_failure(
        record.id,
        node.subtaskId,
        started.runtimeAttemptId,
        category="provider",
        detail="retryable provider failure",
        retryable=True,
        fence=fence,
        validate_live=coordinator.live_validator(actor),
    )
    assert replay.nodes[0].attemptCount == 1
    assert len(replay.failures) == 1
    independent = coordinator.claim_ready(record.id, fence, actor)
    assert independent.key == "b"
    record = _success(coordinator.repository, coordinator, actor, record, fence, independent)
    with app.state.repository.session_factory() as session, session.begin():
        row = session.get(CoordinationRow, record.id)
        payload = dict(row.payload)
        payload["nodes"] = [dict(item) for item in payload["nodes"]]
        payload["nodes"][0]["retryEligibleAt"] = (
            datetime.now(UTC) - timedelta(seconds=1)
        ).isoformat()
        row.payload = payload
    restarted = CoordinatorService(
        app.state.repository,
        app.state.identity_service,
        app.state.agent_runtime_service,
        app.state.model_router,
    )
    retry = restarted.claim_ready(record.id, fence, actor)
    assert retry.runtimeAttemptId != started.runtimeAttemptId


def test_exhausted_failure_blocks_dependents_and_synthesis(app, prepared):
    coordinator, actor, _, record, fence = claim_fixture(app, prepared)
    node = coordinator.claim_ready(record.id, fence, actor)
    started = coordinator.repository.begin_attempt(
        record.id,
        node.subtaskId,
        node.runtimeAttemptId,
        fence,
        coordinator.live_validator(actor),
    )
    with app.state.repository.session_factory() as session, session.begin():
        row = session.get(CoordinationRow, record.id)
        payload = dict(row.payload)
        payload["nodes"] = [dict(item) for item in payload["nodes"]]
        payload["nodes"][0]["attemptCount"] = 3
        row.payload = payload
    record = coordinator.repository.record_failure(
        record.id,
        node.subtaskId,
        started.runtimeAttemptId,
        category="provider",
        detail="retry exhausted",
        retryable=True,
        fence=fence,
        validate_live=coordinator.live_validator(actor),
    )
    assert record.status == "failed"
    assert record.nodes[0].status == "failed"
    assert record.nodes[2].status == "blocked"
    assert (
        coordinator.repository.begin_synthesis(record.id, fence, coordinator.live_validator(actor))
        is None
    )


@pytest.mark.asyncio
async def test_restart_during_running_attempt_records_one_durable_retry(app, prepared):
    coordinator, actor, _, record, fence = claim_fixture(app, prepared)
    node = coordinator.claim_ready(record.id, fence, actor)
    coordinator.repository.begin_attempt(
        record.id,
        node.subtaskId,
        node.runtimeAttemptId,
        fence,
        coordinator.live_validator(actor),
    )
    restarted = CoordinatorService(
        app.state.repository,
        app.state.identity_service,
        app.state.agent_runtime_service,
        app.state.model_router,
    )
    recovered = await restarted.run_once(record.id, fence, actor)
    assert recovered.nodes[0].status == "running"
    fence = takeover(app, fence)
    recovered = await restarted.run_once(record.id, fence, actor)
    assert recovered.nodes[0].status == "retrying"
    assert recovered.nodes[0].attemptCount == 1
    assert len(recovered.failures) == 1
    replay = await restarted.run_once(record.id, fence, actor)
    assert replay.nodes[0].attemptCount == 1
    assert sum(item.subtaskId == node.subtaskId for item in replay.failures) == 1


def test_synthesis_retry_is_durable_and_bounded(app, prepared):
    coordinator, actor, _, record, fence = claim_fixture(app, prepared)
    for _ in range(3):
        node = coordinator.claim_ready(record.id, fence, actor)
        record = _success(coordinator.repository, coordinator, actor, record, fence, node)
    record, _ = coordinator.repository.begin_synthesis(
        record.id, fence, coordinator.live_validator(actor)
    )
    first_attempt = record.synthesis.runtimeAttemptId
    record = coordinator.repository.record_synthesis_failure(
        record.id,
        first_attempt,
        "provider unavailable",
        fence,
        coordinator.live_validator(actor),
    )
    assert record.status == "active"
    assert (
        coordinator.repository.begin_synthesis(record.id, fence, coordinator.live_validator(actor))
        is None
    )
    eligible_now(app, record)
    record, _ = coordinator.repository.begin_synthesis(
        record.id, fence, coordinator.live_validator(actor)
    )
    assert record.synthesis.runtimeAttemptId != first_attempt
    record = coordinator.repository.record_synthesis_failure(
        record.id,
        record.synthesis.runtimeAttemptId,
        "provider unavailable",
        fence,
        coordinator.live_validator(actor),
    )
    assert record.status == "failed"
    assert record.synthesis.attemptCount == 2


@pytest.mark.asyncio
async def test_full_coordinator_execution_synthesis_and_completion_are_idempotent(
    app, prepared, monkeypatch
):
    coordinator, actor, graph = prepared
    coordinator.task_leases = app.state.task_leases
    record, fence = claim_fixture(app, prepared)[3:]

    async def specialist(_record, planned):
        return SpecialistResult(
            subtaskId=planned.id,
            summary=f"done:{planned.key}",
            evidence=[f"checkpoint-evidence:{planned.key}"],
            completionCriteriaSatisfied=planned.completionCriteria,
        )

    async def synthesis(_record, nodes):
        return SynthesisResult(
            summary="final synthesis",
            contributingSubtaskIds=[node.subtaskId for node in nodes],
        )

    monkeypatch.setattr(coordinator, "_specialist_call", specialist)
    monkeypatch.setattr(coordinator, "_synthesis_call", synthesis)
    for _ in range(4):
        record = await coordinator.run_once(record.id, fence, actor)
    assert record.status == "completing"
    completed = await coordinator.run_once(record.id, fence, actor)
    assert completed.status == "completed", completed.blockedReason
    assert all(node.status == "succeeded" for node in completed.nodes)
    assert completed.synthesis.inputSubtaskIds == [node.id for node in graph.subtasks]
    with app.state.repository.session_factory() as session:
        event_count = session.scalar(
            select(func.count())
            .select_from(AuditEventRow)
            .where(AuditEventRow.event_type == "coordination.completed")
        )
    assert await coordinator.run_once(record.id, fence, actor) == completed
    with app.state.repository.session_factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEventRow)
                .where(AuditEventRow.event_type == "coordination.completed")
            )
            == event_count
        )


@pytest.mark.asyncio
async def test_worker_reconciles_completion_after_lost_ack(app, prepared, monkeypatch):
    coordinator, actor, _ = prepared
    coordinator.task_leases = app.state.task_leases
    record, fence = claim_fixture(app, prepared)[3:]

    async def specialist(_record, planned):
        return SpecialistResult(
            subtaskId=planned.id,
            summary=f"done:{planned.key}",
            evidence=[f"checkpoint-evidence:{planned.key}"],
            completionCriteriaSatisfied=planned.completionCriteria,
        )

    async def synthesis(_record, nodes):
        return SynthesisResult(
            summary="final synthesis",
            contributingSubtaskIds=[node.subtaskId for node in nodes],
        )

    monkeypatch.setattr(coordinator, "_specialist_call", specialist)
    monkeypatch.setattr(coordinator, "_synthesis_call", synthesis)
    for _ in range(4):
        record = await coordinator.run_once(record.id, fence, actor)
    assert record.status == "completing"

    complete_task = app.state.task_leases.complete_task

    def complete_then_lose_ack(*args, **kwargs):
        complete_task(*args, **kwargs)
        raise RuntimeError("lost completion acknowledgement")

    monkeypatch.setattr(app.state.task_leases, "complete_task", complete_then_lose_ack)
    with pytest.raises(RuntimeError, match="lost completion acknowledgement"):
        await coordinator.run_once(record.id, fence, actor)
    monkeypatch.setattr(app.state.task_leases, "complete_task", complete_task)

    recovered = await coordinator.run_available(fence.worker_id, actor)
    assert recovered is not None
    assert recovered.status == "completed"
    assert app.state.repository.get_task_durable(record.taskId).status == "completed"


@pytest.mark.asyncio
async def test_production_router_executes_dependencies_and_distinct_synthesis(app, prepared):
    coordinator, actor, graph, record, fence = claim_fixture(app, prepared)
    coordinator.task_leases = app.state.task_leases
    before = authority_snapshot(app)
    router = install_coordinator_router(app, coordinator)
    for _ in range(5):
        record = await coordinator.run_once(record.id, fence, actor)
    assert record.status == "completed", record.blockedReason
    assert [name for name, _ in router.coordinator_calls] == ["SpecialistResult"] * 3 + [
        "SynthesisResult"
    ]
    assert router.coordinator_calls[0][1]["dependencyResults"] == {}
    assert set(router.coordinator_calls[2][1]["dependencyResults"]) == {"a", "b"}
    assert all(
        node.provider == "local-fake" and node.model == "fixture-model" for node in record.nodes
    )
    assert authority_snapshot(app) == before
    assert coordinator.runtime.repository.load_run("coord-parent").state == "succeeded"
    with app.state.repository.session_factory() as session:
        assert session.get(TaskLeaseRow, graph.taskId) is None
    for _ in range(3):
        assert await coordinator.run_once(record.id, fence, actor) == record
    assert len(router.coordinator_calls) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "specialist_suspended",
        "manager_suspended",
        "permission_revoked",
        "operator_graph",
        "objective",
        "cancelled",
        "emergency_stop",
    ],
)
async def test_live_refusal_during_inference_never_persists_or_unlocks(app, prepared, change):
    coordinator, actor, graph, record, fence = claim_fixture(app, prepared)
    coordinator.task_leases = app.state.task_leases

    def mutate():
        if change == "cancelled":
            app.state.task_leases.cancel_task(graph.taskId)
            return
        with app.state.repository.session_factory() as session, session.begin():
            if change in {"specialist_suspended", "manager_suspended"}:
                target = (
                    graph.subtasks[0].assignedAgentId
                    if change.startswith("specialist")
                    else app.state.repository.get_task_durable(graph.taskId).teamSelection.managerId
                )
                session.get(IdentityAgentRow, target).lifecycle_state = "suspended"
            elif change == "permission_revoked":
                for row in session.scalars(
                    select(AgentPermissionAssignmentRow).where(
                        AgentPermissionAssignmentRow.agent_id == actor.actor_id
                    )
                ):
                    row.revoked_at = datetime.now(UTC) - timedelta(seconds=1)
            elif change == "operator_graph":
                row = session.get(TaskDecompositionRow, graph.id)
                row.payload = row.payload | {"operatorProtected": True}
            elif change == "objective":
                row = session.get(TaskRow, graph.taskId)
                row.payload = row.payload | {"request": "Changed objective after reservation"}
            else:
                session.get(SystemStateRow, 1).emergency_stop = True

    router = install_coordinator_router(app, coordinator, mutate=mutate)
    result = await coordinator.run_once(record.id, fence, actor)
    assert result.status == "blocked"
    assert all(node.resultDigest is None for node in result.nodes)
    assert len(router.coordinator_calls) == 1
    assert not coordinator.runtime.repository.list_checkpoints(result.nodes[0].runtimeRunId)
    assert result.nodes[2].status == "pending"
    assert app.state.repository.get_task_durable(graph.taskId).status == (
        "cancelled" if change == "cancelled" else "under_review"
    )


@pytest.mark.asyncio
async def test_retry_exhaustion_restart_preserves_independent_success(app, prepared):
    coordinator, actor, _, record, fence = claim_fixture(app, prepared)
    coordinator.task_leases = app.state.task_leases

    def script(stage, payload, count, content):
        return (
            "{malformed"
            if stage == "SpecialistResult" and payload["title"].endswith("a evidence")
            else content
        )

    router = install_coordinator_router(app, coordinator, script=script)
    for _ in range(5):
        record = await coordinator.run_once(record.id, fence, actor)
        if record.nodes[1].status == "succeeded":
            eligible_now(app, record)
    assert record.status == "failed"
    assert record.nodes[0].attemptCount == 3
    assert record.nodes[1].status == "succeeded"
    assert record.nodes[2].status == "blocked"
    assert record.synthesis.attemptCount == 0
    assert len(router.coordinator_calls) == 4
    restarted = CoordinatorService(
        app.state.repository,
        app.state.identity_service,
        app.state.agent_runtime_service,
        router,
        app.state.task_leases,
    )
    assert await restarted.run_once(record.id, fence, actor) == record


@pytest.mark.asyncio
async def test_same_lease_concurrent_dispatch_has_one_model_call(app, prepared):
    import asyncio

    coordinator, actor, _, record, fence = claim_fixture(app, prepared)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def specialist(_record, planned):
        calls.append(planned.id)
        entered.set()
        await release.wait()
        return SpecialistResult(
            subtaskId=planned.id,
            summary="one dispatch",
            evidence=["bounded evidence"],
            completionCriteriaSatisfied=planned.completionCriteria,
        )

    coordinator._specialist_call = specialist
    first = asyncio.create_task(coordinator.run_once(record.id, fence, actor))
    await entered.wait()
    second = await coordinator.run_once(record.id, fence, actor)
    assert second.nodes[0].status == "running"
    release.set()
    result = await first
    assert result.nodes[0].status == "succeeded"
    assert calls == [result.nodes[0].subtaskId]


@pytest.mark.asyncio
async def test_lost_checkpoint_ack_recovery_does_not_redispatch(app, prepared, monkeypatch):
    coordinator, actor, _, record, fence = claim_fixture(app, prepared)
    router = install_coordinator_router(app, coordinator)
    original = coordinator.repository.record_success

    def lose_ack(*args, **kwargs):
        raise KeyboardInterrupt("simulated process death after runtime checkpoint")

    monkeypatch.setattr(coordinator.repository, "record_success", lose_ack)
    with pytest.raises(KeyboardInterrupt):
        await coordinator.run_once(record.id, fence, actor)
    monkeypatch.setattr(coordinator.repository, "record_success", original)
    fence = takeover(app, fence)
    result = await coordinator.run_once(record.id, fence, actor)
    assert result.nodes[0].status == "succeeded"
    assert len(router.coordinator_calls) == 1


@pytest.mark.asyncio
async def test_ambiguous_dispatch_blocks_instead_of_reissuing_inference(app, prepared):
    coordinator, actor, _, record, fence = claim_fixture(app, prepared)
    node = coordinator.claim_ready(record.id, fence, actor)
    node = coordinator.repository.begin_attempt(
        record.id, node.subtaskId, node.runtimeAttemptId, fence, coordinator.live_validator(actor)
    )
    coordinator._start_runtime(
        record,
        run_id=node.runtimeRunId,
        attempt_id=node.runtimeAttemptId,
        agent_id=node.assignedAgentId,
        operation="Interrupted dispatch",
        capabilities=("research.market",),
        fence=fence,
        actor=actor,
    )
    fence = takeover(app, fence)
    router = install_coordinator_router(app, coordinator)
    result = await coordinator.run_once(record.id, fence, actor)
    assert result.blockedReason == "COORDINATION_DISPATCH_OUTCOME_UNKNOWN"
    assert router.coordinator_calls == []


def test_production_acceptance_ports_use_worker_and_real_repositories(app, prepared):
    from app.autonomy.evidence import InferenceIdentity
    from app.autonomy.harness import AutonomyHarness

    coordinator, actor, graph, record, fence = claim_fixture(app, prepared)
    coordinator.task_leases = app.state.task_leases
    router = install_coordinator_router(app, coordinator)
    worker = app.state.autonomous_worker_service
    worker.router, worker.coordinator = router, coordinator
    worker.settings.autonomous_worker_enabled = True
    worker.settings.autonomous_worker_actor_id = actor.actor_id
    worker.settings.model_execution_mode = "local_only"
    app.state.coordinator_service = coordinator
    evidence = AutonomyHarness.run_production(
        app,
        task_id=graph.taskId,
        worker_id=fence.worker_id,
        repo_sha="fixture-test",
        inference=InferenceIdentity(mode="fixture", provider="local-fake", model="fixture-model"),
    )
    assert evidence.verdict == "pass", evidence.failure_reason
    assert all(stage["implementation"] == "production" for stage in evidence.provenance)
    assert evidence.inference.mode == "fixture"
    assert len(router.coordinator_calls) == 4


@pytest.mark.asyncio
async def test_malformed_synthesis_retries_without_repeating_specialists(app, prepared):
    coordinator, actor, _, record, fence = claim_fixture(app, prepared)
    coordinator.task_leases = app.state.task_leases

    def script(stage, payload, count, content):
        if stage == "SynthesisResult" and count == 4:
            return {"summary": "Invented result", "contributingSubtaskIds": ["invented-result"]}
        return content

    router = install_coordinator_router(app, coordinator, script=script)
    for _ in range(4):
        record = await coordinator.run_once(record.id, fence, actor)
    assert record.synthesis.status == "pending"
    assert all(node.status == "succeeded" for node in record.nodes)
    assert record.synthesis.retryEligibleAt
    assert await coordinator.run_once(record.id, fence, actor) == record
    eligible_now(app, record)
    for _ in range(2):
        record = await coordinator.run_once(record.id, fence, actor)
    assert record.status == "completed"
    assert record.synthesis.attemptCount == 2
    assert [stage for stage, _ in router.coordinator_calls].count("SpecialistResult") == 3


@pytest.mark.asyncio
async def test_lost_synthesis_ack_reuses_checkpoint(app, prepared, monkeypatch):
    coordinator, actor, _, record, fence = claim_fixture(app, prepared)
    coordinator.task_leases = app.state.task_leases
    router = install_coordinator_router(app, coordinator)
    for _ in range(3):
        record = await coordinator.run_once(record.id, fence, actor)
    original = coordinator.repository.record_synthesis

    def lose_ack(*args, **kwargs):
        raise KeyboardInterrupt("simulated process death after synthesis checkpoint")

    monkeypatch.setattr(coordinator.repository, "record_synthesis", lose_ack)
    with pytest.raises(KeyboardInterrupt):
        await coordinator.run_once(record.id, fence, actor)
    monkeypatch.setattr(coordinator.repository, "record_synthesis", original)
    fence = takeover(app, fence)
    for _ in range(2):
        record = await coordinator.run_once(record.id, fence, actor)
    assert record.status == "completed"
    assert len(router.coordinator_calls) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["emergency_stop", "permission_revoked", "checkpoint_corrupt"])
async def test_final_completion_revalidates_authority_and_checkpoint(app, prepared, change):
    from app.db.models import AgentRuntimeCheckpointRow

    coordinator, actor, graph, record, fence = claim_fixture(app, prepared)
    coordinator.task_leases = app.state.task_leases
    router = install_coordinator_router(app, coordinator)
    for _ in range(4):
        record = await coordinator.run_once(record.id, fence, actor)
    assert record.status == "completing"
    with app.state.repository.session_factory() as session, session.begin():
        if change == "emergency_stop":
            session.get(SystemStateRow, 1).emergency_stop = True
        elif change == "permission_revoked":
            for row in session.scalars(
                select(AgentPermissionAssignmentRow).where(
                    AgentPermissionAssignmentRow.agent_id == actor.actor_id
                )
            ):
                row.revoked_at = datetime.now(UTC) - timedelta(seconds=1)
        else:
            checkpoint = session.get(
                AgentRuntimeCheckpointRow,
                (record.nodes[0].checkpointId, record.nodes[0].runtimeRunId),
            )
            checkpoint.contract_json = "{}"
    record = await coordinator.run_once(record.id, fence, actor)
    assert record.status == "blocked"
    assert all(node.status == "succeeded" for node in record.nodes)
    assert app.state.repository.get_task_durable(graph.taskId).status != "completed"
    assert len(router.coordinator_calls) == 4
