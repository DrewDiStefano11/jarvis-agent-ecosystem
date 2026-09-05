from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select

from app.agent_runtime.service import AgentRuntimeService
from app.agent_runtime.sqlalchemy_repository import SqlAlchemyAgentRuntimeRepository
from app.core.errors import DomainError
from app.db.models import (
    AgentRuntimeEventRow,
    AgentRuntimeProcessedCommandRow,
    AgentRuntimeRunRow,
    AuditEventRow,
    OutboxEventRow,
    TaskRow,
)
from app.main import create_app
from app.models.agent_runtime import (
    AutonomousExecutionSpecification,
    AutonomousExecutionType,
    CreateAgentRunCommand,
    QueueAgentRunCommand,
)
from tests.agent_runtime_testkit import make_spec, ts
from tests.test_agent_runtime_sql_control_plane import grant_runtime_permissions


@pytest.fixture
def runtime(tmp_path):
    app = create_app(database_url=f"sqlite:///{(tmp_path / 'admission.db').as_posix()}")
    service = AgentRuntimeService(app.state.agent_runtime_repository)
    set_task_status(app, "queued")
    app.state.repository.reload()
    yield app, service
    app.state.engine.dispose()


def create_command(*, autonomous=True, task_id="task-demo", actor="operator-1"):
    spec = make_spec(task_id=task_id)
    if autonomous:
        spec = spec.model_copy(
            update={
                "autonomous_execution": AutonomousExecutionSpecification(
                    execution_type=AutonomousExecutionType.PLANNING_REVIEW,
                    context_assembly_id="assembly-verified",
                )
            }
        )
    return CreateAgentRunCommand(
        specification=spec, command_id="admission-create", timestamp=ts(0), actor_reference=actor
    )


def queue_command(*, actor="operator-1"):
    return QueueAgentRunCommand(
        run_id="run-1",
        command_id="admission-queue",
        expected_run_version=1,
        timestamp=ts(1),
        actor_reference=actor,
    )


def set_task_status(app, state, session=None):
    if session is None:
        with app.state.repository.session_factory.begin() as owned:
            set_task_status(app, state, owned)
        return
    task = session.get(TaskRow, "task-demo")
    task.status = state
    task.payload = {**task.payload, "status": state}
    session.flush()


def counts(app):
    with app.state.repository.session_factory() as session:
        return tuple(
            session.scalar(select(func.count()).select_from(row))
            for row in (
                AgentRuntimeRunRow,
                AgentRuntimeEventRow,
                AgentRuntimeProcessedCommandRow,
                AuditEventRow,
                OutboxEventRow,
            )
        )


@pytest.mark.parametrize("operation", ["create", "queue"])
@pytest.mark.parametrize(
    "status",
    [
        "completed",
        "failed",
        "cancelled",
        "in_progress",
        "under_review",
        "paused",
        "revision_requested",
        "assigned",
        "planning",
        "waiting",
        "waiting_for_approval",
    ],
)
def test_new_autonomous_admission_rejects_durable_unclaimable_task(runtime, operation, status):
    app, service = runtime
    if operation == "queue":
        service.create_run(create_command())
    set_task_status(app, status)
    # The compatibility cache is intentionally stale, just like an old browser.
    assert app.state.repository.tasks["task-demo"].status == "queued"
    before = counts(app)
    with pytest.raises(DomainError) as caught:
        if operation == "create":
            service.create_run(create_command())
        else:
            service.queue_run(queue_command())
    assert caught.value.code == "AUTONOMOUS_TASK_NOT_READY"
    assert caught.value.status_code == 409
    assert counts(app) == before
    if operation == "queue":
        assert service.repository.load_run("run-1").state.value == "created"


@pytest.mark.parametrize("status", ["queued", "retrying"])
def test_admissible_task_is_not_mutated_by_create_or_queue(runtime, status):
    app, service = runtime
    set_task_status(app, status)
    with app.state.repository.session_factory() as session:
        before = session.get(TaskRow, "task-demo")
        preserved = (before.status, before.updated_at, before.payload)
    service.create_run(create_command())
    result = service.queue_run(queue_command())
    assert result.snapshot.state.value == "queued"
    with app.state.repository.session_factory() as session:
        after = session.get(TaskRow, "task-demo")
        assert (after.status, after.updated_at, after.payload) == preserved


