"""Native transaction fence for explicitly requested planning inference."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select, update

from app.core.errors import DomainError
from app.db.models import AuditEventRow, OutboxEventRow, SystemStateRow, TaskRow
from app.models.domain import EventEnvelope, Task
from app.models.planning_inference import PlanningInferenceIntent


class PlanningRepository:
    def __init__(self, sessions):
        self.sessions = sessions

    def assert_live(
        self,
        task_id,
        expected,
        project,
        validate_live=None,
        *,
        intent: PlanningInferenceIntent | None = None,
    ):
        with self.sessions() as session, session.begin():
            session.execute(
                update(SystemStateRow)
                .where(SystemStateRow.id == 1)
                .values(updated_at=datetime.now(UTC))
            )
            self.assert_live_in_session(session, task_id, expected, project, validate_live)
            if intent is not None:
                if intent.taskId != task_id:
                    raise DomainError("PLANNING_INPUT_CHANGED", "Planning scope changed.", 409)
                prior = session.scalar(
                    select(AuditEventRow.id)
                    .where(
                        AuditEventRow.event_type == "planning_model.dispatch_intent",
                        AuditEventRow.payload["payload"]["planningScope"].as_string()
                        == intent.planningScope,
                    )
                    .limit(1)
                )
                if prior is not None:
                    raise DomainError(
                        "PLANNING_DISPATCH_ALREADY_STARTED",
                        "This planning request already started; reconcile its recorded outcome.",
                        409,
                    )
                self._event(session, intent)

    @staticmethod
    def assert_live_in_session(session, task_id, expected, project, validate_live=None):
        """The caller owns the native write transaction and control-row fence."""
        if session.get(SystemStateRow, 1).emergency_stop:
            raise DomainError("EMERGENCY_STOP_ACTIVE", "Emergency stop blocks planning.", 423)
        row = session.get(TaskRow, task_id)
        if row is None:
            raise DomainError("TASK_NOT_FOUND", "Task not found.", 404)
        task = Task.model_validate(row.payload)
        if task.startedAt or task.status not in {
            "queued",
            "planning",
            "assigned",
            "paused",
            "revision_requested",
        }:
            raise DomainError(
                "PLANNING_EXECUTION_STARTED", "Task lifecycle no longer permits planning.", 409
            )
        if project(task) != expected:
            raise DomainError("PLANNING_INPUT_CHANGED", "Planning inputs changed.", 409)
        if validate_live is not None:
            validate_live(session)

    @staticmethod
    def _event(session, intent):
        state = session.get(SystemStateRow, 1)
        state.current_sequence_number += 1
        now = datetime.now(UTC)
        event_type = "planning_model.dispatch_intent"
        event_id = "evt-" + uuid4().hex
        payload = intent.model_dump(mode="json")
        envelope = EventEnvelope(
            eventId=event_id,
            eventType=event_type,
            timestamp=now,
            sequenceNumber=state.current_sequence_number,
            eventSessionId=state.event_session_id,
            correlationId=intent.planningScope,
            taskId=intent.taskId,
            source="planning-inference",
            payload=payload,
        )
        session.add(
            OutboxEventRow(
                id=event_id,
                event_type=event_type,
                envelope=envelope.model_dump(mode="json"),
                correlation_id=intent.planningScope,
                event_session_id=state.event_session_id,
                sequence_number=state.current_sequence_number,
                status="pending",
                created_at=now,
                publish_attempt_count=0,
            )
        )
        session.add(
            AuditEventRow(
                id="audit-" + uuid4().hex,
                event_type=event_type,
                actor="system",
                task_id=intent.taskId,
                new_state="dispatch_started",
                correlation_id=intent.planningScope,
                sequence_number=state.current_sequence_number,
                event_session_id=state.event_session_id,
                timestamp=now,
                payload={
                    "summary": "Planning model dispatch intent",
                    "payload": payload,
                    "artifactIds": [],
                },
                schema_version="1.0",
            )
        )
