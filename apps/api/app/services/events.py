from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import WebSocket

from app.models.domain import EventEnvelope
from app.repositories.sqlalchemy import IdempotencyResult, SqlAlchemyRepository


class EventBroker:
    def __init__(self, repository: SqlAlchemyRepository | None = None) -> None:
        self.clients: set[WebSocket] = set()
        self.repository = repository
        self.sequence = repository.sequence if repository else 0
        self.dispatcher_running = False
        self._dispatcher_task: asyncio.Task[None] | None = None
        self._dispatcher_stop = asyncio.Event()
        self._dispatch_lock = asyncio.Lock()

    def reset_sequence(self) -> None:
        self.sequence = 0
        if self.repository:
            self.repository.reset_sequence()
            self.repository.persist()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.clients.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self.clients.discard(websocket)

    async def send_snapshot(
        self,
        websocket: WebSocket,
        payload_factory: Callable[[], dict[str, object]],
    ) -> EventEnvelope:
        """Send one synchronization frame without creating a domain event.

        Connection and resynchronization traffic must not consume a durable
        sequence, grow the outbox, or broadcast a caller-triggered snapshot to
        unrelated clients.
        """

        async with self._dispatch_lock:
            if self.repository:
                event_session_id, sequence = self.repository.current_event_cursor()
            else:
                event_session_id, sequence = None, self.sequence
            payload = payload_factory()
            event = EventEnvelope(
                eventId=f"snapshot-{uuid4().hex[:12]}",
                eventType="system.snapshot",
                timestamp=datetime.now(UTC),
                sequenceNumber=sequence,
                eventSessionId=event_session_id,
                correlationId="system-snapshot",
                source="system",
                payload=payload,
            )
            try:
                await websocket.send_json(event.model_dump(mode="json"))
            except Exception:
                self.disconnect(websocket)
                raise
            return event

    async def emit(
        self,
        event_type: str,
        payload: dict[str, object],
        task_id: str | None = None,
        agent_id: str | None = None,
        correlation_id: str = "phase-1-demo",
        source: str = "simulator",
        audit: dict[str, object] | None = None,
        idempotency: IdempotencyResult | None = None,
    ) -> EventEnvelope:
        if not self.repository:
            self.sequence += 1
        event = EventEnvelope(
            eventId=f"evt-{uuid4().hex[:12]}",
            eventType=event_type,
            timestamp=datetime.now(UTC),
            sequenceNumber=self.sequence,
            eventSessionId=None,
            correlationId=correlation_id,
            taskId=task_id,
            agentId=agent_id,
            source=source,
            payload=payload,
        )
        envelope = event.model_dump(mode="json")
        if self.repository:
            if audit:
                envelope["_audit"] = audit
            try:
                committed = self.repository.enqueue_event(envelope, idempotency)
                if committed is not None:
                    envelope = committed
                event = EventEnvelope.model_validate(envelope)
                self.sequence = event.sequenceNumber
            except Exception:
                self.sequence = self.repository.sequence
                raise
        await self._publish(event)
        return event

    async def _publish(self, event: EventEnvelope, outbox_id: str | None = None) -> None:
        async with self._dispatch_lock:
            await self._publish_unlocked(event, outbox_id)

    async def _publish_unlocked(self, event: EventEnvelope, outbox_id: str | None = None) -> None:
        stale: list[WebSocket] = []
        for client in tuple(self.clients):
            try:
                await client.send_json(event.model_dump(mode="json"))
            except Exception:
                stale.append(client)
        for client in stale:
            self.disconnect(client)
        if self.repository:
            self.repository.mark_outbox(outbox_id or event.eventId, True)

    async def dispatch_pending(self) -> None:
        if not self.repository:
            return
        async with self._dispatch_lock:
            blocked_sessions = self.repository.exhausted_outbox_sessions()
            for _ in range(1000):
                dispatched = False
                records = self.repository.pending_outbox_records()
                if not records:
                    return
                for outbox_id, raw in records:
                    try:
                        event = EventEnvelope.model_validate(raw)
                        session_id = event.eventSessionId or "legacy"
                        runtime_ordered = event.eventType.startswith("agent_runtime.")
                        if runtime_ordered and session_id in blocked_sessions:
                            continue
                        await self._publish_unlocked(event, outbox_id)
                        dispatched = True
                    except Exception as exc:
                        if str(raw.get("eventType") or "").startswith("agent_runtime."):
                            session_id = str(raw.get("eventSessionId") or "legacy")
                            blocked_sessions.add(session_id)
                        self.repository.mark_outbox(outbox_id, False, str(exc)[:500])
                if not dispatched:
                    return

    async def start_dispatcher(self, poll_interval_ms: int) -> None:
        if self._dispatcher_task and not self._dispatcher_task.done():
            return
        self._dispatcher_stop.clear()
        self.dispatcher_running = True

        async def run() -> None:
            try:
                while not self._dispatcher_stop.is_set():
                    await self.dispatch_pending()
                    try:
                        await asyncio.wait_for(
                            self._dispatcher_stop.wait(), poll_interval_ms / 1000
                        )
                    except TimeoutError:
                        pass
            finally:
                self.dispatcher_running = False

        self._dispatcher_task = asyncio.create_task(run())

    async def stop_dispatcher(self) -> None:
        self._dispatcher_stop.set()
        if self._dispatcher_task:
            await self._dispatcher_task
            self._dispatcher_task = None
