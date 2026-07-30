from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import uuid4

from sqlalchemy import case, exists, func, select, text, update
from sqlalchemy.orm import Session, aliased, sessionmaker

from app.core.errors import DomainError
from app.db.models import (
    AuditEventRow,
    OutboxEventRow,
    SystemStateRow,
    TaskAttemptRow,
    TaskDependencyRow,
    TaskLeaseRow,
    TaskRow,
    WorkerRow,
    WorkflowCheckpointRow,
)
from app.models.domain import EventEnvelope, Task, TaskLease, Worker
from app.repositories.sqlalchemy import SqlAlchemyRepository

ELIGIBLE_TASK_STATES = ("queued", "retrying")
TERMINAL_TASK_STATES = ("completed", "cancelled")


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class TaskLeaseRepository:
    """Transactional worker and task-lease persistence on the Phase 2A database."""

    def __init__(
        self,
        repository: SqlAlchemyRepository,
        session_factory: sessionmaker[Session],
        default_lease_seconds: int,
    ) -> None:
        self.repository = repository
        self.session_factory = session_factory
        self.default_lease_seconds = default_lease_seconds

    @contextmanager
    def _write(self) -> Iterator[Session]:
        session = self.session_factory()
        try:
            if session.bind and session.bind.dialect.name == "sqlite":
                session.execute(text("BEGIN IMMEDIATE"))
            else:
                session.begin()
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @staticmethod
    def _worker(row: WorkerRow) -> Worker:
        return Worker(
            id=row.id,
            name=row.name,
            instanceId=row.instance_id,
            status=row.status,
            startedAt=row.started_at,
            lastHeartbeatAt=row.last_heartbeat_at,
            stoppedAt=row.stopped_at,
            leaseSeconds=row.lease_seconds,
            metadata=row.metadata_json,
        )

    @staticmethod
    def _lease(row: TaskLeaseRow, recovery_checkpoint_id: str | None = None) -> TaskLease:
        return TaskLease(
            taskId=row.task_id,
            workerId=row.worker_id,
            leaseToken=row.lease_token,
            acquiredAt=row.acquired_at,
            expiresAt=row.expires_at,
            renewedAt=row.renewed_at,
            attemptNumber=row.attempt_number,
            version=row.version,
            recoveryCheckpointId=recovery_checkpoint_id or row.checkpoint_id,
        )

    @staticmethod
    def _token_fingerprint(token: str) -> str:
        return sha256(token.encode()).hexdigest()[:12]

    def _add_event(
        self,
        session: Session,
        event_type: str,
        summary: str,
        *,
        task_id: str | None = None,
        worker_id: str | None = None,
        previous: str | None = None,
        new: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        state = session.get(SystemStateRow, 1)
        if state is None:
            raise RuntimeError("System state must exist before lease events are emitted.")
        state.current_sequence_number += 1
        state.updated_at = datetime.now(UTC)
        now = datetime.now(UTC)
        event_id = f"event-{uuid4().hex}"
        correlation_id = task_id or worker_id or "task-leases"
        event_payload = payload or {}
        envelope = EventEnvelope(
            eventId=event_id,
            eventType=event_type,
            timestamp=now,
            sequenceNumber=state.current_sequence_number,
            eventSessionId=state.event_session_id,
            correlationId=correlation_id,
            taskId=task_id,
            source="task-lease-service",
            payload=event_payload,
        ).model_dump(mode="json")
        session.add(
            OutboxEventRow(
                id=event_id,
                event_type=event_type,
                envelope=envelope,
                correlation_id=correlation_id,
                event_session_id=state.event_session_id,
                sequence_number=state.current_sequence_number,
                status="pending",
                created_at=now,
                publish_attempt_count=0,
            )
        )
        session.add(
            AuditEventRow(
                id=f"audit-{uuid4().hex[:12]}",
                event_type=event_type,
                actor=worker_id or "system",
                agent_id=None,
                task_id=task_id,
                approval_id=None,
                previous_state=previous,
                new_state=new,
                correlation_id=correlation_id,
                sequence_number=state.current_sequence_number,
                event_session_id=state.event_session_id,
                timestamp=now,
                payload={"summary": summary, "payload": event_payload, "artifactIds": []},
                schema_version="1.0",
            )
        )

    def register_worker(
        self,
        name: str,
        instance_id: str,
        lease_seconds: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Worker:
        now = datetime.now(UTC)
        duration = lease_seconds or self.default_lease_seconds
        with self._write() as session:
            existing = session.scalar(select(WorkerRow).where(WorkerRow.instance_id == instance_id))
            if existing:
                if existing.name != name:
                    raise DomainError(
                        "WORKER_INSTANCE_CONFLICT",
                        "The worker instance ID is registered with a different name.",
                        409,
                    )
                existing.status = "active"
                existing.last_heartbeat_at = now
                existing.stopped_at = None
                existing.lease_seconds = duration
                existing.metadata_json = metadata or existing.metadata_json
                row = existing
            else:
                row = WorkerRow(
                    id=f"worker-{uuid4().hex}",
                    name=name,
                    instance_id=instance_id,
                    status="active",
                    started_at=now,
                    last_heartbeat_at=now,
                    lease_seconds=duration,
                    metadata_json=metadata or {},
                )
                session.add(row)
                self._add_event(
                    session,
                    "worker.registered",
                    f"Registered worker {name}",
                    worker_id=row.id,
                    payload={"workerId": row.id, "instanceId": instance_id},
                )
        self.repository.reload()
        return self._worker(row)

    def heartbeat_worker(self, worker_id: str) -> Worker:
        with self._write() as session:
            row = session.get(WorkerRow, worker_id)
            if row is None:
                raise DomainError("WORKER_NOT_FOUND", "The worker was not found.", 404)
            if row.status == "stopped":
                raise DomainError("WORKER_STOPPED", "A stopped worker cannot heartbeat.", 409)
            row.last_heartbeat_at = datetime.now(UTC)
        return self._worker(row)

    def list_workers(self) -> list[Worker]:
        with self.session_factory() as session:
            rows = list(session.scalars(select(WorkerRow).order_by(WorkerRow.started_at)))
            return [self._worker(row) for row in rows]

    def task_status(self, task_id: str) -> str | None:
        with self.session_factory() as session:
            return session.scalar(select(TaskRow.status).where(TaskRow.id == task_id))

    def drain_worker(self, worker_id: str) -> Worker:
        with self._write() as session:
            row = session.get(WorkerRow, worker_id)
            if row is None:
                raise DomainError("WORKER_NOT_FOUND", "The worker was not found.", 404)
            row.status = "draining"
            row.last_heartbeat_at = datetime.now(UTC)
            leases = list(
                session.scalars(select(TaskLeaseRow).where(TaskLeaseRow.worker_id == worker_id))
            )
            for lease in leases:
                self._release_in_transaction(session, lease, "worker_draining")
            self._add_event(
                session,
                "worker.draining",
                f"Worker {row.name} entered draining state",
                worker_id=row.id,
                payload={"workerId": row.id, "releasedLeaseCount": len(leases)},
            )
        self.repository.reload()
        return self._worker(row)

    def stop_worker(self, worker_id: str) -> Worker:
        with self._write() as session:
            row = session.get(WorkerRow, worker_id)
            if row is None:
                raise DomainError("WORKER_NOT_FOUND", "The worker was not found.", 404)
            now = datetime.now(UTC)
            leases = list(
                session.scalars(select(TaskLeaseRow).where(TaskLeaseRow.worker_id == worker_id))
            )
            for lease in leases:
                self._release_in_transaction(session, lease, "worker_stopped")
            row.status = "stopped"
            row.last_heartbeat_at = now
            row.stopped_at = now
            self._add_event(
                session,
                "worker.stopped",
                f"Stopped worker {row.name}",
                worker_id=row.id,
                payload={"workerId": row.id, "releasedLeaseCount": len(leases)},
            )
        self.repository.reload()
        return self._worker(row)

    def acquire_task(
        self,
        worker_id: str,
        lease_seconds: int | None = None,
        task_id: str | None = None,
    ) -> tuple[Task, TaskLease] | None:
        self.recover_expired_leases()
        now = datetime.now(UTC)
        with self._write() as session:
            worker = session.get(WorkerRow, worker_id)
            if worker is None:
                raise DomainError("WORKER_NOT_FOUND", "The worker was not found.", 404)
            if worker.status != "active":
                raise DomainError(
                    "WORKER_NOT_ACTIVE", "Only an active worker may acquire tasks.", 409
                )
            self._require_execution_enabled(session)
            duration = lease_seconds or worker.lease_seconds or self.default_lease_seconds
            dependency = aliased(TaskRow)
            unsatisfied_dependency = exists(
                select(TaskDependencyRow.task_id)
                .join(
                    dependency,
                    dependency.id == TaskDependencyRow.dependency_task_id,
                )
                .where(
                    TaskDependencyRow.task_id == TaskRow.id,
                    TaskDependencyRow.dependency_type == "requires",
                    dependency.status != "completed",
                )
            )
            active_lease = exists(
                select(TaskLeaseRow.task_id).where(TaskLeaseRow.task_id == TaskRow.id)
            )
            task_query = select(TaskRow).where(
                TaskRow.status.in_(ELIGIBLE_TASK_STATES),
                ~active_lease,
                ~unsatisfied_dependency,
            )
            if task_id is not None:
                task_query = task_query.where(TaskRow.id == task_id)
            task = session.scalar(
                task_query.order_by(
                    case(
                        (TaskRow.priority == "urgent", 1),
                        (TaskRow.priority == "high", 2),
                        (TaskRow.priority == "medium", 3),
                        (TaskRow.priority == "low", 4),
                        else_=5,
                    ),
                    TaskRow.created_at,
                    TaskRow.id,
                ).limit(1)
            )
            if task is None:
                return None
            previous = task.status
            attempt_number = (
                int(
                    session.scalar(
                        select(func.coalesce(func.max(TaskAttemptRow.attempt_number), 0)).where(
                            TaskAttemptRow.task_id == task.id
                        )
                    )
                    or 0
                )
                + 1
            )
            token = uuid4().hex
            expires_at = now + timedelta(seconds=duration)
            payload = dict(task.payload)
            payload.update(
                {
                    "status": "in_progress",
                    "statusMessage": f"Owned by worker {worker.name}",
                    "startedAt": payload.get("startedAt") or now.isoformat(),
                    "updatedAt": now.isoformat(),
                }
            )
            claimed = session.execute(
                update(TaskRow)
                .where(TaskRow.id == task.id, TaskRow.status.in_(ELIGIBLE_TASK_STATES))
                .values(
                    status="in_progress",
                    status_message=f"Owned by worker {worker.name}",
                    started_at=task.started_at or now,
                    updated_at=now,
                    payload=payload,
                )
                .execution_options(synchronize_session=False)
            )
            if claimed.rowcount != 1:
                raise DomainError(
                    "TASK_CLAIM_CONFLICT", "Another worker acquired the selected task.", 409
                )
            recovery_checkpoint_id = session.scalar(
                select(TaskAttemptRow.checkpoint_id)
                .where(
                    TaskAttemptRow.task_id == task.id,
                    TaskAttemptRow.checkpoint_id.is_not(None),
                )
                .order_by(TaskAttemptRow.attempt_number.desc())
                .limit(1)
            )
            lease = TaskLeaseRow(
                task_id=task.id,
                worker_id=worker_id,
                lease_token=token,
                acquired_at=now,
                expires_at=expires_at,
                renewed_at=now,
                attempt_number=attempt_number,
                version=1,
                checkpoint_id=recovery_checkpoint_id,
            )
            session.add(lease)
            session.add(
                TaskAttemptRow(
                    id=f"attempt-{uuid4().hex}",
                    task_id=task.id,
                    attempt_number=attempt_number,
                    worker_id=worker_id,
                    lease_token=token,
                    started_at=now,
                    checkpoint_id=recovery_checkpoint_id,
                )
            )
            worker.last_heartbeat_at = now
            self._add_event(
                session,
                "task.lease.acquired",
                f"Worker {worker.name} acquired task {task.id}",
                task_id=task.id,
                worker_id=worker_id,
                previous=previous,
                new="in_progress",
                payload={
                    "workerId": worker_id,
                    "attemptNumber": attempt_number,
                    "leaseTokenFingerprint": self._token_fingerprint(token),
                    "expiresAt": expires_at.isoformat(),
                    "recoveryCheckpointId": recovery_checkpoint_id,
                },
            )
        self.repository.reload()
        return self.repository.tasks[task.id], self._lease(lease, recovery_checkpoint_id)

    def assert_current(
        self,
        task_id: str,
        worker_id: str,
        lease_token: str,
        *,
        require_execution_enabled: bool = True,
    ) -> TaskLease:
        """Revalidate fencing and emergency-stop state without mutating the lease."""

        now = datetime.now(UTC)
        with self.session_factory() as session:
            lease = self._require_lease(session, task_id, worker_id, lease_token, now)
            if require_execution_enabled:
                self._require_execution_enabled(session)
            return self._lease(lease)

    def renew_lease(
        self,
        task_id: str,
        worker_id: str,
        lease_token: str,
        lease_seconds: int | None = None,
        checkpoint_id: str | None = None,
    ) -> TaskLease:
        now = datetime.now(UTC)
        with self._write() as session:
            lease = self._require_lease(session, task_id, worker_id, lease_token, now)
            worker = session.get(WorkerRow, worker_id)
            assert worker is not None
            if checkpoint_id:
                checkpoint = session.get(WorkflowCheckpointRow, checkpoint_id)
                if checkpoint is None or checkpoint.root_task_id != task_id:
                    raise DomainError(
                        "INVALID_LEASE_CHECKPOINT",
                        "The checkpoint does not belong to the leased task.",
                        409,
                    )
            duration = lease_seconds or worker.lease_seconds or self.default_lease_seconds
            lease.expires_at = now + timedelta(seconds=duration)
            lease.renewed_at = now
            lease.version += 1
            if checkpoint_id:
                lease.checkpoint_id = checkpoint_id
            worker.last_heartbeat_at = now
            attempt = session.scalar(
                select(TaskAttemptRow).where(TaskAttemptRow.lease_token == lease_token)
            )
            if attempt and checkpoint_id:
                attempt.checkpoint_id = checkpoint_id
            self._add_event(
                session,
                "task.lease.renewed",
                f"Worker {worker.name} renewed task {task_id}",
                task_id=task_id,
                worker_id=worker_id,
                previous="in_progress",
                new="in_progress",
                payload={
                    "workerId": worker_id,
                    "attemptNumber": lease.attempt_number,
                    "leaseTokenFingerprint": self._token_fingerprint(lease_token),
                    "expiresAt": lease.expires_at.isoformat(),
                    "checkpointId": checkpoint_id,
                },
            )
        self.repository.reload()
        return self._lease(lease)

    def release_lease(self, task_id: str, worker_id: str, lease_token: str) -> Task:
        with self._write() as session:
            lease = self._require_lease(session, task_id, worker_id, lease_token, datetime.now(UTC))
            self._release_in_transaction(session, lease, "released")
        self.repository.reload()
        return self.repository.tasks[task_id]

    def pause_for_review(
        self,
        task_id: str,
        worker_id: str,
        lease_token: str,
        result_reference: str | None = None,
    ) -> Task:
        now = datetime.now(UTC)
        with self._write() as session:
            lease = self._require_lease(session, task_id, worker_id, lease_token, now)
            self._require_execution_enabled(session)
            task = session.get(TaskRow, task_id)
            assert task is not None
            payload = dict(task.payload)
            payload.update(
                {
                    "status": "under_review",
                    "statusMessage": "Paused for human review",
                    "result": result_reference,
                    "updatedAt": now.isoformat(),
                }
            )
            task.status = "under_review"
            task.status_message = "Paused for human review"
            task.result = result_reference
            task.updated_at = now
            task.payload = payload
            attempt = session.scalar(
                select(TaskAttemptRow).where(TaskAttemptRow.lease_token == lease_token)
            )
            if attempt:
                attempt.ended_at = now
                attempt.outcome = "human_review_required"
            session.delete(lease)
            self._add_event(
                session,
                "task.review_required",
                f"Task {task_id} paused for human review",
                task_id=task_id,
                worker_id=worker_id,
                previous="in_progress",
                new="under_review",
                payload={
                    "workerId": worker_id,
                    "attemptNumber": lease.attempt_number,
                    "leaseTokenFingerprint": self._token_fingerprint(lease_token),
                    "resultReference": result_reference,
                },
            )
        self.repository.reload()
        return self.repository.tasks[task_id]

    def _release_in_transaction(self, session: Session, lease: TaskLeaseRow, outcome: str) -> None:
        now = datetime.now(UTC)
        task = session.get(TaskRow, lease.task_id)
        if task is None:
            raise DomainError("TASK_NOT_FOUND", "The task was not found.", 404)
        payload = dict(task.payload)
        payload.update(
            {
                "status": "queued",
                "statusMessage": "Queued after lease release",
                "updatedAt": now.isoformat(),
            }
        )
        task.status = "queued"
        task.status_message = "Queued after lease release"
        task.updated_at = now
        task.payload = payload
        attempt = session.scalar(
            select(TaskAttemptRow).where(TaskAttemptRow.lease_token == lease.lease_token)
        )
        if attempt:
            attempt.ended_at = now
            attempt.outcome = outcome
            attempt.checkpoint_id = lease.checkpoint_id
        session.delete(lease)
        self._add_event(
            session,
            "task.lease.released",
            f"Released task {task.id} from worker {lease.worker_id}",
            task_id=task.id,
            worker_id=lease.worker_id,
            previous="in_progress",
            new="queued",
            payload={
                "workerId": lease.worker_id,
                "attemptNumber": lease.attempt_number,
                "leaseTokenFingerprint": self._token_fingerprint(lease.lease_token),
                "outcome": outcome,
            },
        )

    def complete_task(self, task_id: str, worker_id: str, lease_token: str, result: str) -> Task:
        now = datetime.now(UTC)
        with self._write() as session:
            try:
                lease = self._require_lease(session, task_id, worker_id, lease_token, now)
            except DomainError:
                attempt = session.scalar(
                    select(TaskAttemptRow).where(TaskAttemptRow.lease_token == lease_token)
                )
                task = session.get(TaskRow, task_id)
                if (
                    attempt
                    and attempt.task_id == task_id
                    and attempt.worker_id == worker_id
                    and attempt.outcome == "completed"
                    and task
                ):
                    return Task.model_validate(task.payload)
                raise
            task = session.get(TaskRow, task_id)
            assert task is not None
            self._require_execution_enabled(session)
            payload = dict(task.payload)
            payload.update(
                {
                    "status": "completed",
                    "statusMessage": "Completed by lease owner",
                    "result": result,
                    "progress": 100,
                    "updatedAt": now.isoformat(),
                    "completedAt": now.isoformat(),
                }
            )
            task.status = "completed"
            task.status_message = "Completed by lease owner"
            task.result = result
            task.progress = 100
            task.updated_at = now
            task.completed_at = now
            task.payload = payload
            attempt = session.scalar(
                select(TaskAttemptRow).where(TaskAttemptRow.lease_token == lease_token)
            )
            assert attempt is not None
            attempt.ended_at = now
            attempt.outcome = "completed"
            attempt.checkpoint_id = lease.checkpoint_id
            session.delete(lease)
            self._add_event(
                session,
                "task.completed",
                f"Worker {worker_id} completed task {task_id}",
                task_id=task_id,
                worker_id=worker_id,
                previous="in_progress",
                new="completed",
                payload={
                    "task": payload,
                    "workerId": worker_id,
                    "attemptNumber": attempt.attempt_number,
                    "leaseTokenFingerprint": self._token_fingerprint(lease_token),
                },
            )
        self.repository.reload()
        return self.repository.tasks[task_id]

    def fail_task(
        self,
        task_id: str,
        worker_id: str,
        lease_token: str,
        error: dict[str, Any],
        retryable: bool,
    ) -> Task:
        now = datetime.now(UTC)
        with self._write() as session:
            lease = self._require_lease(session, task_id, worker_id, lease_token, now)
            task = session.get(TaskRow, task_id)
            assert task is not None
            self._require_execution_enabled(session)
            can_retry = retryable and lease.attempt_number <= task.maximum_retries
            new_status = "retrying" if can_retry else "failed"
            outcome = "retry" if can_retry else "failed"
            message = "Retry queued after worker failure" if can_retry else "Worker failed task"
            payload = dict(task.payload)
            payload.update(
                {
                    "status": new_status,
                    "statusMessage": message,
                    "error": error,
                    "retryCount": max(
                        int(payload.get("retryCount", 0)),
                        min(lease.attempt_number, task.maximum_retries),
                    ),
                    "updatedAt": now.isoformat(),
                }
            )
            task.status = new_status
            task.status_message = message
            task.error = error
            task.retry_count = max(
                task.retry_count, min(lease.attempt_number, task.maximum_retries)
            )
            task.updated_at = now
            task.payload = payload
            attempt = session.scalar(
                select(TaskAttemptRow).where(TaskAttemptRow.lease_token == lease_token)
            )
            assert attempt is not None
            attempt.ended_at = now
            attempt.outcome = outcome
            attempt.error = error
            attempt.checkpoint_id = lease.checkpoint_id
            session.delete(lease)
            self._add_event(
                session,
                "task.retrying" if can_retry else "task.failed",
                f"Worker {worker_id} {outcome} task {task_id}",
                task_id=task_id,
                worker_id=worker_id,
                previous="in_progress",
                new=new_status,
                payload={
                    "task": payload,
                    "workerId": worker_id,
                    "attemptNumber": attempt.attempt_number,
                    "leaseTokenFingerprint": self._token_fingerprint(lease_token),
                    "retryable": retryable,
                    "error": error,
                },
            )
        self.repository.reload()
        return self.repository.tasks[task_id]

    def cancel_task(self, task_id: str) -> Task:
        now = datetime.now(UTC)
        with self._write() as session:
            task = session.get(TaskRow, task_id)
            if task is None:
                raise DomainError("TASK_NOT_FOUND", "The task was not found.", 404)
            if task.status in TERMINAL_TASK_STATES:
                raise DomainError(
                    "TASK_NOT_CANCELLABLE", f"Task in {task.status} cannot be cancelled.", 409
                )
            previous = task.status
            lease = session.get(TaskLeaseRow, task_id)
            event_payload: dict[str, Any] = {}
            if lease:
                attempt = session.scalar(
                    select(TaskAttemptRow).where(TaskAttemptRow.lease_token == lease.lease_token)
                )
                if attempt:
                    attempt.ended_at = now
                    attempt.outcome = "cancelled"
                    attempt.checkpoint_id = lease.checkpoint_id
                event_payload = {
                    "workerId": lease.worker_id,
                    "attemptNumber": lease.attempt_number,
                    "leaseTokenFingerprint": self._token_fingerprint(lease.lease_token),
                }
                session.delete(lease)
            payload = dict(task.payload)
            payload.update(
                {
                    "status": "cancelled",
                    "statusMessage": "Cancelled by user",
                    "updatedAt": now.isoformat(),
                }
            )
            task.status = "cancelled"
            task.status_message = "Cancelled by user"
            task.updated_at = now
            task.payload = payload
            event_payload["task"] = payload
            self._add_event(
                session,
                "task.cancel",
                "Cancelled task and revoked any active lease",
                task_id=task_id,
                previous=previous,
                new="cancelled",
                payload=event_payload,
            )
        self.repository.reload()
        return self.repository.tasks[task_id]

    def recover_expired_leases(self) -> int:
        now = datetime.now(UTC)
        with self.session_factory() as session:
            has_expired = session.scalar(
                select(TaskLeaseRow.task_id).where(TaskLeaseRow.expires_at <= now).limit(1)
            )
        if has_expired is None:
            return 0
        recovered = 0
        with self._write() as session:
            leases = list(
                session.scalars(
                    select(TaskLeaseRow)
                    .where(TaskLeaseRow.expires_at <= now)
                    .order_by(TaskLeaseRow.expires_at, TaskLeaseRow.task_id)
                )
            )
            for lease in leases:
                task = session.get(TaskRow, lease.task_id)
                if task is None:
                    session.delete(lease)
                    continue
                can_retry = lease.attempt_number <= task.maximum_retries
                new_status = "retrying" if can_retry else "failed"
                message = (
                    "Retry queued after expired worker lease"
                    if can_retry
                    else "Failed after exhausting lease attempts"
                )
                payload = dict(task.payload)
                payload.update(
                    {
                        "status": new_status,
                        "statusMessage": message,
                        "retryCount": max(
                            int(payload.get("retryCount", 0)),
                            min(lease.attempt_number, task.maximum_retries),
                        ),
                        "updatedAt": now.isoformat(),
                    }
                )
                if not can_retry:
                    payload["error"] = {
                        "code": "LEASE_EXPIRED",
                        "message": "The worker lease expired after all retries were used.",
                        "retryable": False,
                    }
                task.status = new_status
                task.status_message = message
                task.retry_count = max(
                    task.retry_count, min(lease.attempt_number, task.maximum_retries)
                )
                task.updated_at = now
                task.payload = payload
                if not can_retry:
                    task.error = payload["error"]
                attempt = session.scalar(
                    select(TaskAttemptRow).where(TaskAttemptRow.lease_token == lease.lease_token)
                )
                if attempt:
                    attempt.ended_at = now
                    attempt.outcome = "expired"
                    attempt.checkpoint_id = lease.checkpoint_id
                self._add_event(
                    session,
                    "task.lease.expired",
                    f"Recovered expired lease for task {task.id}",
                    task_id=task.id,
                    worker_id=lease.worker_id,
                    previous="in_progress",
                    new=new_status,
                    payload={
                        "workerId": lease.worker_id,
                        "attemptNumber": lease.attempt_number,
                        "leaseTokenFingerprint": self._token_fingerprint(lease.lease_token),
                        "checkpointId": lease.checkpoint_id,
                    },
                )
                session.delete(lease)
                recovered += 1
        if recovered:
            self.repository.reload()
        return recovered

    def health_counts(self) -> dict[str, int]:
        now = datetime.now(UTC)
        with self.session_factory() as session:
            active_workers = int(
                session.scalar(
                    select(func.count()).select_from(WorkerRow).where(WorkerRow.status == "active")
                )
                or 0
            )
            active_leases = int(session.scalar(select(func.count()).select_from(TaskLeaseRow)) or 0)
            expired_leases = int(
                session.scalar(
                    select(func.count())
                    .select_from(TaskLeaseRow)
                    .where(TaskLeaseRow.expires_at <= now)
                )
                or 0
            )
            workers = list(session.scalars(select(WorkerRow).where(WorkerRow.status == "active")))
            stale_workers = sum(
                1
                for worker in workers
                if _utc(worker.last_heartbeat_at) + timedelta(seconds=worker.lease_seconds) <= now
            )
        return {
            "activeWorkerCount": active_workers,
            "activeLeaseCount": active_leases,
            "expiredLeaseCount": expired_leases,
            "staleWorkerCount": stale_workers,
        }

    @staticmethod
    def _require_lease(
        session: Session,
        task_id: str,
        worker_id: str,
        lease_token: str,
        now: datetime,
    ) -> TaskLeaseRow:
        lease = session.get(TaskLeaseRow, task_id)
        if (
            lease is None
            or lease.worker_id != worker_id
            or lease.lease_token != lease_token
            or _utc(lease.expires_at) <= now
        ):
            raise DomainError(
                "TASK_LEASE_LOST",
                "The task lease expired, was released, or is owned by another worker.",
                409,
            )
        return lease

    @staticmethod
    def _require_execution_enabled(session: Session) -> None:
        state = session.get(SystemStateRow, 1)
        if state and state.emergency_stop:
            raise DomainError(
                "EMERGENCY_STOP_ACTIVE",
                "Task acquisition and terminal worker commits are blocked by emergency stop.",
                423,
            )
