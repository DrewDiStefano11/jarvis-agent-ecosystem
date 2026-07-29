from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from typing import Any

from pydantic import ValidationError
from sqlalchemy import JSON, and_, exists, func, or_, select, text
from sqlalchemy.orm import Session, aliased, sessionmaker

from app.autonomous_worker.errors import AutonomousWorkerError
from app.context.assembler import deterministic_hash
from app.db.models import (
    AgentRuntimeRunRow,
    AuditEventRow,
    ContextAssemblyRow,
    IdentityAgentRow,
    ModelExecutionRow,
    OutboxEventRow,
    SystemStateRow,
    TaskDependencyRow,
    TaskLeaseRow,
    TaskRow,
    WorkerRow,
)
from app.models.agent_runtime import AgentRunSnapshot, AgentRunState
from app.models.autonomous_worker import (
    AutonomousWorkerStatus,
    ModelExecutionResult,
    ModelExecutionStage,
    PlanningReviewResult,
)
from app.models.context import ContextAssembly
from app.models.domain import EventEnvelope

ACTIVE_EXECUTION_STAGES = {
    ModelExecutionStage.PREPARED.value,
    ModelExecutionStage.CALL_STARTED.value,
    ModelExecutionStage.RESPONSE_RECEIVED.value,
    ModelExecutionStage.RESULT_PERSISTED.value,
    ModelExecutionStage.FINALIZATION_PENDING.value,
}


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class ModelExecutionRepository:
    """Durable staged execution/result repository owned by the service layer."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        outbox_max_attempts: int,
    ) -> None:
        self.sessions = sessions
        self.outbox_max_attempts = outbox_max_attempts

    @contextmanager
    def _write(self) -> Iterator[Session]:
        session = self.sessions()
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

    def select_eligible_queued_run(self) -> AgentRunSnapshot | None:
        with self.sessions() as session:
            for page in self.iter_queued_autonomous_run_pages():
                for snapshot in page:
                    if self._eligible_in_session(session, snapshot):
                        return snapshot
            return None

    def select_queued_autonomous_run(self) -> AgentRunSnapshot | None:
        for page in self.iter_queued_autonomous_run_pages():
            return page[0]
        return None

    def list_queued_autonomous_runs(self) -> list[AgentRunSnapshot]:
        return [snapshot for page in self.iter_queued_autonomous_run_pages() for snapshot in page]

    def iter_queued_autonomous_run_pages(
        self,
    ) -> Iterator[list[AgentRunSnapshot]]:
        cursor: tuple[datetime, str] | None = None
        page_size = 100
        while True:
            with self.sessions() as session:
                state = session.get(SystemStateRow, 1)
                if state is not None and state.emergency_stop:
                    return
                query = select(AgentRuntimeRunRow).where(
                    AgentRuntimeRunRow.state == AgentRunState.QUEUED.value
                )
                if cursor is not None:
                    created_at, run_id = cursor
                    query = query.where(
                        or_(
                            AgentRuntimeRunRow.created_at > created_at,
                            and_(
                                AgentRuntimeRunRow.created_at == created_at,
                                AgentRuntimeRunRow.run_id > run_id,
                            ),
                        )
                    )
                rows = list(
                    session.scalars(
                        query.order_by(
                            AgentRuntimeRunRow.created_at, AgentRuntimeRunRow.run_id
                        ).limit(page_size)
                    )
                )
            if not rows:
                return
            cursor = (rows[-1].created_at, rows[-1].run_id)
            snapshots: list[AgentRunSnapshot] = []
            for row in rows:
                snapshot = AgentRunSnapshot.model_validate_json(row.snapshot_json)
                request = snapshot.specification.autonomous_execution
                if request is None or request.execution_type.value != "planning_review":
                    continue
                snapshots.append(snapshot)
            if snapshots:
                yield snapshots
            if len(rows) < page_size:
                return

    def target_identity_active(self, agent_id: str) -> bool:
        with self.sessions() as session:
            target = session.get(IdentityAgentRow, agent_id)
            return bool(
                target is not None and target.lifecycle_state == "active" and target.is_enabled
            )

    def _eligible_in_session(self, session: Session, snapshot: AgentRunSnapshot) -> bool:
        specification = snapshot.specification
        request = specification.autonomous_execution
        assert request is not None
        task = session.get(TaskRow, specification.task_id)
        assembly = session.get(ContextAssemblyRow, request.context_assembly_id)
        target = session.get(IdentityAgentRow, specification.agent_id)
        if (
            task is None
            or task.status not in {"queued", "retrying"}
            or assembly is None
            or assembly.task_id != task.id
            or assembly.status != "completed"
            or not isinstance(assembly.payload, dict)
            or not assembly.payload.get("modelRequest")
            or target is None
            or target.lifecycle_state != "active"
            or not target.is_enabled
        ):
            return False
        dependency = aliased(TaskRow)
        unsatisfied = session.scalar(
            select(
                exists(
                    select(TaskDependencyRow.task_id)
                    .join(
                        dependency,
                        dependency.id == TaskDependencyRow.dependency_task_id,
                    )
                    .where(
                        TaskDependencyRow.task_id == task.id,
                        TaskDependencyRow.dependency_type == "requires",
                        dependency.status != "completed",
                    )
                )
            )
        )
        return not bool(unsatisfied)

    def load_context_assembly(self, assembly_id: str) -> ContextAssembly:
        with self.sessions() as session:
            row = session.get(ContextAssemblyRow, assembly_id)
            if row is None:
                raise AutonomousWorkerError("CONTEXT_ASSEMBLY_UNAVAILABLE", status_code=404)
            assembly = ContextAssembly.model_validate(row.payload)
        self.validate_context_assembly_hash(assembly)
        return assembly

    @staticmethod
    def validate_context_assembly_hash(assembly: ContextAssembly) -> None:
        request = assembly.modelRequest
        if request is None:
            raise AutonomousWorkerError("CONTEXT_ASSEMBLY_UNAVAILABLE")
        recomputed = deterministic_hash(
            {
                "generation": request.generation,
                "messages": [message.model_dump(mode="json") for message in request.messages],
                "policyVersion": assembly.policyVersion,
                "schemaVersion": request.schemaVersion,
            }
        )
        if (
            recomputed != request.requestHash
            or recomputed != assembly.requestHash
            or recomputed != assembly.manifest.requestHash
        ):
            raise AutonomousWorkerError("CONTEXT_ASSEMBLY_MISMATCH")

    @staticmethod
    def execution_id(run_id: str, attempt_id: str) -> str:
        digest = sha256(f"{run_id}:{attempt_id}:planning_review:1.0".encode()).hexdigest()
        return f"exec-{digest[:48]}"

    def prepare(
        self,
        *,
        snapshot: AgentRunSnapshot,
        attempt_id: str,
        assembly: ContextAssembly,
        worker_id: str,
        task_attempt_number: int,
        lease_token: str,
        execution_request_hash: str,
    ) -> ModelExecutionResult:
        execution_id = self.execution_id(snapshot.specification.run_id, attempt_id)
        now = datetime.now(UTC)
        with self._write() as session:
            existing = session.get(ModelExecutionRow, execution_id)
            if existing is not None:
                return self._contract(existing)
            row = ModelExecutionRow(
                execution_id=execution_id,
                runtime_run_id=snapshot.specification.run_id,
                runtime_attempt_id=attempt_id,
                task_id=snapshot.specification.task_id,
                target_agent_id=snapshot.specification.agent_id,
                context_assembly_id=assembly.id,
                worker_id=worker_id,
                task_attempt_number=task_attempt_number,
                lease_token_fingerprint=sha256(lease_token.encode()).hexdigest(),
                stage=ModelExecutionStage.PREPARED.value,
                schema_version="1.0",
                request_hash=assembly.requestHash,
                execution_request_hash=execution_request_hash,
                request_count=0,
                requires_human_review=False,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            self._emit(
                session,
                row,
                "model.request.prepared",
                {"requestHash": row.request_hash},
            )
            session.flush()
            return self._contract(row)

    def get_by_run(self, run_id: str) -> ModelExecutionResult | None:
        with self.sessions() as session:
            row = session.scalar(
                select(ModelExecutionRow)
                .where(ModelExecutionRow.runtime_run_id == run_id)
                .order_by(ModelExecutionRow.created_at.desc())
                .limit(1)
            )
            return None if row is None else self._contract(row)

    def get(self, execution_id: str) -> ModelExecutionResult | None:
        with self.sessions() as session:
            row = session.get(ModelExecutionRow, execution_id)
            return None if row is None else self._contract(row)

    def list_for_task(self, task_id: str) -> list[ModelExecutionResult]:
        with self.sessions() as session:
            rows = session.scalars(
                select(ModelExecutionRow)
                .where(ModelExecutionRow.task_id == task_id)
                .order_by(ModelExecutionRow.created_at, ModelExecutionRow.execution_id)
            )
            return [self._contract(row) for row in rows]

    def record_call_started(
        self, execution_id: str, *, worker_id: str, lease_token: str, request_count: int
    ) -> ModelExecutionResult:
        return self._transition(
            execution_id,
            ModelExecutionStage.CALL_STARTED,
            "model.repair.started" if request_count > 1 else "model.call.started",
            worker_id=worker_id,
            lease_token=lease_token,
            request_count=request_count,
        )

    def record_validation_failed(
        self,
        execution_id: str,
        *,
        worker_id: str,
        lease_token: str,
        request_count: int,
    ) -> ModelExecutionResult:
        return self._transition(
            execution_id,
            ModelExecutionStage.RESPONSE_RECEIVED,
            "model.output.validation_failed",
            worker_id=worker_id,
            lease_token=lease_token,
            request_count=request_count,
        )

    def record_response_received(
        self,
        execution_id: str,
        *,
        worker_id: str,
        lease_token: str,
        request_count: int,
    ) -> ModelExecutionResult:
        return self._transition(
            execution_id,
            ModelExecutionStage.RESPONSE_RECEIVED,
            "model.call.succeeded",
            worker_id=worker_id,
            lease_token=lease_token,
            request_count=request_count,
        )

    def persist_result(
        self,
        execution_id: str,
        *,
        worker_id: str,
        lease_token: str,
        result: PlanningReviewResult,
        provider: str,
        model: str,
        input_tokens: int | None,
        output_tokens: int | None,
        request_count: int,
        latency_ms: float,
        finish_reason: str | None,
        estimated_cost_usd: float | None,
    ) -> ModelExecutionResult:
        payload = result.model_dump(mode="json")
        result_hash = sha256(canonical_json(payload).encode()).hexdigest()
        now = datetime.now(UTC)
        with self._write() as session:
            row = self._require_row(session, execution_id)
            self._require_fence(session, row, worker_id, lease_token, now)
            if row.result_hash is not None:
                if row.result_hash != result_hash:
                    raise AutonomousWorkerError("MODEL_RESULT_CONFLICT")
                return self._contract(row)
            row.stage = ModelExecutionStage.RESULT_PERSISTED.value
            row.result_json = payload
            row.result_hash = result_hash
            row.provider = provider
            row.model = model
            row.input_tokens = input_tokens
            row.output_tokens = output_tokens
            row.request_count = request_count
            row.latency_ms = latency_ms
            row.finish_reason = finish_reason
            row.estimated_cost_usd = estimated_cost_usd
            row.requires_human_review = result.requiresHumanReview
            row.updated_at = now
            self._emit(
                session,
                row,
                "model.result.persisted",
                {
                    "provider": provider,
                    "model": model,
                    "resultHash": result_hash,
                    "requestCount": request_count,
                },
            )
            session.flush()
            return self._contract(row)

    def mark_finalization_pending(
        self, execution_id: str, *, worker_id: str, lease_token: str
    ) -> ModelExecutionResult:
        return self._transition(
            execution_id,
            ModelExecutionStage.FINALIZATION_PENDING,
            "model.execution.finalization_pending",
            worker_id=worker_id,
            lease_token=lease_token,
        )

    def mark_completed(self, execution_id: str) -> ModelExecutionResult:
        now = datetime.now(UTC)
        with self._write() as session:
            row = self._require_row(session, execution_id)
            if row.stage == ModelExecutionStage.COMPLETED.value:
                return self._contract(row)
            if row.result_hash is None:
                raise AutonomousWorkerError("MODEL_RESULT_PERSISTENCE_FAILED")
            row.stage = ModelExecutionStage.COMPLETED.value
            row.updated_at = now
            row.completed_at = now
            self._emit(session, row, "model.execution.completed", {"resultHash": row.result_hash})
            session.flush()
            return self._contract(row)

    def mark_failed(
        self,
        execution_id: str,
        failure_code: str,
        *,
        human_review: bool = False,
    ) -> ModelExecutionResult:
        now = datetime.now(UTC)
        with self._write() as session:
            row = self._require_row(session, execution_id)
            row.stage = (
                ModelExecutionStage.HUMAN_REVIEW_REQUIRED.value
                if human_review
                else ModelExecutionStage.FAILED.value
            )
            row.failure_code = failure_code[:80]
            if row.result_hash is None:
                row.requires_human_review = human_review
            row.updated_at = now
            row.completed_at = now
            self._emit(
                session,
                row,
                ("model.execution.review_required" if human_review else "model.execution.failed"),
                {"failureCode": row.failure_code},
            )
            session.flush()
            return self._contract(row)

    def recoverable_results(self) -> list[ModelExecutionResult]:
        with self.sessions() as session:
            rows = session.scalars(
                select(ModelExecutionRow)
                .where(
                    ModelExecutionRow.result_hash.is_not(None),
                    ModelExecutionRow.stage.in_(
                        [
                            ModelExecutionStage.RESULT_PERSISTED.value,
                            ModelExecutionStage.FINALIZATION_PENDING.value,
                        ]
                    ),
                )
                .order_by(ModelExecutionRow.updated_at, ModelExecutionRow.execution_id)
                .limit(100)
            )
            return [self._contract(row) for row in rows]

    def recoverable_uncommitted(self) -> list[ModelExecutionResult]:
        with self.sessions() as session:
            rows = session.scalars(
                select(ModelExecutionRow)
                .where(
                    ModelExecutionRow.result_hash.is_(None),
                    ModelExecutionRow.stage.in_(
                        [
                            ModelExecutionStage.PREPARED.value,
                            ModelExecutionStage.CALL_STARTED.value,
                            ModelExecutionStage.RESPONSE_RECEIVED.value,
                        ]
                    ),
                )
                .order_by(ModelExecutionRow.updated_at, ModelExecutionRow.execution_id)
                .limit(100)
            )
            return [self._contract(row) for row in rows]

    def reclaim_uncommitted(
        self,
        execution_id: str,
        *,
        worker_id: str,
        lease_token: str,
        task_attempt_number: int,
    ) -> ModelExecutionResult:
        now = datetime.now(UTC)
        with self._write() as session:
            row = self._require_row(session, execution_id)
            if row.result_hash is not None or row.stage not in {
                ModelExecutionStage.PREPARED.value,
                ModelExecutionStage.CALL_STARTED.value,
                ModelExecutionStage.RESPONSE_RECEIVED.value,
            }:
                raise AutonomousWorkerError("MODEL_RESULT_CONFLICT")
            state = session.get(SystemStateRow, 1)
            if state is not None and state.emergency_stop:
                raise AutonomousWorkerError("EXECUTION_EMERGENCY_STOPPED", status_code=423)
            lease = session.get(TaskLeaseRow, row.task_id)
            if (
                lease is None
                or lease.worker_id != worker_id
                or lease.lease_token != lease_token
                or lease.expires_at.replace(tzinfo=UTC) <= now
            ):
                raise AutonomousWorkerError("EXECUTION_LEASE_LOST")
            row.worker_id = worker_id
            row.task_attempt_number = task_attempt_number
            row.lease_token_fingerprint = sha256(lease_token.encode()).hexdigest()
            row.stage = ModelExecutionStage.PREPARED.value
            row.updated_at = now
            self._emit(
                session,
                row,
                "model.execution.recovered",
                {"previousCallMayHaveCompleted": True},
            )
            session.flush()
            return self._contract(row)

    def status(
        self, *, enabled: bool, execution_mode: str, provider_ready: bool
    ) -> AutonomousWorkerStatus:
        with self.sessions() as session:
            active = int(
                session.scalar(
                    select(func.count())
                    .select_from(ModelExecutionRow)
                    .where(ModelExecutionRow.stage.in_(ACTIVE_EXECUTION_STAGES))
                )
                or 0
            )
            active_rows = list(
                session.scalars(
                    select(ModelExecutionRow)
                    .where(ModelExecutionRow.stage.in_(ACTIVE_EXECUTION_STAGES))
                    .order_by(ModelExecutionRow.updated_at)
                    .limit(1000)
                )
            )
            leases = {
                row.task_id: row
                for row in session.scalars(
                    select(TaskLeaseRow).where(
                        TaskLeaseRow.task_id.in_([item.task_id for item in active_rows])
                    )
                )
            }
            now = datetime.now(UTC)
            ownership_lost = any(
                (lease := leases.get(row.task_id)) is None
                or lease.worker_id != row.worker_id
                or lease.expires_at.replace(tzinfo=UTC) <= now
                for row in active_rows
            )
            corrupt_results = int(
                session.scalar(
                    select(func.count())
                    .select_from(ModelExecutionRow)
                    .where(
                        or_(
                            and_(
                                ModelExecutionRow.result_hash.is_(None),
                                ModelExecutionRow.result_json.is_not(None),
                                ModelExecutionRow.result_json != JSON.NULL,
                            ),
                            and_(
                                ModelExecutionRow.result_hash.is_not(None),
                                or_(
                                    ModelExecutionRow.result_json.is_(None),
                                    ModelExecutionRow.result_json == JSON.NULL,
                                ),
                            ),
                        )
                    )
                )
                or 0
            )
            corrupt_results += sum(
                1
                for result_hash, result_json in session.execute(
                    select(
                        ModelExecutionRow.result_hash,
                        ModelExecutionRow.result_json,
                    ).where(
                        ModelExecutionRow.result_hash.is_not(None),
                        ModelExecutionRow.result_json.is_not(None),
                    )
                )
                if result_json is None
                or sha256(canonical_json(result_json).encode()).hexdigest() != result_hash
            )
            exhausted_outbox = int(
                session.scalar(
                    select(func.count())
                    .select_from(OutboxEventRow)
                    .where(
                        OutboxEventRow.event_type.like("model.%"),
                        OutboxEventRow.status == "failed",
                        OutboxEventRow.publish_attempt_count >= self.outbox_max_attempts,
                    )
                )
                or 0
            )
            completed = int(
                session.scalar(
                    select(func.count())
                    .select_from(ModelExecutionRow)
                    .where(ModelExecutionRow.stage == ModelExecutionStage.COMPLETED.value)
                )
                or 0
            )
            failed = int(
                session.scalar(
                    select(func.count())
                    .select_from(ModelExecutionRow)
                    .where(ModelExecutionRow.stage == ModelExecutionStage.FAILED.value)
                )
                or 0
            )
            reviews = int(
                session.scalar(
                    select(func.count())
                    .select_from(ModelExecutionRow)
                    .where(
                        ModelExecutionRow.stage == ModelExecutionStage.HUMAN_REVIEW_REQUIRED.value
                    )
                )
                or 0
            )
            last_success = session.scalar(
                select(func.max(ModelExecutionRow.completed_at)).where(
                    ModelExecutionRow.stage == ModelExecutionStage.COMPLETED.value
                )
            )
            autonomous_workers = list(
                session.scalars(
                    select(WorkerRow).order_by(WorkerRow.last_heartbeat_at.desc()).limit(1000)
                )
            )
            last_heartbeat = max(
                (
                    row.last_heartbeat_at
                    for row in autonomous_workers
                    if row.metadata_json.get("kind") == "autonomous_planning_review"
                ),
                default=None,
            )
            queued = 1 if self.select_eligible_queued_run() is not None else 0
        status = "disabled"
        reason = None
        if enabled:
            status = "healthy" if execution_mode == "local_only" and provider_ready else "degraded"
            if execution_mode != "local_only":
                reason = "model_execution_disabled"
            elif not provider_ready:
                reason = "no_local_provider_available"
            elif ownership_lost:
                status = "degraded"
                reason = "execution_lease_lost"
            elif corrupt_results:
                status = "degraded"
                reason = "model_result_corrupt"
            elif exhausted_outbox:
                status = "degraded"
                reason = "model_execution_outbox_exhausted"
        return AutonomousWorkerStatus(
            enabled=enabled,
            modelExecutionMode=execution_mode,
            activeExecutionCount=active,
            queuedEligibleRuntimeCount=queued,
            completedExecutionCount=completed,
            failedExecutionCount=failed,
            reviewRequiredCount=reviews,
            lastWorkerHeartbeat=last_heartbeat,
            lastSuccessfulExecutionAt=last_success,
            providerReady=provider_ready,
            status=status,
            reasonCode=reason,
        )

    def _transition(
        self,
        execution_id: str,
        stage: ModelExecutionStage,
        event_type: str,
        *,
        worker_id: str,
        lease_token: str,
        request_count: int | None = None,
    ) -> ModelExecutionResult:
        now = datetime.now(UTC)
        with self._write() as session:
            row = self._require_row(session, execution_id)
            self._require_fence(session, row, worker_id, lease_token, now)
            row.stage = stage.value
            row.updated_at = now
            if request_count is not None:
                row.request_count = request_count
            self._emit(
                session,
                row,
                event_type,
                {"requestCount": row.request_count},
            )
            session.flush()
            return self._contract(row)

    @staticmethod
    def _require_row(session: Session, execution_id: str) -> ModelExecutionRow:
        row = session.get(ModelExecutionRow, execution_id)
        if row is None:
            raise AutonomousWorkerError("MODEL_RESULT_PERSISTENCE_FAILED", status_code=404)
        return row

    @staticmethod
    def _require_fence(
        session: Session,
        row: ModelExecutionRow,
        worker_id: str,
        lease_token: str,
        now: datetime,
    ) -> None:
        state = session.get(SystemStateRow, 1)
        if state is not None and state.emergency_stop:
            raise AutonomousWorkerError("EXECUTION_EMERGENCY_STOPPED", status_code=423)
        lease = session.get(TaskLeaseRow, row.task_id)
        if (
            lease is None
            or lease.worker_id != worker_id
            or lease.lease_token != lease_token
            or lease.expires_at.replace(tzinfo=UTC) <= now
        ):
            raise AutonomousWorkerError("EXECUTION_LEASE_LOST")

    def _emit(
        self,
        session: Session,
        row: ModelExecutionRow,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        identity = canonical_json(
            {"executionId": row.execution_id, "eventType": event_type}
        ).encode()
        digest = sha256(identity).hexdigest()
        event_id = f"model-{digest[:48]}"
        if session.get(OutboxEventRow, event_id) is not None:
            return
        state = session.get(SystemStateRow, 1)
        if state is None:
            raise AutonomousWorkerError("MODEL_RESULT_PERSISTENCE_FAILED")
        now = datetime.now(UTC)
        state.current_sequence_number += 1
        state.updated_at = now
        safe_payload = {
            "executionId": row.execution_id,
            "taskId": row.task_id,
            "runtimeRunId": row.runtime_run_id,
            "runtimeAttemptId": row.runtime_attempt_id,
            "workerId": row.worker_id,
            "targetAgentId": row.target_agent_id,
            **payload,
        }
        envelope = EventEnvelope(
            eventId=event_id,
            eventType=event_type,
            timestamp=now,
            sequenceNumber=state.current_sequence_number,
            eventSessionId=state.event_session_id,
            correlationId=row.runtime_run_id,
            taskId=row.task_id,
            agentId=row.target_agent_id,
            source="autonomous-worker",
            payload=safe_payload,
        ).model_dump(mode="json")
        session.add(
            OutboxEventRow(
                id=event_id,
                event_type=event_type,
                envelope=envelope,
                correlation_id=row.runtime_run_id,
                event_session_id=state.event_session_id,
                sequence_number=state.current_sequence_number,
                status="pending",
                created_at=now,
                publish_attempt_count=0,
            )
        )
        session.add(
            AuditEventRow(
                id=f"audit-{digest[:48]}",
                event_type=event_type,
                actor=row.worker_id,
                agent_id=None,
                task_id=row.task_id,
                approval_id=None,
                previous_state=None,
                new_state=row.stage,
                correlation_id=row.runtime_run_id,
                sequence_number=state.current_sequence_number,
                event_session_id=state.event_session_id,
                timestamp=now,
                payload={
                    "summary": event_type,
                    "payload": safe_payload,
                    "artifactIds": [],
                },
                schema_version="1.0",
            )
        )

    @staticmethod
    def _contract(row: ModelExecutionRow) -> ModelExecutionResult:
        result: PlanningReviewResult | None = None
        if row.result_json is not None:
            actual_hash = sha256(canonical_json(row.result_json).encode()).hexdigest()
            if row.result_hash is None or actual_hash != row.result_hash:
                raise AutonomousWorkerError("MODEL_RESULT_CORRUPT")
            try:
                result = PlanningReviewResult.model_validate(row.result_json)
            except ValidationError as exc:
                raise AutonomousWorkerError("MODEL_RESULT_CORRUPT") from exc
            if row.requires_human_review != result.requiresHumanReview:
                raise AutonomousWorkerError("MODEL_RESULT_CORRUPT")
        elif row.result_hash is not None:
            raise AutonomousWorkerError("MODEL_RESULT_CORRUPT")
        return ModelExecutionResult(
            executionId=row.execution_id,
            runtimeRunId=row.runtime_run_id,
            runtimeAttemptId=row.runtime_attempt_id,
            taskId=row.task_id,
            targetAgentId=row.target_agent_id,
            contextAssemblyId=row.context_assembly_id,
            workerId=row.worker_id,
            stage=row.stage,
            requestHash=row.request_hash,
            executionRequestHash=row.execution_request_hash,
            resultHash=row.result_hash,
            provider=row.provider,
            model=row.model,
            result=result,
            inputTokens=row.input_tokens,
            outputTokens=row.output_tokens,
            requestCount=row.request_count,
            latencyMs=float(row.latency_ms) if row.latency_ms is not None else None,
            finishReason=row.finish_reason,
            estimatedCostUsd=(
                float(row.estimated_cost_usd)
                if isinstance(row.estimated_cost_usd, Decimal)
                else row.estimated_cost_usd
            ),
            requiresHumanReview=(
                result.requiresHumanReview if result is not None else row.requires_human_review
            ),
            failureCode=row.failure_code,
            createdAt=row.created_at,
            updatedAt=row.updated_at,
            completedAt=row.completed_at,
        )
