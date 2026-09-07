"""Bounded coordination aggregate serialized by the control-plane database fence."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.agent_runtime.repository import RuntimeExecutionFence
from app.core.errors import DomainError
from app.db.models import (
    AgentRuntimeRunRow,
    AuditEventRow,
    CoordinationRow,
    OutboxEventRow,
    SystemStateRow,
    TaskDecompositionRow,
    TaskRow,
)
from app.decomposition.service import fingerprint
from app.models.agent_runtime import AgentRunSnapshot
from app.models.coordination import (
    CoordinatedSubtask,
    CoordinationRecord,
    execution_ready_keys,
)
from app.models.decomposition import DecompositionRecord
from app.models.domain import EventEnvelope, Task
from app.repositories.task_leases import TaskLeaseRepository

LiveValidator = Callable[[Session, Task, DecompositionRecord, AgentRunSnapshot], frozenset[str]]


def coordination_id(*parts: str) -> str:
    return "coord-" + sha256("\0".join(parts).encode()).hexdigest()[:48]


class CoordinationRepository:
    def __init__(self, sessions):
        self.sessions = sessions

    @contextmanager
    def _write(self) -> Iterator[Session]:
        with self.sessions() as session, session.begin():
            # Same first-write fence as decomposition/context/task/outbox commits.
            # SQLite serializes writers; other databases lock this row until commit.
            session.execute(
                update(SystemStateRow)
                .where(SystemStateRow.id == 1)
                .values(updated_at=datetime.now(UTC))
            )
            yield session

    def current(self, task_id: str) -> CoordinationRecord | None:
        with self.sessions() as session:
            row = session.scalar(
                select(CoordinationRow)
                .join(
                    TaskDecompositionRow,
                    TaskDecompositionRow.id == CoordinationRow.decomposition_id,
                )
                .where(TaskDecompositionRow.active_task_id == task_id)
            )
            return CoordinationRecord.model_validate(row.payload) if row else None

    @staticmethod
    def _live(session, task_id, decomposition_id, run_id, fence, validate_live):
        state = session.get(SystemStateRow, 1)
        if state.emergency_stop:
            raise DomainError("EMERGENCY_STOP_ACTIVE", "Coordination is stopped.", 423)
        task_row = session.get(TaskRow, task_id)
        graph_row = session.get(TaskDecompositionRow, decomposition_id)
        run_row = session.get(AgentRuntimeRunRow, run_id)
        if task_row is None or graph_row is None or run_row is None:
            raise DomainError("COORDINATION_INPUT_MISSING", "Coordination input is missing.", 409)
        task = Task.model_validate(task_row.payload)
        graph = DecompositionRecord.model_validate(graph_row.payload)
        run = AgentRunSnapshot.model_validate_json(run_row.snapshot_json)
        if (
            graph_row.active_task_id != task_id
            or graph.taskId != task_id
            or graph.status != "ready"
            or run.specification.task_id != task_id
        ):
            raise DomainError("COORDINATION_STALE_PLAN", "Use the authoritative ready graph.", 409)
        if task.status not in {"queued", "retrying", "in_progress"} or run.state not in {
            "queued",
            "claimed",
            "starting",
            "running",
        }:
            raise DomainError(
                "COORDINATION_LIFECYCLE_BLOCKED", "Task or runtime is not executable.", 409
            )
        if fence is not None:
            if fence.task_id != task_id or task.status != "in_progress":
                raise DomainError(
                    "TASK_LEASE_LOST", "Coordination requires the current task lease.", 409
                )
            TaskLeaseRepository._require_lease(
                session, task_id, fence.worker_id, fence.lease_token, datetime.now(UTC)
            )
        return task, graph, run, validate_live(session, task, graph, run)

    def initialize(self, task_id, decomposition_id, run_id, validate_live: LiveValidator):
        with self._write() as session:
            _, graph, _, _ = self._live(
                session, task_id, decomposition_id, run_id, None, validate_live
            )
            existing = session.scalar(
                select(CoordinationRow).where(CoordinationRow.decomposition_id == decomposition_id)
            )
            if existing:
                if existing.runtime_run_id != run_id:
                    raise DomainError(
                        "COORDINATION_ALREADY_OWNED",
                        "This graph already has a canonical runtime.",
                        409,
                    )
                return CoordinationRecord.model_validate(existing.payload)
            now = datetime.now(UTC)
            record = CoordinationRecord(
                id=coordination_id(task_id, decomposition_id),
                taskId=task_id,
                decompositionId=decomposition_id,
                decompositionHash=fingerprint(graph.model_dump(mode="json")),
                runtimeRunId=run_id,
                createdAt=now,
                updatedAt=now,
                nodes=[
                    CoordinatedSubtask(
                        subtaskId=node.id,
                        key=node.key,
                        assignedAgentId=node.assignedAgentId,
                        runtimeRunId=coordination_id(decomposition_id, node.id),
                    )
                    for node in graph.subtasks
                ],
            )
            session.add(
                CoordinationRow(
                    id=record.id,
                    task_id=task_id,
                    decomposition_id=decomposition_id,
                    runtime_run_id=run_id,
                    payload=record.model_dump(mode="json"),
                )
            )
            self._event(session, record, "started")
            return record

    def claim_ready(
        self, record_id: str, fence: RuntimeExecutionFence, validate_live: LiveValidator
    ) -> CoordinatedSubtask | None:
        """Reserve one node under the existing one-execution worker bound.

        Preparation survives a process death. A retrying caller never obtains a
        second reservation, including when it presents the same task lease.
        Phase B resumes this reference through authorized runtime commands.
        """
        with self._write() as session:
            row = session.get(CoordinationRow, record_id)
            if row is None:
                raise DomainError("COORDINATION_NOT_FOUND", "Coordination not found.", 404)
            record = CoordinationRecord.model_validate(row.payload)
            _, graph, _, eligible = self._live(
                session,
                record.taskId,
                record.decompositionId,
                record.runtimeRunId,
                fence,
                validate_live,
            )
            if record.decompositionHash != fingerprint(graph.model_dump(mode="json")):
                raise DomainError(
                    "COORDINATION_PLAN_CORRECTED",
                    "Operator plan changes require reconciliation.",
                    409,
                )
            owned = frozenset(n.key for n in record.nodes if n.status == "claimed")
            if owned:
                return None
            ready = execution_ready_keys(
                graph, owned=owned, eligible_agents=eligible, execution_permitted=True
            )
            if not ready:
                return None
            node = next(n for n in record.nodes if n.key == ready[0])
            node.status = "claimed"
            node.runtimeAttemptId = coordination_id(node.runtimeRunId, "attempt-1")
            record.updatedAt = datetime.now(UTC)
            row.payload = record.model_dump(mode="json")
            self._event(session, record, "subtask_claimed", node)
            return node

    @staticmethod
    def _event(session, record, transition, node=None):
        state = session.get(SystemStateRow, 1)
        state.current_sequence_number += 1
        now = datetime.now(UTC)
        event_id = "evt-" + uuid4().hex
        event_type = "coordination." + transition
        payload = {
            "taskId": record.taskId,
            "coordinationId": record.id,
            "decompositionId": record.decompositionId,
            "runtimeRunId": record.runtimeRunId,
            "status": record.status,
        }
        if node:
            payload.update(
                subtaskId=node.subtaskId,
                agentId=node.assignedAgentId,
                runtimeAttemptId=node.runtimeAttemptId,
            )
        envelope = EventEnvelope(
            eventId=event_id,
            eventType=event_type,
            timestamp=now,
            sequenceNumber=state.current_sequence_number,
            eventSessionId=state.event_session_id,
            correlationId=record.id,
            taskId=record.taskId,
            source="coordination",
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
                id="audit-" + uuid4().hex,
                event_type=event_type,
                actor="system",
                task_id=record.taskId,
                new_state=record.status,
                correlation_id=record.id,
                sequence_number=state.current_sequence_number,
                event_session_id=state.event_session_id,
                timestamp=now,
                schema_version="1.0",
                payload={
                    "summary": "Coordination " + transition,
                    "payload": payload,
                    "artifactIds": [],
                },
            )
        )