def test_accepted_create_and_queue_replay_after_task_completion(runtime):
    app, service = runtime
    created = service.create_run(create_command())
    queued = service.queue_run(queue_command())
    set_task_status(app, "completed")
    before = counts(app)
    assert service.create_run(create_command()).snapshot == created.snapshot
    replay = service.queue_run(queue_command())
    assert replay.idempotent_replay is True and replay.snapshot == queued.snapshot
    assert counts(app) == before


@pytest.mark.parametrize("task_id", ["task-demo", "independent-runtime-task"])
def test_general_runtime_remains_independent_of_task_admission(runtime, task_id):
    app, service = runtime
    set_task_status(app, "completed")
    service.create_run(create_command(autonomous=False, task_id=task_id))
    assert service.queue_run(queue_command()).snapshot.state.value == "queued"


def test_autonomous_run_requires_durable_task(runtime):
    app, service = runtime
    before = counts(app)
    with pytest.raises(DomainError) as caught:
        service.create_run(create_command(task_id="missing-task"))
    assert caught.value.code == "AUTONOMOUS_TASK_NOT_READY"
    assert counts(app) == before


@pytest.mark.parametrize("operation", ["create", "queue"])
def test_exact_admission_replays_across_independent_repository_instances(runtime, operation):
    app, service = runtime
    if operation == "queue":
        service.create_run(create_command())
    peers = [
        AgentRuntimeService(SqlAlchemyAgentRuntimeRepository(app.state.repository.session_factory))
        for _ in range(2)
    ]
    barrier = Barrier(2)

    def compete(index):
        barrier.wait(timeout=5)
        peer = peers[index]
        return (
            peer.create_run(create_command())
            if operation == "create"
            else peer.queue_run(queue_command())
        )

    before = counts(app)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(compete, range(2)))
    assert results[0].snapshot == results[1].snapshot
    assert sum(result.idempotent_replay for result in results) == 1
    after = counts(app)
    assert after[1] == before[1] + 1 and after[2] == before[2] + 1
    assert after[3] == before[3] + 1 and after[4] == before[4] + 1


def test_queue_waits_for_concurrent_terminal_commit_before_checking_task(runtime):
    app, service = runtime
    service.create_run(create_command())
    before = counts(app)
    waiting = Event()

    def started_guard(_conn, _cursor, statement, _parameters, _context, _many):
        if statement == "BEGIN IMMEDIATE":
            waiting.set()

    event.listen(app.state.engine, "before_cursor_execute", started_guard)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with app.state.repository.session_factory.begin() as completing:
                set_task_status(app, "completed", completing)
                attempt = pool.submit(service.queue_run, queue_command())
                assert waiting.wait(timeout=5), "admission must acquire the SQLite write fence"
                assert not attempt.done()
            with pytest.raises(DomainError) as caught:
                attempt.result(timeout=5)
            assert caught.value.code == "AUTONOMOUS_TASK_NOT_READY"
    finally:
        event.remove(app.state.engine, "before_cursor_execute", started_guard)
    assert counts(app) == before


def test_http_stale_client_gets_conflict_and_accepted_retry_stays_idempotent(runtime):
    app, _ = runtime
    actor = grant_runtime_permissions(app, "admission-operator", task_id="task-demo")
    headers = {"X-Jarvis-Actor-Id": actor}
    with TestClient(app) as client:
        created = client.post(
            "/api/agent-runtime/commands",
            json=create_command(actor=actor).model_dump(mode="json"),
            headers=headers,
        )
        assert created.status_code == 200
        set_task_status(app, "completed")
        rejected = client.post(
            "/api/agent-runtime/commands",
            json=queue_command(actor=actor).model_dump(mode="json"),
            headers=headers,
        )
        assert rejected.status_code == 409
        assert rejected.json()["error"]["code"] == "AUTONOMOUS_TASK_NOT_READY"
        replay = client.post(
            "/api/agent-runtime/commands",
            json=create_command(actor=actor).model_dump(mode="json"),
            headers=headers,
        )
        assert replay.status_code == 200 and replay.json()["data"]["idempotent_replay"]
