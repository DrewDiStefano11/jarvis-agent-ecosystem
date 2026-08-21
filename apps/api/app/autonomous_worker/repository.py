from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
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

# Durable marker for an attempt whose persisted plan was reviewed and returned
# for one bounded revision cycle.
REVISION_REQUESTED_FAILURE_CODE = "review_revision_requested"
REVISION_EXHAUSTED_FAILURE_CODE = "review_revision_exhausted"


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

    def count_eligible_queued_runs(self) -> int:
        count = 0
        with self.sessions() as session:
            for page in self.iter_queued_autonomous_run_pages():
                count += sum(1 for snapshot in page if self._eligible_in_session(session, snapshot))
        return count

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

    def iter_pre_execution_pause_recovery_pages(
        self,
    ) -> Iterator[list[AgentRunSnapshot]]:
        yield from self._iter_runtime_recovery_pages(
            {
                AgentRunState.PAUSE_REQUESTED,
                AgentRunState.PAUSED,
            }
        )

    def iter_preparation_transition_recovery_pages(
        self,
    ) -> Iterator[list[AgentRunSnapshot]]:
        yield from self._iter_runtime_recovery_pages(
            {
                AgentRunState.CLAIMED,
                AgentRunState.STARTING,
            }
        )

    def iter_revision_cycle_pages(self) -> Iterator[list[AgentRunSnapshot]]:
        """Yield runs owed exactly one bounded review-revision planning cycle.

        A candidate is a blocked autonomous run whose durable execution history
        contains a review-revision marker and no still-active execution row, so
        the next deterministic attempt cannot duplicate live work.
        """

        yield from self._iter_runtime_recovery_pages(
            {AgentRunState.BLOCKED},
            filters=(
                exists().where(
                    and_(
                        ModelExecutionRow.runtime_run_id == AgentRuntimeRunRow.run_id,
                        ModelExecutionRow.failure_code == REVISION_REQUESTED_FAILURE_CODE,
                    )
                ),
                ~exists().where(
                    and_(
                        ModelExecutionRow.runtime_run_id == AgentRuntimeRunRow.run_id,
                        ModelExecutionRow.stage.in_(ACTIVE_EXECUTION_STAGES),
                    )
                ),
            ),
        )

    def _iter_runtime_recovery_pages(
        self,
        states: set[AgentRunState],
        *,
        filters: tuple[Any, ...] | None = None,
    ) -> Iterator[list[AgentRunSnapshot]]:
        cursor: tuple[datetime, str] | None = None
        page_size = 100
        if filters is None:
            filters = (
                ~exists().where(ModelExecutionRow.runtime_run_id == AgentRuntimeRunRow.run_id),
            )
        while True:
            with self.sessions() as session:
                query = select(AgentRuntimeRunRow).where(
                    AgentRuntimeRunRow.state.in_([state.value for state in states]),
                    *filters,
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
                            AgentRuntimeRunRow.created_at,
                            AgentRuntimeRunRow.run_id,
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
            # The preparation row is the durable recovery marker.  Fence its
            # transaction just like result-stage transitions so a stale worker
            # cannot manufacture recovery state after lease loss or stop.
            self._require_task_fence(
                session,
                snapshot.specification.task_id,
                worker_id,
                lease_token,
                now,
            )
            self._require_target_active(session, snapshot.specification.agent_id)
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

    def count_for_run(self, run_id: str) -> int:
        """Return the number of durable planning attempts recorded for one run.

        This is the authoritative revision-cycle index: exactly one execution row
        exists per deterministic runtime attempt, so the count identifies the next
        attempt without inspecting "latest" result content.
        """

        with self.sessions() as session:
            return int(
                session.scalar(
                    select(func.count())
                    .select_from(ModelExecutionRow)
                    .where(ModelExecutionRow.runtime_run_id == run_id)
                )
                or 0
            )

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

    def assert_advance_allowed(
        self, execution_id: str, *, worker_id: str, lease_token: str
    ) -> None:
        """Revalidate emergency stop, exact lease ownership, and target eligibility.

        The target identity row is locked for the duration of the check so a
        suspension racing a durable orchestration advance fails closed instead of
        being observed as stale eligibility.
        """

        now = datetime.now(UTC)
        with self._write() as session:
            row = self._require_row(session, execution_id)
            self._require_fence(session, row, worker_id, lease_token, now)

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
        return [execution for page in self.iter_recoverable_result_pages() for execution in page]

    def recoverable_uncommitted(self) -> list[ModelExecutionResult]:
        return [
            execution for page in self.iter_recoverable_uncommitted_pages() for execution in page
        ]

    def iter_recoverable_result_pages(
        self,
        *,
        skip_corrupt: bool = False,
    ) -> Iterator[list[ModelExecutionResult]]:
        yield from self._iter_recoverable_pages(
            result_persisted=True,
            skip_corrupt=skip_corrupt,
        )

    def iter_recoverable_uncommitted_pages(
        self,
    ) -> Iterator[list[ModelExecutionResult]]:
        yield from self._iter_recoverable_pages(
            result_persisted=False,
            skip_corrupt=False,
        )

    def _iter_recoverable_pages(
        self,
        *,
        result_persisted: bool,
        skip_corrupt: bool,
    ) -> Iterator[list[ModelExecutionResult]]:
        cursor: tuple[datetime, str] | None = None
        page_size = 100
        stages = (
            [
                ModelExecutionStage.RESULT_PERSISTED.value,
                ModelExecutionStage.FINALIZATION_PENDING.value,
            ]
            if result_persisted
            else [
                ModelExecutionStage.PREPARED.value,
                ModelExecutionStage.CALL_STARTED.value,
                ModelExecutionStage.RESPONSE_RECEIVED.value,
            ]
        )
        while True:
            with self.sessions() as session:
                result_predicate = (
                    ModelExecutionRow.result_hash.is_not(None)
                    if result_persisted
                    else ModelExecutionRow.result_hash.is_(None)
                )
                query = select(ModelExecutionRow).where(
                    result_predicate,
                    ModelExecutionRow.stage.in_(stages),
                )
                if cursor is not None:
                    updated_at, execution_id = cursor
                    query = query.where(
                        or_(
                            ModelExecutionRow.updated_at > updated_at,
                            and_(
                                ModelExecutionRow.updated_at == updated_at,
                                ModelExecutionRow.execution_id > execution_id,
                            ),
                        )
                    )
                rows = list(
                    session.scalars(
                        query.order_by(
                            ModelExecutionRow.updated_at,
                            ModelExecutionRow.execution_id,
                        ).limit(page_size)
                    )
                )
                contracts: list[ModelExecutionResult] = []
                for row in rows:
                    if not skip_corrupt:
                        contracts.append(self._contract(row))
                        continue
                    try:
                        contracts.append(self._contract(row))
                    except (AutonomousWorkerError, ValidationError):
                        # Health remains degraded for corrupt results, but one bad
                        # candidate must not starve later rows in this bounded page.
                        continue
            if not rows:
                return
            yield contracts
            cursor = (rows[-1].updated_at, rows[-1].execution_id)
            if len(rows) < page_size:
                return

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
            self._require_target_active(session, row.target_agent_id)
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
        runtime_state_corrupt = False
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
            worker_rows = list(
                session.scalars(
                    select(WorkerRow).order_by(WorkerRow.last_heartbeat_at.desc()).limit(1000)
                )
            )
            if any(not isinstance(row.metadata_json, dict) for row in worker_rows):
                runtime_state_corrupt = True
            autonomous_workers = [
                row
                for row in worker_rows
                if isinstance(row.metadata_json, dict)
                and row.metadata_json.get("kind") == "autonomous_planning_review"
            ]
            last_heartbeat = max(
                (row.last_heartbeat_at for row in autonomous_workers),
                default=None,
            )
            live_worker = any(
                row.status == "active"
                and row.last_heartbeat_at.replace(tzinfo=UTC) + timedelta(seconds=row.lease_seconds)
                > now
                for row in autonomous_workers
            )
            try:
                queued = self.count_eligible_queued_runs()
            except (AutonomousWorkerError, ValidationError, TypeError, ValueError):
                queued = 0
                runtime_state_corrupt = True
        status = "disabled"
        reason = None
        if corrupt_results or runtime_state_corrupt:
            status = "degraded"
            reason = (
                "model_result_corrupt" if corrupt_results else "autonomous_runtime_state_corrupt"
            )
        elif exhausted_outbox:
            status = "degraded"
            reason = "model_execution_outbox_exhausted"
        elif active and ownership_lost:
            status = "degraded"
            reason = "execution_lease_lost"
        elif enabled:
            status = "healthy" if execution_mode == "local_only" and provider_ready else "degraded"
            if execution_mode != "local_only":
                reason = "model_execution_disabled"
            elif not provider_ready:
                reason = "no_local_provider_available"
            elif not live_worker:
                status = "degraded"
                reason = "autonomous_worker_unavailable"
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
    def _require_task_fence(
        session: Session,
        task_id: str,
        worker_id: str,
        lease_token: str,
        now: datetime,
    ) -> None:
        state = session.get(SystemStateRow, 1)
        if state is not None and state.emergency_stop:
            raise AutonomousWorkerError("EXECUTION_EMERGENCY_STOPPED", status_code=423)
        lease = session.get(TaskLeaseRow, task_id)
        if (
            lease is None
            or lease.worker_id != worker_id
            or lease.lease_token != lease_token
            or lease.expires_at.replace(tzinfo=UTC) <= now
        ):
            raise AutonomousWorkerError("EXECUTION_LEASE_LOST")

    @classmethod
    def _require_fence(
        cls,
        session: Session,
        row: ModelExecutionRow,
        worker_id: str,
        lease_token: str,
        now: datetime,
    ) -> None:
        cls._require_task_fence(session, row.task_id, worker_id, lease_token, now)
        cls._require_target_active(session, row.target_agent_id)

    @staticmethod
    def _require_target_active(session: Session, agent_id: str) -> None:
        target = session.scalar(
            select(IdentityAgentRow).where(IdentityAgentRow.id == agent_id).with_for_update()
        )
        if target is None or target.lifecycle_state != "active" or not target.is_enabled:
            raise AutonomousWorkerError("RUNTIME_EXECUTION_NOT_ELIGIBLE")

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
