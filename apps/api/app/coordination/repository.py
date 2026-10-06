"""Bounded coordination aggregate serialized by the control-plane database fence."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.agent_runtime.repository import RuntimeExecutionFence
from app.coordination.verification import require_node_verdict
from app.core.errors import DomainError
from app.db.models import (
    AgentRuntimeRunRow,
    AuditEventRow,
    CoordinationRow,
    OutboxEventRow,
    SystemStateRow,
    TaskDecompositionRow,
    TaskLeaseRow,
    TaskRow,
)
from app.decomposition.service import fingerprint
from app.models.agent_runtime import AgentRunSnapshot
from app.models.coordination import (
    CoordinatedSubtask,
    CoordinationFailure,
    CoordinationRecord,
    CoordinationSynthesis,
    SpecialistResult,
    SynthesisResult,
    execution_ready_keys,
)
from app.models.decomposition import DecompositionRecord, topological_keys
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

    def current_by_id(self, record_id: str) -> CoordinationRecord | None:
        with self.sessions() as session:
            row = session.get(CoordinationRow, record_id)
            return CoordinationRecord.model_validate(row.payload) if row else None

    def unfinished(self) -> list[CoordinationRecord]:
        with self.sessions() as session:
            rows = list(session.scalars(select(CoordinationRow).order_by(CoordinationRow.id)))
            records = [CoordinationRecord.model_validate(row.payload) for row in rows]
            return [
                record
                for record in records
                if record.status not in {"completed", "failed", "blocked"}
            ]

    def fence_for_worker(self, task_id: str, worker_id: str) -> RuntimeExecutionFence | None:
        with self.sessions() as session:
            row = session.get(TaskLeaseRow, task_id)
            if (
                row is None
                or row.worker_id != worker_id
                or row.expires_at.replace(tzinfo=UTC) <= datetime.now(UTC)
            ):
                return None
            return RuntimeExecutionFence(
                task_id=task_id,
                worker_id=worker_id,
                lease_token=row.lease_token,
            )

    @staticmethod
    def _live(
        session, task_id, decomposition_id, run_id, fence, validate_live, *, completing=False
    ):
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
            *(["succeeded"] if completing else []),
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
                synthesis=CoordinationSynthesis(
                    runtimeRunId=coordination_id(decomposition_id, "synthesis")
                ),
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
            if record.status != "active":
                return None
            completed = frozenset(n.key for n in record.nodes if n.status == "succeeded")
            owned = frozenset(n.key for n in record.nodes if n.status in {"claimed", "running"})
            if owned:
                return None
            ready = execution_ready_keys(
                graph,
                completed=completed,
                owned=owned,
                eligible_agents=eligible,
                execution_permitted=True,
            )
            ready = [
                key
                for key in ready
                if next(n for n in record.nodes if n.key == key).status in {"pending", "retrying"}
                and (
                    next(n for n in record.nodes if n.key == key).retryEligibleAt is None
                    or next(n for n in record.nodes if n.key == key).retryEligibleAt
                    <= datetime.now(UTC)
                )
            ]
            if not ready:
                return None
            node = next(n for n in record.nodes if n.key == ready[0])
            node.status = "claimed"
            node.runtimeRunId = coordination_id(
                record.decompositionId, node.subtaskId, f"run-{node.attemptCount + 1}"
            )
            node.runtimeAttemptId = coordination_id(
                node.runtimeRunId, f"attempt-{node.attemptCount + 1}"
            )
            node.retryEligibleAt = None
            record.updatedAt = datetime.now(UTC)
            row.payload = record.model_dump(mode="json")
            self._event(session, record, "subtask_claimed", node)
            return node

    def begin_attempt(
        self,
        record_id: str,
        subtask_id: str,
        runtime_attempt_id: str,
        fence: RuntimeExecutionFence,
        validate_live: LiveValidator,
    ) -> CoordinatedSubtask | None:
        with self._write() as session:
            row, record = self._record(session, record_id)
            _, graph, _, _ = self._live_record(session, record, fence, validate_live)
            node = self._node(record, subtask_id, runtime_attempt_id)
            if node.status == "running":
                return None
            if node.status != "claimed":
                raise DomainError("COORDINATION_ATTEMPT_STALE", "Attempt is not claimable.", 409)
            node.status = "running"
            node.attemptCount += 1
            node.dispatchLeaseFingerprint = sha256(fence.lease_token.encode()).hexdigest()
            record.updatedAt = datetime.now(UTC)
            row.payload = record.model_dump(mode="json")
            self._event(session, record, "subtask_started", node)
            return node

    def record_success(
        self,
        record_id: str,
        subtask_id: str,
        runtime_attempt_id: str,
        *,
        summary: str,
        result_digest: str,
        evidence: list[str],
        checkpoint_id: str,
        fence: RuntimeExecutionFence,
        validate_live: LiveValidator,
    ) -> CoordinationRecord:
        with self._write() as session:
            row, record = self._record(session, record_id)
            _, _, parent, _ = self._live_record(session, record, fence, validate_live)
            node = self._node(record, subtask_id, runtime_attempt_id)
            if node.status == "succeeded":
                return record
            if node.status != "running":
                raise DomainError("COORDINATION_ATTEMPT_STALE", "Attempt is not running.", 409)
            validated = self._require_checkpoint(
                session,
                node.runtimeRunId,
                runtime_attempt_id,
                checkpoint_id,
                result_digest,
                summary,
                SpecialistResult,
            )
            graph = DecompositionRecord.model_validate(
                session.get(TaskDecompositionRow, record.decompositionId).payload
            )
            planned = next(item for item in graph.subtasks if item.id == subtask_id)
            policy = parent.specification.autonomous_execution.coordinator_verification
            if policy is not None:
                require_node_verdict(
                    session, record, node, graph, planned, checkpoint_id, result_digest, policy
                )
            if (
                validated.subtaskId != subtask_id
                or validated.evidence != evidence
                or set(validated.completionCriteriaSatisfied) != set(planned.completionCriteria)
            ):
                raise DomainError(
                    "COORDINATION_CHECKPOINT_INVALID",
                    "Checkpoint result criteria or lineage differ.",
                    409,
                )
            node.status = "succeeded"
            node.resultSummary = summary
            node.resultDigest = result_digest
            node.evidence = evidence
            node.checkpointId = checkpoint_id
            self._inference_identity(session, node, node.runtimeRunId, checkpoint_id)
            node.failureCategory = None
            node.failureDetail = None
            record.updatedAt = datetime.now(UTC)
            row.payload = record.model_dump(mode="json")
            self._event(session, record, "subtask_succeeded", node)
            return record

    def record_failure(
        self,
        record_id: str,
        subtask_id: str,
        runtime_attempt_id: str,
        *,
        category: str,
        detail: str,
        retryable: bool,
        fence: RuntimeExecutionFence,
        validate_live: LiveValidator,
    ) -> CoordinationRecord:
        with self._write() as session:
            row, record = self._record(session, record_id)
            _, graph, _, _ = self._live_record(session, record, fence, validate_live)
            node = self._node(record, subtask_id, runtime_attempt_id)
            if node.status in {"retrying", "failed"}:
                return record
            if node.status not in {"claimed", "running"}:
                raise DomainError("COORDINATION_ATTEMPT_STALE", "Attempt cannot fail.", 409)
            retry = retryable and node.attemptCount < record.maximumAttemptsPerSubtask
            now = datetime.now(UTC)
            node.status = "retrying" if retry else "failed"
            node.failureCategory = category
            node.failureDetail = detail[:500]
            node.retryEligibleAt = (
                now + timedelta(seconds=min(2**node.attemptCount, 30)) if retry else None
            )
            record.failures.append(
                CoordinationFailure(
                    subtaskId=node.subtaskId,
                    runtimeAttemptId=runtime_attempt_id,
                    attemptNumber=max(node.attemptCount, 1),
                    category=category,
                    detail=detail[:500],
                    retryable=retry,
                    recordedAt=now,
                )
            )
            if not retry:
                record.status = "failed"
                failed_key = node.key
                blocked = {failed_key}
                changed = True
                while changed:
                    changed = False
                    for planned in graph.subtasks:
                        target = next(item for item in record.nodes if item.key == planned.key)
                        if target.status == "pending" and set(planned.dependsOn) & blocked:
                            target.status = "blocked"
                            target.failureCategory = "upstream_failed"
                            target.failureDetail = f"Required upstream work failed: {failed_key}"
                            blocked.add(planned.key)
                            changed = True
            record.updatedAt = now
            row.payload = record.model_dump(mode="json")
            self._event(
                session,
                record,
                "subtask_retrying" if retry else "subtask_failed",
                node,
            )
            return record

    def begin_synthesis(
        self,
        record_id: str,
        fence: RuntimeExecutionFence,
        validate_live: LiveValidator,
    ) -> tuple[CoordinationRecord, list[CoordinatedSubtask]] | None:
        with self._write() as session:
            row, record = self._record(session, record_id)
            _, graph, _, _ = self._live_record(session, record, fence, validate_live)
            if record.status in {"completing", "completed"}:
                return None
            if (
                record.synthesis.status == "running"
                or record.synthesis.attemptCount >= record.maximumSynthesisAttempts
            ):
                return None
            if record.synthesis.retryEligibleAt and record.synthesis.retryEligibleAt > datetime.now(
                UTC
            ):
                return None
            if record.synthesis.status == "succeeded":
                return record, self._ordered_successes(record, graph)
            if any(node.status != "succeeded" for node in record.nodes):
                return None
            ordered = self._ordered_successes(record, graph)
            record.status = "synthesizing"
            record.synthesis.status = "running"
            record.synthesis.attemptCount += 1
            record.synthesis.dispatchLeaseFingerprint = sha256(
                fence.lease_token.encode()
            ).hexdigest()
            record.synthesis.retryEligibleAt = None
            record.synthesis.runtimeRunId = coordination_id(
                record.decompositionId,
                "synthesis",
                f"run-{record.synthesis.attemptCount}",
            )
            record.synthesis.runtimeAttemptId = coordination_id(
                record.synthesis.runtimeRunId,
                f"attempt-{record.synthesis.attemptCount}",
            )
            record.synthesis.inputSubtaskIds = [node.subtaskId for node in ordered]
            record.synthesis.inputsDigest = self.inputs_digest(ordered)
            record.updatedAt = datetime.now(UTC)
            row.payload = record.model_dump(mode="json")
            self._event(session, record, "synthesis_started")
            return record, ordered

    def record_dispatch(self, record_id, fence, validate_live):
        with self._write() as session:
            row, record = self._record(session, record_id)
            self._live_record(session, record, fence, validate_live)
            if record.modelDispatchCount >= 38:
                raise DomainError(
                    "COORDINATION_MODEL_BUDGET_EXCEEDED",
                    "Coordinator dispatch budget exhausted.",
                    409,
                )
            record.modelDispatchCount += 1
            record.updatedAt = datetime.now(UTC)
            row.payload = record.model_dump(mode="json")
            self._event(session, record, "model_dispatch_intent")

    def record_synthesis(
        self,
        record_id: str,
        runtime_attempt_id: str,
        *,
        summary: str,
        result_digest: str,
        checkpoint_id: str,
        fence: RuntimeExecutionFence,
        validate_live: LiveValidator,
    ) -> CoordinationRecord:
        with self._write() as session:
            row, record = self._record(session, record_id)
            _, graph, _, _ = self._live_record(session, record, fence, validate_live)
            synthesis = record.synthesis
            if synthesis.status == "succeeded":
                return record
            if synthesis.status != "running" or synthesis.runtimeAttemptId != runtime_attempt_id:
                raise DomainError("COORDINATION_ATTEMPT_STALE", "Synthesis attempt is stale.", 409)
            ordered = self._ordered_successes(record, graph)
            if synthesis.inputsDigest != self.inputs_digest(ordered):
                raise DomainError("COORDINATION_SYNTHESIS_STALE", "Synthesis inputs changed.", 409)
            validated = self._require_checkpoint(
                session,
                synthesis.runtimeRunId,
                runtime_attempt_id,
                checkpoint_id,
                result_digest,
                summary,
                SynthesisResult,
            )
            if validated.contributingSubtaskIds != synthesis.inputSubtaskIds:
                raise DomainError(
                    "COORDINATION_CHECKPOINT_INVALID",
                    "Checkpoint synthesis contributors differ.",
                    409,
                )
            synthesis.status = "succeeded"
            synthesis.summary = summary
            synthesis.resultDigest = result_digest
            synthesis.checkpointId = checkpoint_id
            self._inference_identity(session, synthesis, synthesis.runtimeRunId, checkpoint_id)
            record.status = "completing"
            record.finalResultReference = f"coordination:{record.id}:{result_digest}"
            record.updatedAt = datetime.now(UTC)
            row.payload = record.model_dump(mode="json")
            self._event(session, record, "synthesis_succeeded")
            return record

    def record_synthesis_failure(
        self,
        record_id: str,
        runtime_attempt_id: str,
        detail: str,
        fence: RuntimeExecutionFence,
        validate_live: LiveValidator,
        retryable: bool = True,
    ) -> CoordinationRecord:
        with self._write() as session:
            row, record = self._record(session, record_id)
            self._live_record(session, record, fence, validate_live)
            synthesis = record.synthesis
            if synthesis.runtimeAttemptId != runtime_attempt_id or synthesis.status != "running":
                return record
            synthesis.failureDetail = detail[:500]
            if retryable and synthesis.attemptCount < record.maximumSynthesisAttempts:
                synthesis.status = "pending"
                record.status = "active"
                synthesis.retryEligibleAt = datetime.now(UTC) + timedelta(
                    seconds=2**synthesis.attemptCount
                )
            else:
                synthesis.status = "failed"
                record.status = "failed"
            record.updatedAt = datetime.now(UTC)
            row.payload = record.model_dump(mode="json")
            self._event(
                session,
                record,
                "synthesis_retrying" if record.status == "active" else "synthesis_failed",
            )
            return record

    def mark_completed(self, record_id: str, result_reference: str) -> CoordinationRecord:
        """Reconcile the post-completion acknowledgement from durable task truth."""
        with self._write() as session:
            row, record = self._record(session, record_id)
            if record.status == "completed":
                return record
            task = session.get(TaskRow, record.taskId)
            if (
                task is None
                or task.status != "completed"
                or task.result != result_reference
                or record.status != "completing"
                or record.synthesis.status != "succeeded"
            ):
                raise DomainError(
                    "COORDINATION_COMPLETION_STALE", "Completion is not durable.", 409
                )
            parent = session.get(AgentRuntimeRunRow, record.runtimeRunId)
            if (
                parent is None
                or parent.state != "succeeded"
                or session.get(TaskLeaseRow, record.taskId)
            ):
                raise DomainError(
                    "COORDINATION_COMPLETION_STALE", "Runtime or lease is not terminal.", 409
                )
            now = datetime.now(UTC)
            record.status = "completed"
            record.completedAt = now
            record.updatedAt = now
            row.payload = record.model_dump(mode="json")
            self._event(session, record, "completed")
            return record

    def completion_guard(
        self, record_id: str, result_reference: str, fence=None, validate_live=None
    ):
        def guard(session: Session) -> None:
            row = session.get(CoordinationRow, record_id)
            if row is None:
                raise DomainError("COORDINATION_NOT_FOUND", "Coordination not found.", 404)
            record = CoordinationRecord.model_validate(row.payload)
            if validate_live is not None:
                self._live_record(session, record, fence, validate_live, completing=True)
            if (
                record.status != "completing"
                or record.synthesis.status != "succeeded"
                or record.finalResultReference != result_reference
                or any(
                    node.status != "succeeded" or not node.checkpointId or not node.evidence
                    for node in record.nodes
                )
            ):
                raise DomainError(
                    "COORDINATION_INCOMPLETE", "Coordinator evidence is incomplete.", 409
                )
            graph = DecompositionRecord.model_validate(
                session.get(TaskDecompositionRow, record.decompositionId).payload
            )
            parent = AgentRunSnapshot.model_validate_json(
                session.get(AgentRuntimeRunRow, record.runtimeRunId).snapshot_json
            )
            policy = parent.specification.autonomous_execution.coordinator_verification
            for node in record.nodes:
                result = self._require_checkpoint(
                    session,
                    node.runtimeRunId,
                    node.runtimeAttemptId,
                    node.checkpointId,
                    node.resultDigest,
                    node.resultSummary,
                    SpecialistResult,
                )
                planned = next(item for item in graph.subtasks if item.id == node.subtaskId)
                if policy is not None:
                    require_node_verdict(
                        session,
                        record,
                        node,
                        graph,
                        planned,
                        node.checkpointId,
                        node.resultDigest,
                        policy,
                    )
                if (
                    result.subtaskId != node.subtaskId
                    or result.evidence != node.evidence
                    or set(result.completionCriteriaSatisfied) != set(planned.completionCriteria)
                ):
                    raise DomainError(
                        "COORDINATION_CHECKPOINT_INVALID", "Contributor checkpoint differs.", 409
                    )
            ordered = self._ordered_successes(record, graph)
            synthesis = record.synthesis
            result = self._require_checkpoint(
                session,
                synthesis.runtimeRunId,
                synthesis.runtimeAttemptId,
                synthesis.checkpointId,
                synthesis.resultDigest,
                synthesis.summary,
                SynthesisResult,
            )
            if result.contributingSubtaskIds != [
                node.subtaskId for node in ordered
            ] or synthesis.inputsDigest != self.inputs_digest(ordered):
                raise DomainError("COORDINATION_SYNTHESIS_STALE", "Final contributors differ.", 409)

        return guard

    @staticmethod
    def inputs_digest(nodes: list[CoordinatedSubtask]) -> str:
        material = "\0".join(
            f"{node.subtaskId}\0{node.resultDigest}\0{node.checkpointId}" for node in nodes
        )
        return "sha256:" + sha256(material.encode()).hexdigest()

    @staticmethod
    def _inference_identity(session, target, run_id, checkpoint_id):
        from app.db.models import AgentRuntimeCheckpointRow

        checkpoint = session.scalar(
            select(AgentRuntimeCheckpointRow).where(
                AgentRuntimeCheckpointRow.run_id == run_id,
                AgentRuntimeCheckpointRow.checkpoint_id == checkpoint_id,
            )
        )
        if checkpoint:
            import json

            payload = json.loads(checkpoint.contract_json)
            target.provider = payload["metadata"].get("provider")
            target.model = payload["metadata"].get("model")

    @staticmethod
    def _require_checkpoint(
        session, run_id, attempt_id, checkpoint_id, digest, summary, result_type
    ):
        import json

        from app.db.models import AgentRuntimeCheckpointRow

        row = session.get(AgentRuntimeCheckpointRow, (checkpoint_id, run_id))
        run = session.get(AgentRuntimeRunRow, run_id)
        if row is None or row.attempt_id != attempt_id or run is None or run.state != "succeeded":
            raise DomainError(
                "COORDINATION_CHECKPOINT_INVALID",
                "A completed runtime with validated checkpoint is required.",
                409,
            )
        try:
            checkpoint = json.loads(row.contract_json)
            result = result_type.model_validate_json(
                "".join(checkpoint["metadata"]["resultChunks"]),
                context={"persisted_checkpoint": True},
            )
            material = json.dumps(
                result.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
            expected = "sha256:" + sha256(material.encode()).hexdigest()
            if (
                expected != digest
                or checkpoint["integrity_digest"] != digest
                or checkpoint["metadata"]["resultDigest"] != digest
                or result.summary != summary
            ):
                raise ValueError("checkpoint result differs")
        except (KeyError, TypeError, ValueError) as exc:
            raise DomainError(
                "COORDINATION_CHECKPOINT_INVALID", "Checkpoint integrity validation failed.", 409
            ) from exc
        return result

    @staticmethod
    def _ordered_successes(record: CoordinationRecord, graph: DecompositionRecord):
        by_key = {node.key: node for node in record.nodes}
        return [by_key[key] for key in topological_keys(graph.subtasks)]

    @staticmethod
    def _record(session: Session, record_id: str) -> tuple[CoordinationRow, CoordinationRecord]:
        row = session.get(CoordinationRow, record_id)
        if row is None:
            raise DomainError("COORDINATION_NOT_FOUND", "Coordination not found.", 404)
        return row, CoordinationRecord.model_validate(row.payload)

    @classmethod
    def _node(cls, record, subtask_id, runtime_attempt_id):
        node = next((item for item in record.nodes if item.subtaskId == subtask_id), None)
        if node is None or node.runtimeAttemptId != runtime_attempt_id:
            raise DomainError("COORDINATION_ATTEMPT_STALE", "Attempt reference is stale.", 409)
        return node

    @classmethod
    def _live_record(cls, session, record, fence, validate_live, *, completing=False):
        result = cls._live(
            session,
            record.taskId,
            record.decompositionId,
            record.runtimeRunId,
            fence,
            validate_live,
            completing=completing,
        )
        if record.decompositionHash != fingerprint(result[1].model_dump(mode="json")):
            raise DomainError(
                "COORDINATION_PLAN_CORRECTED", "Operator plan changes require reconciliation.", 409
            )
        return result

    def execution_guard(self, record_id, fence, validate_live):
        def guard(session):
            _, record = self._record(session, record_id)
            self._live_record(
                session, record, fence, validate_live, completing=record.status == "completing"
            )

        return guard

    def block(self, record_id, fence, reason):
        """Record refusal under the task fence without granting revoked execution authority."""
        with self._write() as session:
            row, record = self._record(session, record_id)
            if record.status in {"completed", "blocked", "failed"}:
                return record
            task = session.get(TaskRow, record.taskId)
            if task.status == "cancelled" and session.get(TaskLeaseRow, record.taskId) is None:
                reason = "task_cancelled"
            else:
                if fence is None:
                    raise DomainError("TASK_LEASE_LOST", "A current task lease is required.", 409)
                TaskLeaseRepository._require_lease(
                    session, record.taskId, fence.worker_id, fence.lease_token, datetime.now(UTC)
                )
            record.status = "blocked"
            record.blockedReason = reason[:120]
            for node in record.nodes:
                if node.status in {"claimed", "running"}:
                    node.status = "blocked"
                    node.failureCategory = "control_plane"
                    node.failureDetail = reason[:120]
            if record.synthesis.status == "running":
                record.synthesis.status = "failed"
                record.synthesis.failureDetail = reason[:120]
            record.updatedAt = datetime.now(UTC)
            row.payload = record.model_dump(mode="json")
            self._event(session, record, "blocked")
            return record

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
