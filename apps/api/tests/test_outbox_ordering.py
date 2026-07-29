from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db.models import OutboxEventRow
from app.main import create_app
from tests.test_persistence import database_url


def envelope(event_id: str, session: str | None, sequence: int) -> dict[str, object]:
    return {
        "eventId": event_id,
        "schemaVersion": "1.0",
        "eventType": "agent_runtime.test",
        "timestamp": "2026-01-01T00:00:00Z",
        "sequenceNumber": sequence,
        "eventSessionId": session,
        "correlationId": "corr",
        "source": "agent_runtime",
        "payload": {},
    }


def add_outbox(
    app,
    event_id: str,
    session: str | None,
    sequence: int,
    *,
    created_at: datetime,
    status: str = "pending",
    attempts: int = 0,
) -> None:
    with app.state.repository.session_factory() as db, db.begin():
        db.add(
            OutboxEventRow(
                id=event_id,
                event_type="agent_runtime.test",
                envelope=envelope(event_id, session, sequence),
                correlation_id="corr",
                event_session_id=session,
                sequence_number=sequence,
                status=status,
                created_at=created_at,
                published_at=None,
                publish_attempt_count=attempts,
                last_publish_error=None,
            )
        )


def outbox_statuses(app) -> dict[str, tuple[str, int]]:
    with app.state.repository.session_factory() as db:
        return {
            row.id: (row.status, row.publish_attempt_count)
            for row in db.scalars(
                select(OutboxEventRow).where(OutboxEventRow.event_type == "agent_runtime.test")
            )
        }


def test_pending_outbox_records_order_by_session_sequence_and_id(tmp_path) -> None:
    app = create_app(delay_ms=1, database_url=database_url(tmp_path / "outbox-order.db"))
    same = datetime(2026, 1, 1, tzinfo=UTC)
    add_outbox(app, "z-seq-3", "runtime-a", 3, created_at=same)
    add_outbox(app, "a-seq-1", "runtime-a", 1, created_at=same)
    add_outbox(app, "m-seq-2", "runtime-a", 2, created_at=same)
    add_outbox(app, "legacy", "legacy", 1, created_at=same)
    ids = [
        item[0]
        for item in app.state.repository.pending_outbox_records()
        if item[0] in {"z-seq-3", "a-seq-1", "m-seq-2", "legacy"}
    ]
    assert ids == ["legacy", "a-seq-1"]
    app.state.repository.mark_outbox("a-seq-1", True)
    second_ids = [
        item[0]
        for item in app.state.repository.pending_outbox_records()
        if item[0] in {"z-seq-3", "m-seq-2"}
    ]
    assert second_ids == ["m-seq-2"]


@pytest.mark.asyncio
async def test_dispatch_blocks_failed_session_but_not_other_sessions(tmp_path) -> None:
    app = create_app(delay_ms=1, database_url=database_url(tmp_path / "outbox-dispatch.db"))
    same = datetime(2026, 1, 1, tzinfo=UTC)
    add_outbox(app, "a-1", "runtime-a", 1, created_at=same)
    add_outbox(app, "a-2", "runtime-a", 2, created_at=same + timedelta(microseconds=1))
    add_outbox(app, "b-1", "runtime-b", 1, created_at=same + timedelta(microseconds=2))
    published: list[str] = []

    async def publish(event, outbox_id=None):
        if outbox_id == "a-1":
            raise RuntimeError("controlled failure")
        published.append(outbox_id)
        app.state.repository.mark_outbox(outbox_id or event.eventId, True)

    app.state.broker._publish_unlocked = publish
    await app.state.broker.dispatch_pending()
    assert published == ["b-1"]
    assert outbox_statuses(app)["a-1"] == ("failed", 1)
    assert outbox_statuses(app)["a-2"] == ("pending", 0)
    published.clear()

    async def publish_retry(event, outbox_id=None):
        published.append(outbox_id)
        app.state.repository.mark_outbox(outbox_id or event.eventId, True)

    app.state.broker._publish_unlocked = publish_retry
    await app.state.broker.dispatch_pending()
    assert published == ["a-1", "a-2"]


