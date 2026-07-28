"""Regression tests for the follow-up runtime persistence review findings."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent_runtime.service import AgentRuntimeService
from app.agent_runtime.sqlalchemy_repository import SqlAlchemyAgentRuntimeRepository
from app.db.models import AgentRuntimeAttemptRow, AgentRuntimeRunRow, AuditEventRow, OutboxEventRow
from app.db.session import create_database_engine, create_session_factory
from app.models.agent_runtime import BeginAttemptCommand
from app.models.domain import EventEnvelope
from app.repositories.sqlalchemy import SqlAlchemyRepository
from app.services.events import EventBroker
from tests.agent_runtime_testkit import (
    SequenceFactory,
    claim_run,
    create_run,
    make_spec,
    queue_run,
    ts,
)


def _database_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def _migrate_head(url: str) -> None:
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")


def _runtime_service(session_factory) -> AgentRuntimeService:
    return AgentRuntimeService(
        SqlAlchemyAgentRuntimeRepository(session_factory),
        utc_clock=lambda: ts(10_000),
        run_id_factory=SequenceFactory("run"),
        attempt_id_factory=SequenceFactory("attempt"),
        event_id_factory=SequenceFactory("event"),
        checkpoint_id_factory=SequenceFactory("checkpoint"),
    )


def _prepare_attempt(
    service: AgentRuntimeService,
    *,
    run_id: str,
    attempt_id: str,
    command_prefix: str,
) -> None:
    create_run(
        service,
        specification=make_spec(run_id=run_id),
        command_id=f"{command_prefix}-create",
    )
    queue_run(
        service,
        run_id,
        expected_run_version=1,
        command_id=f"{command_prefix}-queue",
    )
    claim_run(
        service,
        run_id,
        expected_run_version=2,
        command_id=f"{command_prefix}-claim",
    )
    service.begin_attempt(
        BeginAttemptCommand(
            run_id=run_id,
            command_id=f"{command_prefix}-begin",
            expected_run_version=3,
            timestamp=ts(3),
            actor_reference="worker-1",
            executor_reference="worker-1",
            attempt_id=attempt_id,
            source_metadata={"source": "test"},
        )
    )


@pytest.fixture
def runtime_database(tmp_path: Path):
    url = _database_url(tmp_path / "runtime-follow-up.db")
    _migrate_head(url)
    engine = create_database_engine(url)
    return engine, create_session_factory(engine)


def test_run_scoped_attempt_identity_persists_and_reloads_equal_ids(runtime_database) -> None:
    engine, session_factory = runtime_database
    service = _runtime_service(session_factory)

    _prepare_attempt(service, run_id="run-a", attempt_id="attempt-shared", command_prefix="a")
    _prepare_attempt(service, run_id="run-b", attempt_id="attempt-shared", command_prefix="b")

    with Session(engine) as session:
        rows = session.scalars(
            select(AgentRuntimeAttemptRow)
            .where(AgentRuntimeAttemptRow.attempt_id == "attempt-shared")
            .order_by(AgentRuntimeAttemptRow.run_id)
        ).all()
    assert [(row.run_id, row.attempt_id) for row in rows] == [
        ("run-a", "attempt-shared"),
        ("run-b", "attempt-shared"),
    ]

    restarted_repository = SqlAlchemyAgentRuntimeRepository(session_factory)
    assert [item.attempt_id for item in restarted_repository.load_attempt_history("run-a")] == [
        "attempt-shared"
    ]
    assert [item.attempt_id for item in restarted_repository.load_attempt_history("run-b")] == [
        "attempt-shared"
    ]
    assert restarted_repository.integrity_check("run-a") is True
    assert restarted_repository.integrity_check("run-b") is True


def test_run_scoped_attempt_delete_cascades_only_its_parent(runtime_database) -> None:
    engine, session_factory = runtime_database
    service = _runtime_service(session_factory)
    _prepare_attempt(service, run_id="run-a", attempt_id="attempt-shared", command_prefix="a")
    _prepare_attempt(service, run_id="run-b", attempt_id="attempt-shared", command_prefix="b")

    with Session(engine) as session, session.begin():
        run_a = session.get(AgentRuntimeRunRow, "run-a")
        assert run_a is not None
        session.delete(run_a)

    with Session(engine) as session:
        remaining = session.scalars(select(AgentRuntimeAttemptRow)).all()
    assert [(row.run_id, row.attempt_id) for row in remaining] == [("run-b", "attempt-shared")]


class _RecordingWebSocket:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_json(self, message: dict) -> None:
        self.messages.append(message)


def test_runtime_outbox_envelope_is_dispatcher_compatible(runtime_database) -> None:
    engine, session_factory = runtime_database
    durable_repository = SqlAlchemyRepository(session_factory)
    runtime_service = _runtime_service(session_factory)
    created = create_run(
        runtime_service,
        specification=make_spec(run_id="run-dispatch"),
        command_id="command-dispatch",
    )

    with Session(engine) as session:
        outbox = session.scalar(
            select(OutboxEventRow).where(OutboxEventRow.id == created.events[0].event_id)
        )
        assert outbox is not None
        validated = EventEnvelope.model_validate(outbox.envelope)
        assert validated.eventType == "agent_runtime.run_created"
        assert validated.source == "agent_runtime"
        assert validated.payload["runtimeEvent"]["run_id"] == "run-dispatch"
        assert validated.payload["runtimeEvent"]["event_id"] == validated.eventId

    websocket = _RecordingWebSocket()
    broker = EventBroker(durable_repository)
    broker.clients.add(websocket)  # type: ignore[arg-type]
    asyncio.run(broker.dispatch_pending())

    with Session(engine) as session:
        dispatched = session.get(OutboxEventRow, created.events[0].event_id)
        assert dispatched is not None
        assert dispatched.status == "published"
        assert dispatched.publish_attempt_count == 1
        assert dispatched.last_publish_error is None
        assert dispatched.published_at is not None
    assert len(websocket.messages) == 1
    assert EventEnvelope.model_validate(websocket.messages[0]).eventId == created.events[0].event_id


def test_runtime_outbox_dispatch_survives_repository_restart(runtime_database) -> None:
    engine, session_factory = runtime_database
    runtime_service = _runtime_service(session_factory)
    created = create_run(
        runtime_service,
        specification=make_spec(run_id="run-restart-dispatch"),
        command_id="command-restart-dispatch",
    )

    restarted_repository = SqlAlchemyRepository(session_factory)
    asyncio.run(EventBroker(restarted_repository).dispatch_pending())

    with Session(engine) as session:
        row = session.get(OutboxEventRow, created.events[0].event_id)
        assert row is not None
        assert row.status == "published"
        assert row.publish_attempt_count == 1
        assert row.last_publish_error is None


def test_runtime_audit_identity_delimits_run_and_command_ids(runtime_database) -> None:
    engine, session_factory = runtime_database
    service = _runtime_service(session_factory)
    assert "ab" + "c" == "a" + "bc"

    create_run(service, specification=make_spec(run_id="ab"), command_id="c")
    create_run(service, specification=make_spec(run_id="a"), command_id="bc")

    with Session(engine) as session:
        rows = session.scalars(
            select(AuditEventRow)
            .where(AuditEventRow.event_type == "agent_runtime.command")
            .order_by(AuditEventRow.id)
        ).all()
        runtime_run_count = session.scalar(select(func.count()).select_from(AgentRuntimeRunRow))

    assert runtime_run_count == 2
    assert len(rows) == 2
    assert len({row.id for row in rows}) == 2
    assert {(row.payload["runId"], row.payload["commandId"]) for row in rows} == {
        ("ab", "c"),
        ("a", "bc"),
    }
    assert all(row.id.startswith("runtime-") and len(row.id) == 72 for row in rows)
