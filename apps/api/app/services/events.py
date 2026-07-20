from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import WebSocket

from app.models.domain import EventEnvelope
from app.repositories.sqlalchemy import SqlAlchemyRepository


class EventBroker:
    def __init__(self, repository: SqlAlchemyRepository | None = None) -> None:
        self.clients: set[WebSocket] = set()
        self.repository = repository
        self.sequence = repository.sequence if repository else 0
        self.dispatcher_running = False
        self._dispatcher_task: asyncio.Task[None] | None = None
        self._dispatcher_stop = asyncio.Event()

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

    async def emit(
        self,
        event_type: str,
        payload: dict[str, object],
        task_id: str | None = None,
        agent_id: str | None = None,
        correlation_id: str = "phase-1-demo",
    ) -> EventEnvelope:
        if self.repository:
            self.sequence = self.repository.next_sequence()
        else:
            self.sequence += 1
        event = EventEnvelope(
            eventId=f"evt-{uuid4().hex[:12]}",
            eventType=event_type,
            timestamp=datetime.now(UTC),
            sequenceNumber=self.sequence,
            eventSessionId=self.repository.event_session_id if self.repository else None,
            correlationId=correlation_id,
            taskId=task_id,
            agentId=agent_id,
            payload=payload,
        )
        envelope = event.model_dump(mode="json")
        if self.repository:
            self.repository.enqueue_event(envelope)
        await self._publish(event)
        return event

    async def _publish(self, event: EventEnvelope) -> None:
        stale: list[WebSocket] = []
        for client in tuple(self.clients):
            try:
                await client.send_json(event.model_dump(mode="json"))
            except Exception:
                stale.append(client)
        for client in stale:
            self.disconnect(client)
        if self.repository:
            self.repository.mark_outbox(event.eventId, True)

    async def dispatch_pending(self) -> None:
        if not self.repository:
            return
        for raw in self.repository.pending_outbox():
            try:
                event = EventEnvelope.model_validate(raw)
                await self._publish(event)
            except Exception as exc:
                event_id = str(raw.get("eventId", "invalid"))
                self.repository.mark_outbox(event_id, False, str(exc)[:500])

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
