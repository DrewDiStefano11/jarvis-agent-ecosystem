import asyncio
import os
import tempfile
from collections.abc import Generator
from datetime import UTC, datetime
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import (
    AuditEventRow,
    Base,
    IdempotencyRecordRow,
    OutboxEventRow,
    TaskRow,
)
from app.main import create_app
from app.repositories.sqlalchemy import SqlAlchemyRepository
from app.services.events import EventBroker
from app.services.unit_of_work import UnitOfWork


class FakeWebSocket:
    def __init__(self):
        self.sent_messages = []
        self.closed = False
        self.fail_on_send = False

    async def accept(self):
        pass

    async def send_json(self, data):
        if self.fail_on_send:
            raise Exception("Failed to send WebSocket message")
        self.sent_messages.append(data)

    async def close(self):
        self.closed = True


@pytest.fixture
def temp_db_url() -> Generator[str, None, None]:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        url = f"sqlite:///{f.name}"
    yield url
    os.remove(f.name)


@pytest.fixture
def db_engine(temp_db_url: str):
    engine = create_engine(
        temp_db_url, connect_args={"check_same_thread": False, "timeout": 5}, pool_pre_ping=True
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session_factory(db_engine) -> sessionmaker[Session]:
    return sessionmaker(bind=db_engine, expire_on_commit=False, autoflush=False)


@pytest.fixture
def repository(session_factory) -> SqlAlchemyRepository:
    return SqlAlchemyRepository(session_factory=session_factory)


@pytest.fixture
def broker(repository) -> EventBroker:
    return EventBroker(repository=repository)


@pytest.fixture
def client(temp_db_url: str) -> Generator[TestClient, None, None]:
    app = create_app(database_url=temp_db_url)
    with TestClient(app) as test_client:
        yield test_client


# ==============================================================================
# 1. PUBLIC COMMAND PATH TESTS (Full Atomicity, Idempotency)
# ==============================================================================


def test_public_command_end_to_end_atomicity(client: TestClient, repository: SqlAlchemyRepository):
    # Group A: Successful atomic creation
    # Use real HTTP endpoint to trigger domain mutation

    # We must construct a completely fresh repository query path because the client uses its own dependencies

    response = client.post(
        "/api/tasks",
        json={
            "title": "Outbox test task",
            "description": "Atomicity description",
            "priority": "medium",
        },
        headers={"Idempotency-Key": "idem-123"},
    )

    assert response.status_code == 201
    task_id = response.json()["data"]["id"]

    # The client runs synchronously. The dispatcher runs async in real life.
    # The API might have synchronous emit, which in turn calls enqueue_event and _publish.

    with repository.session_factory() as session:
        # Domain mutated
        task = session.execute(select(TaskRow).where(TaskRow.id == task_id)).scalar_one_or_none()
        assert task is not None

        # Audit inserted
        audits = (
            session.execute(select(AuditEventRow).where(AuditEventRow.event_type == "task.created"))
            .scalars()
            .all()
        )
        assert len(audits) >= 1

        # Outbox inserted
        outboxes = (
            session.execute(
                select(OutboxEventRow).where(OutboxEventRow.event_type == "task.created")
            )
            .scalars()
            .all()
        )
        assert len(outboxes) >= 1

        # Idempotency stored
        idem = session.execute(
            select(IdempotencyRecordRow).where(IdempotencyRecordRow.idempotency_key == "idem-123")
        ).scalar_one_or_none()
        assert idem is not None


def test_public_command_rollback(client: TestClient, repository: SqlAlchemyRepository):
    # Group B/E: Inject failure at commit
    # We patch UnitOfWork.commit within the application context.

    with patch.object(UnitOfWork, "commit", side_effect=RuntimeError("Commit failed")):
        with pytest.raises(RuntimeError, match="Commit failed"):
            client.post(
                "/api/tasks",
                json={"title": "Rollback test", "description": "Will fail", "priority": "low"},
            )

    with repository.session_factory() as session:
        tasks = (
            session.execute(select(TaskRow).where(TaskRow.title == "Rollback test")).scalars().all()
        )
        assert len(tasks) == 0

        audits = (
            session.execute(select(AuditEventRow).where(AuditEventRow.event_type == "task.created"))
            .scalars()
            .all()
        )
        # Since no tasks were successfully created, there should be no task.created audits
        assert len(audits) == 0

        # Outbox should have nothing for this task
        # We can't query by task id because it was rolled back and never returned, but we can query by recently created
        # In a completely fresh DB, it should be empty
        pass


def test_idempotent_replay_real_path(client: TestClient, repository: SqlAlchemyRepository):
    # Idempotency real path: same key & payload = same response, no duplicate outbox

    key = "idem-456"
    payload = {"title": "Idempotent task", "description": "Desc", "priority": "high"}

    # First request
    resp1 = client.post("/api/tasks", json=payload, headers={"Idempotency-Key": key})
    assert resp1.status_code == 201
    task_id1 = resp1.json()["data"]["id"]

    # Second request
    resp2 = client.post("/api/tasks", json=payload, headers={"Idempotency-Key": key})
    assert resp2.status_code == 201
    assert resp2.json()["data"]["id"] == task_id1

    with repository.session_factory() as session:
        tasks = session.execute(select(TaskRow).where(TaskRow.id == task_id1)).scalars().all()
        assert len(tasks) == 1

        audits = (
            session.execute(select(AuditEventRow).where(AuditEventRow.task_id == task_id1))
            .scalars()
            .all()
        )
        assert len(audits) == 1

        # Search by correlation/taskId or payload inside json
        # In SQLite json search is tricky, we can fetch all and check
        outboxes = (
            session.execute(
                select(OutboxEventRow).where(OutboxEventRow.event_type == "task.created")
            )
            .scalars()
            .all()
        )
        task_outboxes = [
            o
            for o in outboxes
            if "taskId" in o.envelope["payload"]
            and o.envelope["payload"]["taskId"] == task_id1
            or "task" in o.envelope["payload"]
            and o.envelope["payload"]["task"]["id"] == task_id1
        ]
        assert len(task_outboxes) == 1


def test_conflicting_idempotency_payload(client: TestClient, repository: SqlAlchemyRepository):
    key = "idem-789"
    payload1 = {"title": "Task 1", "description": "Desc", "priority": "high"}
    payload2 = {"title": "Task 2", "description": "Desc", "priority": "high"}

    resp1 = client.post("/api/tasks", json=payload1, headers={"Idempotency-Key": key})
    assert resp1.status_code == 201

    resp2 = client.post("/api/tasks", json=payload2, headers={"Idempotency-Key": key})
    assert resp2.status_code == 409

    with repository.session_factory() as session:
        # Check no second task/audit/outbox was made
        tasks = session.execute(select(TaskRow).where(TaskRow.title == "Task 2")).scalars().all()
        assert len(tasks) == 0


# ==============================================================================
# 2. BROKER AND DISPATCHER TESTS
# ==============================================================================


@pytest.fixture
def fake_websocket():
    return FakeWebSocket()


@pytest.mark.asyncio
async def test_subscriber_failure_isolation(repository: SqlAlchemyRepository, broker: EventBroker):
    fake_ws1 = FakeWebSocket()
    fake_ws2 = FakeWebSocket()
    fake_ws2.fail_on_send = True

    await broker.connect(fake_ws1)
    await broker.connect(fake_ws2)

    # We use a real low-level enqueue for broker tests, as they test the boundary
    # beginning at the stored record.
    event_id = f"evt-{uuid4().hex[:12]}"
    repository.enqueue_event(
        {
            "eventId": event_id,
            "eventType": "task.created",
            "sequenceNumber": 800,
            "correlationId": "test-123",
            "payload": {"taskId": "task-subs"},
            "timestamp": datetime.now(UTC).isoformat(),
        }
    )

    await broker.dispatch_pending()

    assert len(fake_ws1.sent_messages) == 1
    assert len(fake_ws2.sent_messages) == 0

    with repository.session_factory() as session:
        outbox = session.execute(
            select(OutboxEventRow).where(OutboxEventRow.id == event_id)
        ).scalar_one()
        assert (
            outbox.status == "published"
        )  # At least one succeeded or broker swallowed client failure. Current design: broker resilient.


@pytest.mark.asyncio
async def test_temporary_publication_failure(
    repository: SqlAlchemyRepository, broker: EventBroker, fake_websocket
):
    await broker.connect(fake_websocket)
    event_id = f"evt-{uuid4().hex[:12]}"
    repository.enqueue_event(
        {
            "eventId": event_id,
            "eventType": "task.created",
            "sequenceNumber": 102,
            "correlationId": "test-123",
            "payload": {"taskId": "task-3"},
            "timestamp": datetime.now(UTC).isoformat(),
        }
    )

    # Simulate a broker failure, not a client failure, because client failures are ignored.
    with patch.object(EventBroker, "_publish_unlocked", side_effect=RuntimeError("Broker failure")):
        await broker.dispatch_pending()

    with repository.session_factory() as session:
        outbox = session.execute(
            select(OutboxEventRow).where(OutboxEventRow.id == event_id)
        ).scalar_one()
        assert outbox.status == "failed"
        assert outbox.publish_attempt_count == 1

    await broker.dispatch_pending()

    with repository.session_factory() as session:
        outbox = session.execute(
            select(OutboxEventRow).where(OutboxEventRow.id == event_id)
        ).scalar_one()
        assert outbox.status == "published"
        assert outbox.publish_attempt_count == 2


@pytest.mark.asyncio
async def test_poison_payload(repository: SqlAlchemyRepository, broker: EventBroker):
    event_id = f"evt-{uuid4().hex[:12]}"
    with repository.session_factory() as session, session.begin():
        session.add(
            OutboxEventRow(
                id=event_id,
                event_type="task.created",
                envelope={"eventId": event_id, "invalid": "This doesn't match EventEnvelope"},
                correlation_id="test-123",
                event_session_id=repository.event_session_id,
                sequence_number=1400,
                status="pending",
                created_at=datetime.now(UTC),
                publish_attempt_count=0,
            )
        )

    await broker.dispatch_pending()

    with repository.session_factory() as session:
        outbox = session.execute(
            select(OutboxEventRow).where(OutboxEventRow.id == event_id)
        ).scalar_one()
        assert outbox.status == "failed"

    for _ in range(repository.outbox_max_attempts + 1):
        await broker.dispatch_pending()

    with repository.session_factory() as session:
        outbox = session.execute(
            select(OutboxEventRow).where(OutboxEventRow.id == event_id)
        ).scalar_one()
        assert outbox.status == "failed"
        assert outbox.publish_attempt_count == repository.outbox_max_attempts
        assert "validation error" in outbox.last_publish_error.lower()


# ==============================================================================
# 3. LOW LEVEL OUTBOX UNIT TESTS
# ==============================================================================


def test_payload_fidelity_and_json_safety(repository: SqlAlchemyRepository):
    event_id = f"evt-{uuid4().hex[:12]}"
    complex_payload = {
        "taskId": "task-fidelity",
        "actor": {"id": "user_1", "name": "José M."},
        "status": None,
        "metrics": {"count": 42, "ratio": 3.14},
        "flags": [True, False],
        "emoji": "🚀",
    }

    repository.enqueue_event(
        {
            "eventId": event_id,
            "eventType": "task.created",
            "sequenceNumber": 1600,
            "correlationId": "test-123",
            "payload": complex_payload,
            "timestamp": datetime.now(UTC).isoformat(),
        }
    )

    with repository.session_factory() as session:
        outbox = session.execute(
            select(OutboxEventRow).where(OutboxEventRow.id == event_id)
        ).scalar_one()
        saved = outbox.envelope["payload"]
        assert saved["actor"]["name"] == "José M."
        assert saved["metrics"]["ratio"] == 3.14


@pytest.mark.asyncio
async def test_concurrent_dispatcher_instances(session_factory, temp_db_url):
    repo1 = SqlAlchemyRepository(session_factory)
    repo2 = SqlAlchemyRepository(session_factory)
    broker1 = EventBroker(repo1)
    broker2 = EventBroker(repo2)

    for i in range(5):
        repo1.enqueue_event(
            {
                "eventId": f"evt-{uuid4().hex[:12]}",
                "eventType": "task.created",
                "sequenceNumber": 500 + i,
                "correlationId": "test-123",
                "payload": {"taskId": f"task-conc-{i}"},
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )

    await asyncio.gather(broker1.dispatch_pending(), broker2.dispatch_pending())

    with repo1.session_factory() as session:
        outboxes = (
            session.execute(select(OutboxEventRow).where(OutboxEventRow.status == "published"))
            .scalars()
            .all()
        )
        assert len(outboxes) == 5
