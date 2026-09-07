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
from app.models.coordination import execution_ready_keys
from tests.test_agent_runtime_sql_control_plane import grant_runtime_permissions
from tests.test_autonomous_worker import queue_autonomous_runtime
from tests.test_task_decomposition import app as decomposition_app
from tests.test_task_decomposition import node, proposal, service, setup


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
    return coordinator, actor, graph, record, fence


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
