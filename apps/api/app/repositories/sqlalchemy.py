from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import DomainError
from app.db.models import (
    AgentRow,
    ApprovalRow,
    ArtifactRow,
    AuditEventRow,
    DepartmentRow,
    IdempotencyRecordRow,
    NotificationRow,
    OutboxEventRow,
    SystemStateRow,
    TaskAgentRow,
    TaskAttemptRow,
    TaskBlockerRow,
    TaskDependencyRow,
    TaskLeaseRow,
    TaskRow,
    WorkflowCheckpointRow,
    WorkflowRunRow,
)
from app.models.domain import Agent, Approval, Artifact, AuditEvent, Department, Notification, Task
from app.services.seed import build_seed
from app.services.unit_of_work import UnitOfWork

SEED_VERSION = "2.0"


@dataclass(frozen=True)
class IdempotencyClaim:
    response: tuple[int, dict[str, Any]] | None
    owned: bool
    lease_expires_at: datetime | None = None


@dataclass(frozen=True)
class IdempotencyResult:
    key: str
    command: str
    payload: Any
    status: int
    body: dict[str, Any]
    lease_expires_at: datetime
    resource_id: str | None = None


class SqlAlchemyRepository:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        idempotency_lease_seconds: int = 30,
        outbox_max_attempts: int = 10,
    ) -> None:
        self.session_factory = session_factory
        self.idempotency_lease_seconds = idempotency_lease_seconds
        self.outbox_max_attempts = outbox_max_attempts
        self._pending_checkpoint: dict[str, Any] | None = None
        self._pending_workflow_run: dict[str, Any] | None = None
        self._audit_session_ids: dict[str, str] = {}
        self._load_or_seed()

    def _load_or_seed(self) -> None:
        with self.session_factory() as session:
            has_seed = session.scalar(select(func.count()).select_from(DepartmentRow)) or 0
        if not has_seed:
            self._load_seed_memory()
            self._system = SystemStateRow(
                id=1,
                emergency_stop=False,
                simulator_status="idle",
                event_session_id=f"session-{uuid4().hex}",
                current_sequence_number=0,
                seed_data_version=SEED_VERSION,
                last_successful_startup=datetime.now(UTC),
                startup_was_clean=True,
                recovery_status="none",
                updated_at=datetime.now(UTC),
            )
            self.persist()
        else:
            self.reload()

    def _load_seed_memory(self) -> None:
        seed = build_seed()
        self.departments = {x["id"]: Department.model_validate(x) for x in seed["departments"]}
        self.agents = {x["id"]: Agent.model_validate(x) for x in seed["agents"]}
        self.tasks = {x["id"]: Task.model_validate(x) for x in seed["tasks"]}
        self.approvals = {x["id"]: Approval.model_validate(x) for x in seed["approvals"]}
        self.artifacts = {x["id"]: Artifact.model_validate(x) for x in seed["artifacts"]}
        self.notifications = {
            x["id"]: Notification.model_validate(x) for x in seed["notifications"]
        }
        self.audit = [AuditEvent.model_validate(x) for x in seed["audit"]]
        self._audit_session_ids = {item.id: "seed" for item in self.audit}

    def reload(self) -> None:
        self._pending_checkpoint = None
        self._pending_workflow_run = None
        with self.session_factory() as session:
            self.departments = {
                row.id: Department.model_validate(row.payload)
                for row in session.scalars(select(DepartmentRow))
            }
            self.agents = {
                row.id: Agent.model_validate(row.payload)
                for row in session.scalars(select(AgentRow))
            }
            self.tasks = {
                row.id: Task.model_validate(row.payload) for row in session.scalars(select(TaskRow))
            }
            self.approvals = {
                row.id: Approval.model_validate(row.payload)
                for row in session.scalars(select(ApprovalRow))
            }
            self.artifacts = {
                row.id: Artifact.model_validate(row.payload)
                for row in session.scalars(select(ArtifactRow))
            }
            self.notifications = {
                row.id: Notification.model_validate(row.payload)
                for row in session.scalars(select(NotificationRow))
            }
            self.audit = [
                self._audit_from_row(row)
                for row in session.scalars(select(AuditEventRow).order_by(AuditEventRow.timestamp))
            ]
            self._audit_session_ids = {
                row.id: row.event_session_id for row in session.scalars(select(AuditEventRow))
            }
            self._system = session.get(SystemStateRow, 1)
            if self._system is None:
                self._system = SystemStateRow(
                    id=1,
                    emergency_stop=False,
                    simulator_status="idle",
                    event_session_id=f"session-{uuid4().hex}",
                    current_sequence_number=0,
                    seed_data_version=SEED_VERSION,
                    startup_was_clean=True,
                    recovery_status="none",
                    updated_at=datetime.now(UTC),
                )
                session.add(self._system)
                session.commit()

    @property
    def emergency_stop(self) -> bool:
        return self._system.emergency_stop

    @emergency_stop.setter
    def emergency_stop(self, value: bool) -> None:
        self._system.emergency_stop = value

    @property
    def event_session_id(self) -> str:
        return self._system.event_session_id

    @property
    def sequence(self) -> int:
        return self._system.current_sequence_number

    def next_sequence(self) -> int:
        self._system.current_sequence_number += 1
        return self._system.current_sequence_number

    def reset_sequence(self) -> None:
        self._system.event_session_id = f"session-{uuid4().hex}"
        self._system.current_sequence_number = 0

    @staticmethod
    def require(store: dict[str, object], item_id: str, kind: str) -> object:
        if item_id not in store:
            raise DomainError(f"{kind.upper()}_NOT_FOUND", f"Unknown {kind} ID: {item_id}", 404)
        return store[item_id]

    def persist(self) -> None:
        try:
            with UnitOfWork(self.session_factory) as uow:
                assert uow.session is not None
                self._persist_entities(uow.session)
                self._persist_audit(uow.session)
                self._system.updated_at = datetime.now(UTC)
                uow.session.merge(self._system)
        except Exception:
            self.reload()
            raise

    def _persist_entities(self, session: Session) -> None:
        for item in self.departments.values():
            payload = item.model_dump(mode="json")
            session.merge(
                DepartmentRow(
                    id=item.id,
                    name=item.name,
                    description=item.description,
                    manager_agent_id=item.managerAgentId,
                    schema_version="1.0",
                    payload=payload,
                )
            )
        session.flush()
        for item in self.agents.values():
            session.merge(
                AgentRow(
                    id=item.id,
                    name=item.name,
                    role=item.role,
                    description=item.description,
                    department_id=item.departmentId,
                    manager_id=item.managerId,
                    status=item.status,
                    previous_status=item.previousStatus,
                    current_task_id=item.currentTaskId,
                    progress=item.progress,
                    status_message=item.statusMessage,
                    deployment_status=item.deploymentStatus,
                    is_temporary=item.isTemporary,
                    schema_version=item.schemaVersion,
                    version=item.version,
                    payload=item.model_dump(mode="json"),
                    created_at=item.createdAt,
                    updated_at=item.updatedAt,
                )
            )
            session.flush()
        for item in self.tasks.values():
            session.merge(
                TaskRow(
                    id=item.id,
                    title=item.title,
                    description=item.description,
                    original_request=item.request,
                    parent_task_id=item.parentTaskId,
                    project_id=item.projectId,
                    creator=item.createdBy,
                    assigned_manager_id=item.assignedManagerId,
                    priority=item.priority,
                    status=item.status,
                    progress=item.progress,
                    status_message=item.statusMessage,
                    result=item.result,
                    error=item.error,
                    retry_count=item.retryCount,
                    maximum_retries=item.maxRetries,
                    schema_version=item.schemaVersion,
                    payload=item.model_dump(mode="json"),
                    created_at=item.createdAt,
                    started_at=item.startedAt,
                    updated_at=item.updatedAt,
                    completed_at=item.completedAt,
                )
            )
            session.flush()
        for item in self.approvals.values():
            updated = item.reviewedAt or item.createdAt
            session.merge(
                ApprovalRow(
                    id=item.id,
                    task_id=item.taskId,
                    requesting_agent_id=item.requestedByAgentId,
                    action_type=item.actionType,
                    title=item.title,
                    description=item.description,
                    reason=item.reason,
                    risk_level=item.riskLevel,
                    affected_resources=item.affectedResources,
                    exact_action_preview=item.exactActionPreview,
                    expected_outcome=item.expectedOutcome,
                    reversal_method=item.reversalMethod,
                    expires_at=item.expiresAt,
                    status=item.status,
                    reviewed_by=item.reviewedBy,
                    reviewed_at=item.reviewedAt,
                    decision_note=item.decisionNote,
                    schema_version=item.schemaVersion,
                    payload=item.model_dump(mode="json"),
                    created_at=item.createdAt,
                    updated_at=updated,
                )
            )
        for item in self.artifacts.values():
            session.merge(
                ArtifactRow(
                    id=item.id,
                    task_id=item.taskId,
                    producing_agent_id=None,
                    name=item.name,
                    artifact_type=item.type,
                    description=item.summary,
                    content_reference=item.simulatedPath,
                    metadata_json={"simulated": True},
                    version="1.0.0",
                    schema_version="1.0",
                    payload=item.model_dump(mode="json"),
                    created_at=item.createdAt,
                    updated_at=item.createdAt,
                )
            )
        for item in self.notifications.values():
            session.merge(
                NotificationRow(
                    id=item.id,
                    notification_type=item.level,
                    title=item.title,
                    message=item.message,
                    related_task_id=item.taskId,
                    related_agent_id=None,
                    is_read=item.isRead,
                    metadata_json={},
                    schema_version="1.0",
                    payload=item.model_dump(mode="json"),
                    created_at=item.createdAt,
                    read_at=datetime.now(UTC) if item.isRead else None,
                )
            )
        session.execute(delete(TaskAgentRow))
        session.execute(delete(TaskDependencyRow))
        session.execute(delete(TaskBlockerRow))
        task_ids = set(self.tasks)
        for item in self.tasks.values():
            for agent_id in item.assignedAgentIds:
                if agent_id in self.agents:
                    session.add(TaskAgentRow(task_id=item.id, agent_id=agent_id))
            for dependency in item.dependencies:
                if dependency.taskId in task_ids:
                    session.add(
                        TaskDependencyRow(
                            task_id=item.id,
                            dependency_task_id=dependency.taskId,
                            dependency_type=dependency.type,
                        )
                    )
            for blocker_id in item.blockedBy:
                if blocker_id in task_ids:
                    session.add(TaskBlockerRow(task_id=item.id, blocker_task_id=blocker_id))

    def _persist_audit(self, session: Session) -> None:
        existing = set(session.scalars(select(AuditEventRow.id)))
        for item in self.audit:
            if item.id not in existing:
                session.add(
                    AuditEventRow(
                        id=item.id,
                        event_type=item.eventType,
                        actor="system" if item.actorAgentId is None else item.actorAgentId,
                        agent_id=item.actorAgentId,
                        task_id=item.taskId,
                        approval_id=item.approvalId,
                        previous_state=item.previousState,
                        new_state=item.newState,
                        correlation_id=item.correlationId,
                        sequence_number=item.sequenceNumber,
                        event_session_id=self._audit_session_ids.get(
                            item.id, self.event_session_id
                        ),
                        timestamp=item.timestamp,
                        payload={
                            "summary": item.summary,
                            "payload": item.payload,
                            "artifactIds": item.artifactIds,
                        },
                        schema_version="1.0",
                    )
                )

    @staticmethod
    def _audit_from_row(row: AuditEventRow) -> AuditEvent:
        return AuditEvent(
            id=row.id,
            timestamp=row.timestamp,
            eventType=row.event_type,
            actorAgentId=row.agent_id,
            taskId=row.task_id,
            previousState=row.previous_state,
            newState=row.new_state,
            summary=row.payload.get("summary", row.event_type),
            correlationId=row.correlation_id,
            sequenceNumber=row.sequence_number,
            payload=row.payload.get("payload", {}),
            artifactIds=row.payload.get("artifactIds", []),
            approvalId=row.approval_id,
        )

    def add_audit(
        self,
        event_type: str,
        summary: str,
        sequence: int,
        task_id: str | None = None,
        agent_id: str | None = None,
        previous: str | None = None,
        new: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> AuditEvent:
        item = self.stage_audit(
            event_type, summary, sequence, task_id, agent_id, previous, new, payload
        )
        self.persist()
        return item

    def stage_audit(
        self,
        event_type: str,
        summary: str,
        sequence: int,
        task_id: str | None = None,
        agent_id: str | None = None,
        previous: str | None = None,
        new: str | None = None,
        payload: dict[str, object] | None = None,
        event_session_id: str | None = None,
    ) -> AuditEvent:
        item = AuditEvent(
            id=f"audit-{uuid4().hex[:12]}",
            timestamp=datetime.now(UTC),
            eventType=event_type,
            actorAgentId=agent_id,
            taskId=task_id,
            previousState=previous,
            newState=new,
            summary=summary,
            correlationId="phase-2-demo",
            sequenceNumber=sequence,
            payload=payload or {},
            approvalId=str((payload or {}).get("approvalId"))
            if (payload or {}).get("approvalId")
            else None,
        )
        self.audit.append(item)
        if event_session_id:
            self._audit_session_ids[item.id] = event_session_id
        return item

    def enqueue_event(
        self,
        envelope: dict[str, Any],
        idempotency: IdempotencyResult | None = None,
    ) -> None:
        pending_checkpoint = self._pending_checkpoint
        pending_workflow_run = self._pending_workflow_run
        try:
            with UnitOfWork(self.session_factory) as uow:
                assert uow.session is not None
                session = uow.session
                self._persist_entities(session)
                self._persist_audit(session)
                self._system.updated_at = datetime.now(UTC)
                session.merge(self._system)
                if pending_workflow_run:
                    session.add(WorkflowRunRow(**pending_workflow_run))
                    session.flush()
                session.add(
                    OutboxEventRow(
                        id=envelope["eventId"],
                        event_type=envelope["eventType"],
                        envelope=envelope,
                        correlation_id=envelope["correlationId"],
                        event_session_id=self.event_session_id,
                        sequence_number=envelope["sequenceNumber"],
                        status="pending",
                        created_at=datetime.now(UTC),
                        publish_attempt_count=0,
                    )
                )
                if pending_checkpoint:
                    self._write_checkpoint(session, pending_checkpoint)
                if idempotency:
                    self._store_idempotency(session, idempotency)
        except Exception:
            self.reload()
            raise
        if pending_workflow_run:
            self._pending_workflow_run = None
        if pending_checkpoint:
            self._system.last_checkpoint_id = pending_checkpoint["id"]
            self._system.simulator_status = pending_checkpoint["status"]
            self._pending_checkpoint = None

    def pending_outbox(self) -> list[dict[str, Any]]:
        return [envelope for _, envelope in self.pending_outbox_records()]

    def pending_outbox_records(self) -> list[tuple[str, dict[str, Any]]]:
        with self.session_factory() as session:
            return [
                (row.id, row.envelope)
                for row in session.scalars(
                    select(OutboxEventRow)
                    .where(
                        OutboxEventRow.status.in_(["pending", "failed"]),
                        OutboxEventRow.publish_attempt_count < self.outbox_max_attempts,
                    )
                    .order_by(OutboxEventRow.created_at)
                )
            ]

    def mark_outbox(self, event_id: str, published: bool, error: str | None = None) -> None:
        with self.session_factory() as session, session.begin():
            row = session.get(OutboxEventRow, event_id)
            if row:
                row.publish_attempt_count += 1
                row.status = "published" if published else "failed"
                row.published_at = datetime.now(UTC) if published else None
                row.last_publish_error = error

    def outbox_pending_count(self) -> int:
        with self.session_factory() as session:
            return int(
                session.scalar(
                    select(func.count())
                    .select_from(OutboxEventRow)
                    .where(OutboxEventRow.status != "published")
                )
                or 0
            )

    def outbox_exhausted_count(self) -> int:
        with self.session_factory() as session:
            return int(
                session.scalar(
                    select(func.count())
                    .select_from(OutboxEventRow)
                    .where(
                        OutboxEventRow.status == "failed",
                        OutboxEventRow.publish_attempt_count >= self.outbox_max_attempts,
                    )
                )
                or 0
            )

    @staticmethod
    def request_hash(payload: Any) -> str:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return sha256(canonical.encode()).hexdigest()

    def idempotency_lookup(
        self, key: str, command: str, payload: Any
    ) -> tuple[int, dict[str, Any]] | None:
        digest = self.request_hash(payload)
        with self.session_factory() as session:
            row = session.scalar(
                select(IdempotencyRecordRow).where(
                    IdempotencyRecordRow.idempotency_key == key,
                    IdempotencyRecordRow.command_type == command,
                )
            )
            if not row:
                return None
            if row.canonical_request_hash != digest:
                raise DomainError(
                    "IDEMPOTENCY_KEY_CONFLICT",
                    "The idempotency key was already used with a different request.",
                    409,
                )
            if row.response_status == 0:
                raise DomainError(
                    "IDEMPOTENCY_REQUEST_IN_PROGRESS",
                    "A command with this idempotency key is still in progress.",
                    409,
                )
            return row.response_status, row.response_body

    def idempotency_claim(self, key: str, command: str, payload: Any) -> IdempotencyClaim:
        digest = self.request_hash(payload)
        now = datetime.now(UTC)
        lease_expires_at = now + timedelta(seconds=self.idempotency_lease_seconds)
        try:
            with self.session_factory() as session, session.begin():
                row = session.scalar(
                    select(IdempotencyRecordRow).where(
                        IdempotencyRecordRow.idempotency_key == key,
                        IdempotencyRecordRow.command_type == command,
                    )
                )
                if row:
                    if row.canonical_request_hash != digest:
                        raise DomainError(
                            "IDEMPOTENCY_KEY_CONFLICT",
                            "The idempotency key was already used with a different request.",
                            409,
                        )
                    if row.response_status != 0:
                        return IdempotencyClaim(
                            response=(row.response_status, row.response_body), owned=False
                        )
                    reclaimed = session.execute(
                        update(IdempotencyRecordRow)
                        .where(
                            IdempotencyRecordRow.id == row.id,
                            IdempotencyRecordRow.response_status == 0,
                            or_(
                                IdempotencyRecordRow.expiration_at.is_(None),
                                IdempotencyRecordRow.expiration_at <= now,
                            ),
                        )
                        .values(
                            created_at=now,
                            expiration_at=lease_expires_at,
                            response_body={},
                            created_resource_id=None,
                        )
                        .execution_options(synchronize_session=False)
                    )
                    if reclaimed.rowcount == 1:
                        return IdempotencyClaim(
                            response=None,
                            owned=True,
                            lease_expires_at=lease_expires_at,
                        )
                    raise DomainError(
                        "IDEMPOTENCY_REQUEST_IN_PROGRESS",
                        "A command with this idempotency key is still in progress.",
                        409,
                    )
                session.add(
                    IdempotencyRecordRow(
                        idempotency_key=key,
                        command_type=command,
                        canonical_request_hash=digest,
                        response_status=0,
                        response_body={},
                        created_resource_id=None,
                        created_at=now,
                        expiration_at=lease_expires_at,
                    )
                )
        except IntegrityError:
            return self.idempotency_claim(key, command, payload)
        return IdempotencyClaim(response=None, owned=True, lease_expires_at=lease_expires_at)

    @staticmethod
    def _store_idempotency(session: Session, result: IdempotencyResult) -> None:
        completed = session.execute(
            update(IdempotencyRecordRow)
            .where(
                IdempotencyRecordRow.idempotency_key == result.key,
                IdempotencyRecordRow.command_type == result.command,
                IdempotencyRecordRow.response_status == 0,
                IdempotencyRecordRow.expiration_at == result.lease_expires_at,
            )
            .values(
                response_status=result.status,
                response_body=result.body,
                created_resource_id=result.resource_id,
                expiration_at=None,
            )
            .execution_options(synchronize_session=False)
        )
        if completed.rowcount != 1:
            raise RuntimeError("The idempotency claim lease was lost before command commit.")

    def idempotency_abandon(self, key: str, command: str, lease_expires_at: datetime) -> None:
        with self.session_factory() as session, session.begin():
            session.execute(
                delete(IdempotencyRecordRow).where(
                    IdempotencyRecordRow.idempotency_key == key,
                    IdempotencyRecordRow.command_type == command,
                    IdempotencyRecordRow.response_status == 0,
                    IdempotencyRecordRow.expiration_at == lease_expires_at,
                )
            )

    def create_workflow_run(self, run_id: str, total_steps: int) -> None:
        now = datetime.now(UTC)
        self._pending_workflow_run = {
            "id": run_id,
            "correlation_id": run_id,
            "root_task_id": "task-demo",
            "workflow_type": "deterministic-demo",
            "workflow_version": "2.0",
            "current_step_index": 0,
            "current_step_identifier": None,
            "status": "running",
            "started_at": now,
            "updated_at": now,
            "retry_count": 0,
            "resume_eligibility": True,
        }
        self._system.last_workflow_run_id = run_id
        self._system.simulator_status = "running"
        self._system.recovery_status = "none"

    def active_workflow(self) -> WorkflowRunRow | None:
        with self.session_factory() as session:
            return session.scalar(
                select(WorkflowRunRow)
                .where(
                    WorkflowRunRow.status.in_(
                        ["running", "paused", "recovery_required", "waiting_for_approval"]
                    )
                )
                .order_by(WorkflowRunRow.started_at.desc())
            )

    def health_probe(self, expected_revision: str) -> tuple[bool, bool]:
        try:
            with self.session_factory() as session:
                session.execute(text("SELECT 1"))
        except SQLAlchemyError:
            return False, False
        try:
            with self.session_factory() as session:
                revision = session.scalar(text("SELECT version_num FROM alembic_version"))
        except SQLAlchemyError:
            return True, False
        return True, revision == expected_revision

    def checkpoint(
        self,
        run_id: str,
        step_index: int,
        step_identifier: str,
        simulator_variables: dict[str, Any] | None = None,
        status: str = "running",
    ) -> str:
        checkpoint = self._build_checkpoint(
            run_id, step_index, step_identifier, simulator_variables, status
        )
        with self.session_factory() as session, session.begin():
            self._write_checkpoint(session, checkpoint)
        self._system.last_checkpoint_id = checkpoint["id"]
        self._system.simulator_status = status
        return str(checkpoint["id"])

    def stage_checkpoint(
        self,
        run_id: str,
        step_index: int,
        step_identifier: str,
        simulator_variables: dict[str, Any] | None = None,
        status: str = "running",
    ) -> str:
        checkpoint = self._build_checkpoint(
            run_id, step_index, step_identifier, simulator_variables, status
        )
        checkpoint["payload"]["lastCommittedSequenceNumber"] = self.sequence + 1
        self._pending_checkpoint = checkpoint
        return str(checkpoint["id"])

    def _build_checkpoint(
        self,
        run_id: str,
        step_index: int,
        step_identifier: str,
        simulator_variables: dict[str, Any] | None,
        status: str,
    ) -> dict[str, Any]:
        checkpoint_id = f"checkpoint-{uuid4().hex}"
        payload = {
            "workflowVersion": "2.0",
            "stepIndex": step_index,
            "stepIdentifier": step_identifier,
            "rootTaskId": "task-demo",
            "agentStatuses": {key: value.status for key, value in self.agents.items()},
            "agentLocations": {
                key: value.office.currentDestination for key, value in self.agents.items()
            },
            "taskStatuses": {key: value.status for key, value in self.tasks.items()},
            "taskProgressValues": {key: value.progress for key, value in self.tasks.items()},
            "pendingApprovals": [
                key for key, value in self.approvals.items() if value.status == "pending"
            ],
            "revisionCount": int((simulator_variables or {}).get("revisionCount", 0)),
            "retryCount": int((simulator_variables or {}).get("retryCount", 0)),
            "emergencyStop": self.emergency_stop,
            "eventSessionId": self.event_session_id,
            "lastCommittedSequenceNumber": self.sequence,
            "simulatorVariables": simulator_variables or {},
        }
        self.validate_checkpoint(payload)
        return {
            "id": checkpoint_id,
            "run_id": run_id,
            "step_index": step_index,
            "step_identifier": step_identifier,
            "payload": payload,
            "status": status,
            "created_at": datetime.now(UTC),
        }

    @staticmethod
    def _write_checkpoint(session: Session, checkpoint: dict[str, Any]) -> None:
        session.add(
            WorkflowCheckpointRow(
                id=checkpoint["id"],
                workflow_run_id=checkpoint["run_id"],
                workflow_version="2.0",
                step_index=checkpoint["step_index"],
                step_identifier=checkpoint["step_identifier"],
                root_task_id="task-demo",
                payload=checkpoint["payload"],
                created_at=checkpoint["created_at"],
            )
        )
        run = session.get(WorkflowRunRow, checkpoint["run_id"])
        if run:
            run.current_step_index = checkpoint["step_index"]
            run.current_step_identifier = checkpoint["step_identifier"]
            run.checkpoint_id = checkpoint["id"]
            run.status = checkpoint["status"]
            run.updated_at = checkpoint["created_at"]
            if checkpoint["status"] in {"completed", "failed"}:
                run.completed_at = checkpoint["created_at"]
                run.resume_eligibility = False
            if checkpoint["status"] == "failed":
                scenario = checkpoint["payload"].get("simulatorVariables", {}).get("scenario")
                run.failure_reason = str(scenario or "Controlled simulated failure.")
        state = session.get(SystemStateRow, 1)
        state.last_checkpoint_id = checkpoint["id"]
        state.simulator_status = checkpoint["status"]
        if checkpoint["status"] == "failed":
            state.recovery_status = "none"

    def load_checkpoint(self, checkpoint_id: str) -> dict[str, Any]:
        with self.session_factory() as session:
            row = session.get(WorkflowCheckpointRow, checkpoint_id)
            if not row:
                raise DomainError(
                    "CHECKPOINT_NOT_FOUND", "The workflow checkpoint was not found.", 404
                )
            payload = row.payload
        self.validate_checkpoint(payload)
        return payload

    def set_workflow_status(self, run_id: str, status: str, reason: str | None = None) -> None:
        with self.session_factory() as session, session.begin():
            run = session.get(WorkflowRunRow, run_id)
            if run:
                run.status = status
                run.updated_at = datetime.now(UTC)
                if status == "cancelled":
                    run.completed_at = datetime.now(UTC)
                if reason:
                    run.pause_reason = reason
            state = session.get(SystemStateRow, 1)
            state.simulator_status = status
            if status in {"running", "completed", "cancelled"}:
                state.recovery_status = "none"
        self._system.simulator_status = status
        if status in {"running", "completed", "cancelled"}:
            self._system.recovery_status = "none"

    @staticmethod
    def validate_checkpoint(payload: dict[str, Any]) -> None:
        required = {
            "workflowVersion",
            "stepIndex",
            "stepIdentifier",
            "rootTaskId",
            "agentStatuses",
            "taskStatuses",
            "eventSessionId",
            "lastCommittedSequenceNumber",
        }
        if (
            not required.issubset(payload)
            or payload["workflowVersion"] != "2.0"
            or not isinstance(payload["stepIndex"], int)
        ):
            raise DomainError(
                "INVALID_CHECKPOINT", "The workflow checkpoint is invalid or incompatible.", 409
            )

    def mark_interrupted_workflow(self) -> str | None:
        with self.session_factory() as session, session.begin():
            run = session.scalar(
                select(WorkflowRunRow)
                .where(WorkflowRunRow.status.in_(["running", "paused", "recovery_required"]))
                .order_by(WorkflowRunRow.started_at.desc())
            )
            if not run:
                return None
            was_paused = run.status == "paused"
            if run.status == "running":
                run.status = "recovery_required"
                run.pause_reason = "Backend restarted before a clean workflow completion."
            run.updated_at = datetime.now(UTC)
            state = session.get(SystemStateRow, 1)
            state.recovery_status = "none" if was_paused else "required"
            state.simulator_status = "paused"
        self._system.recovery_status = "none" if was_paused else "required"
        self._system.simulator_status = "paused"
        return "paused" if was_paused else "recovery_required"

    def reset(
        self,
        run_id: str | None = None,
        idempotency: IdempotencyResult | None = None,
    ) -> None:
        user_tasks = {
            key: value
            for key, value in self.tasks.items()
            if key not in {x["id"] for x in build_seed()["tasks"]}
            and not key.startswith("task-demo-")
        }
        historical_audit = list(self.audit)
        historical_audit_sessions = dict(self._audit_session_ids)
        seed = build_seed()
        seed_artifact_ids = {item["id"] for item in seed["artifacts"]}
        seed_notification_ids = {item["id"] for item in seed["notifications"]}
        self._load_seed_memory()
        self.tasks.update(user_tasks)
        self.audit = historical_audit
        self._audit_session_ids = historical_audit_sessions
        self.reset_sequence()
        self.emergency_stop = False
        self._system.recovery_status = "none"
        self._system.simulator_status = "idle"
        try:
            with UnitOfWork(self.session_factory) as uow:
                assert uow.session is not None
                session = uow.session
                session.execute(delete(TaskAgentRow))
                session.execute(delete(TaskDependencyRow))
                session.execute(delete(TaskBlockerRow))
                demo_lease_tokens = list(
                    session.scalars(
                        select(TaskLeaseRow.lease_token).where(
                            or_(
                                TaskLeaseRow.task_id == "task-demo",
                                TaskLeaseRow.task_id.like("task-demo-%"),
                            )
                        )
                    )
                )
                if demo_lease_tokens:
                    now = datetime.now(UTC)
                    session.execute(
                        update(TaskAttemptRow)
                        .where(TaskAttemptRow.lease_token.in_(demo_lease_tokens))
                        .values(ended_at=now, outcome="simulator_reset")
                    )
                    session.execute(
                        delete(TaskLeaseRow).where(TaskLeaseRow.lease_token.in_(demo_lease_tokens))
                    )
                session.execute(delete(ArtifactRow).where(ArtifactRow.id.not_in(seed_artifact_ids)))
                session.execute(delete(TaskRow).where(TaskRow.id.like("task-demo-%")))
                session.execute(delete(AgentRow).where(AgentRow.is_temporary.is_(True)))
                session.execute(
                    delete(NotificationRow).where(NotificationRow.id.not_in(seed_notification_ids))
                )
                if run_id:
                    run = session.get(WorkflowRunRow, run_id)
                    if run:
                        now = datetime.now(UTC)
                        run.status = "cancelled"
                        run.updated_at = now
                        run.completed_at = now
                        run.pause_reason = "Reset by local operator"
                        run.resume_eligibility = False
                self._persist_entities(session)
                self._persist_audit(session)
                self._system.updated_at = datetime.now(UTC)
                session.merge(self._system)
                if idempotency:
                    self._store_idempotency(session, idempotency)
        except Exception:
            self.reload()
            raise

    def snapshot(self) -> dict[str, object]:
        return deepcopy(
            {
                "departments": list(self.departments.values()),
                "agents": list(self.agents.values()),
                "tasks": list(self.tasks.values()),
                "approvals": list(self.approvals.values()),
                "artifacts": list(self.artifacts.values()),
                "notifications": list(self.notifications.values()),
                "auditEvents": self.audit,
                "emergencyStop": self.emergency_stop,
            }
        )