@pytest.mark.asyncio
async def test_exhausted_lower_sequence_blocks_same_session(tmp_path) -> None:
    app = create_app(delay_ms=1, database_url=database_url(tmp_path / "outbox-exhausted.db"))
    same = datetime(2026, 1, 1, tzinfo=UTC)
    add_outbox(
        app,
        "a-1",
        "runtime-a",
        1,
        created_at=same,
        status="failed",
        attempts=app.state.repository.outbox_max_attempts,
    )
    add_outbox(app, "a-2", "runtime-a", 2, created_at=same + timedelta(microseconds=1))
    add_outbox(app, "b-1", "runtime-b", 1, created_at=same + timedelta(microseconds=2))
    published: list[str] = []

    async def publish(event, outbox_id=None):
        published.append(outbox_id)
        app.state.repository.mark_outbox(outbox_id or event.eventId, True)

    app.state.broker._publish_unlocked = publish
    await app.state.broker.dispatch_pending()
    assert published == ["b-1"]
    assert outbox_statuses(app)["a-2"] == ("pending", 0)


def test_runtime_outbox_uses_enqueue_time_and_begin_attempt_sequence_order(tmp_path) -> None:
    from app.models.agent_runtime import (
        BeginAttemptCommand,
        ClaimAgentRunCommand,
        QueueAgentRunCommand,
    )
    from tests.agent_runtime_testkit import create_run, make_spec, ts

    app = create_app(delay_ms=1, database_url=database_url(tmp_path / "runtime-outbox-order.db"))
    with app.state.repository.session_factory() as db:
        before = datetime.now(UTC)
    service = app.state.agent_runtime_service
    create_run(service, specification=make_spec(run_id="run-order"), timestamp=ts(0))
    service.queue_run(
        QueueAgentRunCommand(
            command_type="queue",
            run_id="run-order",
            command_id="cmd-order-queue",
            expected_run_version=1,
            timestamp=ts(1),
            actor_reference="actor",
        )
    )
    service.claim_run(
        ClaimAgentRunCommand(
            command_type="claim",
            run_id="run-order",
            command_id="cmd-order-claim",
            expected_run_version=2,
            timestamp=ts(2),
            actor_reference="actor",
            executor_reference="worker",
        )
    )
    service.begin_attempt(
        BeginAttemptCommand(
            command_type="begin_attempt",
            run_id="run-order",
            command_id="cmd-order-begin",
            expected_run_version=3,
            timestamp=ts(3),
            actor_reference="actor",
            executor_reference="worker",
        )
    )
    with app.state.repository.session_factory() as db:
        rows = list(
            db.scalars(
                select(OutboxEventRow)
                .where(OutboxEventRow.event_type.like("agent_runtime.%"))
                .order_by(OutboxEventRow.sequence_number)
            )
        )
    begin_rows = [
        row
        for row in rows
        if row.envelope["eventId"].startswith("event-") and row.sequence_number in {4, 5}
    ]
    assert [row.sequence_number for row in begin_rows] == [4, 5]
    assert all(row.created_at.replace(tzinfo=UTC) >= before for row in rows)
    assert all(row.created_at.date() != ts(0).date() for row in rows)


@pytest.mark.asyncio
async def test_same_session_clock_skew_still_publishes_sequence_order(tmp_path) -> None:
    app = create_app(delay_ms=1, database_url=database_url(tmp_path / "outbox-skew.db"))
    base = datetime(2026, 1, 1, tzinfo=UTC)
    add_outbox(app, "seq-2", "runtime-skew", 2, created_at=base)
    add_outbox(app, "seq-1", "runtime-skew", 1, created_at=base + timedelta(seconds=5))
    add_outbox(app, "seq-3", "runtime-skew", 3, created_at=base - timedelta(seconds=5))
    published: list[str] = []

    async def publish(event, outbox_id=None):
        published.append(outbox_id)
        app.state.repository.mark_outbox(outbox_id or event.eventId, True)

    app.state.broker._publish_unlocked = publish
    await app.state.broker.dispatch_pending()
    assert published == ["seq-1", "seq-2", "seq-3"]
