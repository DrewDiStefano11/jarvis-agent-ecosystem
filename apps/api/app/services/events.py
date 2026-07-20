from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from fastapi import WebSocket

from app.models.domain import EventEnvelope


class EventBroker:
    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()
        self.sequence = 0

    def reset_sequence(self) -> None:
        self.sequence = 0

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
        self.sequence += 1
        event = EventEnvelope(
            eventId=f"evt-{uuid4().hex[:12]}",
            eventType=event_type,
            timestamp=datetime.now(UTC),
            sequenceNumber=self.sequence,
            correlationId=correlation_id,
            taskId=task_id,
            agentId=agent_id,
            payload=payload,
        )
        stale: list[WebSocket] = []
        for client in tuple(self.clients):
            try:
                await client.send_json(event.model_dump(mode="json"))
            except Exception:
                stale.append(client)
        for client in stale:
            self.disconnect(client)
        return event
