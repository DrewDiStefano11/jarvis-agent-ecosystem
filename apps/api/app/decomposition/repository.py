"""One bounded aggregate per version, serialized with the existing control plane."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select, update

from app.core.errors import DomainError
from app.db.models import (
    AuditEventRow,
    OutboxEventRow,
    SystemStateRow,
    TaskDecompositionRow,
    TaskRow,
)
from app.models.decomposition import DecompositionRecord
from app.models.domain import EventEnvelope, Task


def task_input(task: Task) -> dict:
    return {
        "title": task.title.strip(),
        "description": task.description.strip(),
        "request": task.request.strip(),
        "projectId": task.projectId,
        "correctionOfTaskId": task.correctionOfTaskId,
        "teamSelection": task.teamSelection.model_dump(
            mode="json", exclude={"createdAt", "updatedAt"}
        )
        if task.teamSelection
        else None,
    }


class DecompositionRepository:
    def __init__(self, sessions):
        self.sessions = sessions
        self.session_factory = sessions

    def get_task_durable(self, task_id):
        with self.sessions() as session:
            row = session.get(TaskRow, task_id)
            if row is None:
                raise DomainError("TASK_NOT_FOUND", "Task not found.", 404)
            return Task.model_validate(row.payload)

    def current(self, task_id: str) -> DecompositionRecord | None:
        with self.sessions() as session:
            row = session.scalar(
                select(TaskDecompositionRow).where(TaskDecompositionRow.active_task_id == task_id)
            )
            return DecompositionRecord.model_validate(row.payload) if row else None

    def history(self, task_id: str) -> list[DecompositionRecord]:
        with self.sessions() as session:
            return [
                DecompositionRecord.model_validate(row.payload)
                for row in session.scalars(
                    select(TaskDecompositionRow)
                    .where(TaskDecompositionRow.task_id == task_id)
                    .order_by(TaskDecompositionRow.version)
                )
            ]

    def persist(
        self, record: DecompositionRecord, task: Task, validate_live
    ) -> DecompositionRecord:
        with self.sessions() as session, session.begin():
            # Acquire the same database write fence as task/context/outbox commits,
            # before reading anything (also avoids SQLite read-to-write upgrade races).
            session.execute(
                update(SystemStateRow)
                .where(SystemStateRow.id == 1)
                .values(updated_at=datetime.now(UTC))
            )
            current_task = Task.model_validate(session.get(TaskRow, task.id).payload)
            if task_input(current_task) != task_input(task):
                raise DomainError(
                    "DECOMPOSITION_INPUT_CHANGED",
                    "Task or selected team changed; prepare planning again.",
                    409,
                )
            validate_live(session)
            row = session.scalar(
                select(TaskDecompositionRow).where(TaskDecompositionRow.active_task_id == task.id)
            )
            previous = DecompositionRecord.model_validate(row.payload) if row else None
            if previous and previous.inputFingerprint == record.inputFingerprint:
                return previous
            if current_task.startedAt or current_task.status not in {
                "queued",
                "planning",
                "assigned",
                "paused",
                "revision_requested",
            }:
                raise DomainError(
                    "DECOMPOSITION_EXECUTION_STARTED",
                    "The task lifecycle no longer permits replacing planned work.",
                    409,
                )
            if previous and previous.operatorProtected:
                raise DomainError(
                    "DECOMPOSITION_OPERATOR_PROTECTED",
                    "Explicit operator edits require operator reconciliation.",
                    409,
                )
            record.version = previous.version + 1 if previous else 1
            if row and previous:
                previous.status = "superseded"
                previous.supersededBy = record.id
                row.active_task_id = None
                row.payload = previous.model_dump(mode="json")
                session.flush()
                self._event(session, previous, "superseded")
            session.add(
                TaskDecompositionRow(
                    id=record.id,
                    task_id=task.id,
                    active_task_id=task.id,
                    version=record.version,
                    input_fingerprint=record.inputFingerprint,
                    payload=record.model_dump(mode="json"),
                )
            )
            session.flush()
            self._event(session, record, "completed" if record.status == "ready" else "blocked")
            return record

    @staticmethod
    def _event(session, record, transition):
        state = session.get(SystemStateRow, 1)
        state.current_sequence_number += 1
        now = datetime.now(UTC)
        event_type = f"task_decomposition.{transition}"
        event_id = f"evt-{uuid4().hex}"
        payload = {
            "taskId": record.taskId,
            "decompositionId": record.id,
            "version": record.version,
            "status": record.status,
        }
        envelope = EventEnvelope(
            eventId=event_id,
            eventType=event_type,
            timestamp=now,
            sequenceNumber=state.current_sequence_number,
            eventSessionId=state.event_session_id,
            correlationId=record.id,
            taskId=record.taskId,
            source="task-decomposition",
            payload=payload,
        )
        session.add(
            OutboxEventRow(
                id=event_id,
                event_type=event_type,
                envelope=envelope.model_dump(mode="json"),
                correlation_id=record.id,
                event_session_id=state.event_session_id,
                sequence_number=state.current_sequence_number,
                status="pending",
                created_at=now,
                publish_attempt_count=0,
            )
        )
        session.add(
            AuditEventRow(
                id=f"audit-{uuid4().hex}",
                event_type=event_type,
                actor="system",
                task_id=record.taskId,
                new_state=record.status,
                correlation_id=record.id,
                sequence_number=state.current_sequence_number,
                event_session_id=state.event_session_id,
                timestamp=now,
                payload={
                    "summary": f"Planned work v{record.version}: {record.status}",
                    "payload": payload,
                    "artifactIds": [],
                },
                schema_version="1.0",
            )
        )
