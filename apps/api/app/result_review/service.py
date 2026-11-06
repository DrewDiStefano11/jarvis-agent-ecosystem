"""Review saved planning output using the existing durable control plane."""

from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import select

from app.agent_runtime.transitions import TERMINAL_STATES
from app.autonomous_worker.repository import ModelExecutionRepository
from app.context.assembler import deterministic_hash
from app.core.errors import DomainError
from app.db.models import (
    AgentRuntimeRunRow,
    ArtifactRow,
    ContextAssemblyRow,
    IdentityAgentRow,
    ModelExecutionRow,
    ResultArtifactContentRow,
    ResultReviewRow,
    SystemStateRow,
    TaskLeaseRow,
    TaskRow,
)
from app.models.agent_runtime import AgentRunState, canonical_json
from app.models.autonomous_worker import PlanningReviewResult
from app.models.domain import Artifact, CreateTaskRequest, Task
from app.models.result_review import ResultReviewItem, ResultReviewReceipt, ReviewedArtifact
from app.repositories.sqlalchemy import SqlAlchemyRepository
from app.services.task_creation import prepare_task_creation


def conflict(message):
    raise DomainError("RESULT_REVIEW_CONFLICT", message, 409)


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def candidate_rows(session, query):
    """Page storage candidates; permission filtering must precede the UI limit."""
    offset = 0
    while True:
        rows = session.execute(query.offset(offset).limit(100)).all()
        yield from rows
        if len(rows) < 100:
            return
        offset += len(rows)


class ResultReviewService:
    def __init__(self, sessions, identity, runtime, task_leases):
        self.sessions = sessions
        self.identity = identity
        self.runtime = runtime
        self.task_leases = task_leases

    def authorize(self, actor_id, task_id, *, review=False, session=None):
        keys = ("results.read", "results.review") if review else ("results.read",)
        for key in keys:
            if not self.identity.check_permission_resource_access(
                actor_id, key, "task", task_id, session=session
            ).allowed:
                raise DomainError(
                    "RESULT_REVIEW_PERMISSION_DENIED",
                    "Result access is not authorized for this task.",
                    403,
                )

    def _position(self, session, row, command=None):
        from app.goal_planning.guards import requires_goal_review

        execution = ModelExecutionRepository._contract(row)
        task_row = session.scalar(
            select(TaskRow).where(TaskRow.id == row.task_id).with_for_update()
        )
        target = session.scalar(
            select(IdentityAgentRow)
            .where(IdentityAgentRow.id == row.target_agent_id)
            .with_for_update()
        )
        if (
            execution.stage != "human_review_required"
            or not isinstance(execution.result, PlanningReviewResult)
            or not (execution.requiresHumanReview or requires_goal_review(session, row.task_id))
            or target is None
            or not target.is_enabled
            or target.lifecycle_state != "active"
            or task_row is None
            or task_row.status != "under_review"
        ):
            conflict("Only an intact paused planning result can be reviewed.")
        aggregate = self.runtime._load_current(row.runtime_run_id)
        snapshot = aggregate.snapshot
        if (
            snapshot.state != AgentRunState.PAUSED
            or snapshot.pause_reason is None
            or snapshot.pause_reason.code != "model_result_review_required"
            or snapshot.pause_reason.resume_state != AgentRunState.RUNNING
            or snapshot.specification.task_id != row.task_id
            or snapshot.specification.agent_id != row.target_agent_id
            or snapshot.active_attempt_id != row.runtime_attempt_id
        ):
            conflict("The native runtime no longer matches the saved result.")
        checkpoint = next(
            (
                entry
                for entry in aggregate.checkpoints
                if entry.checkpoint_id == snapshot.latest_checkpoint_id
            ),
            None,
        )
        name = None if checkpoint is None else checkpoint.metadata.get("stage")
        if not isinstance(name, str) or not (
            name == "result-persisted" or name.startswith("result-persisted-")
        ):
            conflict("The latest checkpoint does not identify a persisted result.")
        digest = (
            "sha256:"
            + sha256(
                canonical_json(
                    {
                        "executionId": execution.executionId,
                        "name": name,
                        "requestHash": execution.requestHash,
                        "resultHash": execution.resultHash,
                    }
                ).encode()
            ).hexdigest()
        )
        if (
            checkpoint.attempt_id != execution.runtimeAttemptId
            or checkpoint.metadata.get("executionId") != execution.executionId
            or checkpoint.state_reference != f"model-execution:{execution.executionId}:{name}"
            or checkpoint.integrity_digest != digest
        ):
            conflict("Saved-result checkpoint integrity failed.")
        task = Task.model_validate(task_row.payload)
        assembly = session.get(ContextAssemblyRow, execution.contextAssemblyId)
        fingerprint = deterministic_hash(
            {
                "id": task.id,
                "projectId": task.projectId,
                "request": task.request,
                "title": task.title,
                "description": task.description,
            }
        )
        if (
            assembly is None
            or assembly.task_id != task.id
            or assembly.payload.get("taskInputFingerprint") != fingerprint
        ):
            conflict("The task scope differs from the saved model context.")
        if command is not None and (
            command.run_id != row.runtime_run_id
            or command.expected_result_hash != row.result_hash
            or command.expected_checkpoint_id != snapshot.latest_checkpoint_id
            or command.expected_run_version != snapshot.version
            or utc(command.expected_task_updated_at) != utc(task_row.updated_at)
        ):
            conflict("Review position changed. Refresh the saved result.")
        return execution, task_row, task, snapshot

    def pending(self, actor_id):
        with self.sessions() as session:
            rows = candidate_rows(
                session,
                select(ModelExecutionRow)
                .where(ModelExecutionRow.stage == "human_review_required")
                .order_by(
                    ModelExecutionRow.updated_at.desc(), ModelExecutionRow.execution_id.desc()
                ),
            )
            items = []
            for (row,) in rows:
                if not self.identity.check_permission_resource_access(
                    actor_id, "results.read", "task", row.task_id, session=session
                ).allowed:
                    continue
                if row.result_json is None or "steps" in row.result_json:
                    continue
                try:
                    execution, task_row, task, snapshot = self._position(session, row)
                except DomainError as error:
                    if error.code != "RESULT_REVIEW_CONFLICT":
                        raise
                    continue
                items.append(
                    ResultReviewItem(
                        execution=execution,
                        taskTitle=task.title,
                        taskUpdatedAt=utc(task_row.updated_at),
                        runVersion=snapshot.version,
                        checkpointId=snapshot.latest_checkpoint_id,
                    )
                )
                if len(items) == 100:
                    break
            return items

    def _fence(self, session, row):
        state = session.get(SystemStateRow, 1)
        if state is None or state.emergency_stop:
            raise DomainError(
                "EMERGENCY_STOP_ACTIVE", "Emergency stop blocks result finalization.", 423
            )
        if session.scalar(select(TaskLeaseRow).where(TaskLeaseRow.task_id == row.task_id)):
            conflict("The task still has a worker lease.")
        runs = session.scalars(select(AgentRuntimeRunRow)).all()
        descendants = {row.runtime_run_id}
        # A bounded native lineage closure prevents accepting a parent while its
        # descendants remain live. Corrupt cycles fail closed through the ledger.
        for _ in range(len(runs)):
            found = {entry.run_id for entry in runs if entry.parent_run_id in descendants}
            if found <= descendants:
                break
            descendants |= found
        terminal = {state.value for state in TERMINAL_STATES}
        for entry in runs:
            if (
                entry.run_id != row.runtime_run_id
                and entry.state not in terminal
                and (entry.task_id == row.task_id or entry.run_id in descendants)
            ):
                conflict("Related runtime work must finish before result finalization.")
        tasks = session.scalars(select(TaskRow)).all()
        child_ids = {row.task_id}
        for _ in range(len(tasks)):
            found = {entry.id for entry in tasks if entry.parent_task_id in child_ids}
            if found <= child_ids:
                break
            child_ids |= found
        if any(
            entry.id != row.task_id
            and entry.id in child_ids
            and entry.status not in {"completed", "failed", "cancelled"}
            for entry in tasks
        ):
            conflict("Child task work must finish before result finalization.")

    def decide(self, actor_id, command):
        if actor_id != command.actor_reference:
            raise DomainError(
                "RESULT_REVIEW_PERMISSION_DENIED", "Review actor does not match.", 403
            )
        with self.sessions() as session:
            row = session.get(ModelExecutionRow, command.execution_id)
            if row is None:
                raise DomainError("MODEL_EXECUTION_NOT_FOUND", "Saved result was not found.", 404)
            self.authorize(actor_id, row.task_id, review=True, session=session)
            if row.runtime_run_id != command.run_id:
                conflict("Review run does not match the execution.")
        # Native replay checks the complete original command hash and actor.
        existing = self.runtime._assert_idempotency(command.run_id, command.command_id, command)
        if existing is not None:
            return self.receipt(actor_id, command.execution_id)

        def effects(session):
            row = session.get(ModelExecutionRow, command.execution_id)
            self.authorize(actor_id, row.task_id, review=True, session=session)
            execution, task_row, task, _ = self._position(session, row, command)
            self._fence(session, row)
            now = datetime.now(UTC)
            review_id = f"review-{uuid4().hex}"
            artifact_id = f"artifact-{uuid4().hex}" if command.decision == "accept" else None
            correction = None
            if command.decision == "request_changes":
                correction = prepare_task_creation(
                    CreateTaskRequest(
                        title=("Revise: " + task.title)[:160],
                        description=command.revised_request,
                        correctionOfTaskId=task.id,
                    ),
                    task,
                )
                correction_row = SqlAlchemyRepository._task_row(correction)
                session.add(correction_row)
                # Native audit rows reference the correction task; SQLAlchemy
                # mappings have no ORM relationship to order these inserts.
                session.flush([correction_row])
            receipt = ResultReviewReceipt(
                id=review_id,
                executionId=row.execution_id,
                taskId=task.id,
                actorId=actor_id,
                commandId=command.command_id,
                decision=command.decision,
                resultHash=row.result_hash,
                artifactId=artifact_id,
                correctionTaskId=None if correction is None else correction.id,
                reviewedAt=now,
            )
            review = ResultReviewRow(
                id=review_id,
                execution_id=row.execution_id,
                actor_id=actor_id,
                command_id=command.command_id,
                result_hash=row.result_hash,
                decision=command.decision,
                reason=command.reason,
                payload=receipt.model_dump(mode="json"),
                created_at=now,
            )
            session.add(review)
            if artifact_id:
                artifact = Artifact(
                    id=artifact_id,
                    taskId=task.id,
                    name=task.title[:200],
                    type="planning_review_json_v1",
                    summary=execution.result.summary,
                    simulatedPath="",
                    createdAt=now,
                )
                session.add(
                    ArtifactRow(
                        id=artifact.id,
                        task_id=task.id,
                        producing_agent_id=None,
                        name=artifact.name,
                        artifact_type=artifact.type,
                        description=artifact.summary,
                        content_reference=f"reviewed-result:{execution.executionId}",
                        metadata_json={"resultHash": row.result_hash, "reviewId": review_id},
                        version="1.0.0",
                        schema_version="1.0",
                        payload=artifact.model_dump(mode="json"),
                        created_at=now,
                        updated_at=now,
                    )
                )
                session.flush([review])
                session.flush()
                session.add(
                    ResultArtifactContentRow(
                        artifact_id=artifact_id,
                        review_id=review_id,
                        content=canonical_json(row.result_json),
                        content_hash=row.result_hash,
                    )
                )
                task.artifactIds.append(artifact_id)
            task.status = {
                "accept": "completed",
                "request_changes": "revision_requested",
                "reject": "cancelled",
            }[command.decision]
            task.statusMessage = {
                "accept": "Saved result accepted",
                "request_changes": "Changes requested; a new draft is ready",
                "reject": "Saved result rejected",
            }[command.decision]
            task.updatedAt = now
            task.completedAt = now if command.decision != "request_changes" else None
            task.progress = 100 if command.decision == "accept" else task.progress
            task.error = None
            task_row.status, task_row.status_message = task.status, task.statusMessage
            task_row.progress, task_row.completed_at = task.progress, task.completedAt
            task_row.updated_at, task_row.error = now, None
            task_row.payload = task.model_dump(mode="json")
            row.stage = "completed" if command.decision == "accept" else "failed"
            row.failure_code = (
                None if command.decision == "accept" else "operator_" + command.decision
            )
            row.updated_at = now
            self.task_leases._add_event(
                session,
                "result.reviewed",
                "Operator finalized saved planning result",
                task_id=task.id,
                previous="under_review",
                new=task.status,
                actor_identity_id=actor_id,
                payload={
                    "reviewId": review_id,
                    "executionId": row.execution_id,
                    "resultHash": row.result_hash,
                    "decision": command.decision,
                    "artifactId": artifact_id,
                    "correctionTaskId": receipt.correctionTaskId,
                },
            )
            if correction is not None:
                self.task_leases._add_event(
                    session,
                    "task.created",
                    "Created operator-requested correction draft",
                    task_id=correction.id,
                    new="queued",
                    actor_identity_id=actor_id,
                    payload={"task": correction.model_dump(mode="json")},
                )

        # The native authorizer runs before replay/version checks under the same
        # write fence, including a concurrent identical-command replay.
        def authorize_commit(session, snapshot):
            self.authorize(actor_id, snapshot.specification.task_id, review=True, session=session)

        with self.runtime.repository.authorize_commits(authorize_commit):
            self.runtime.finalize_model_review(command, domain_effects=effects)
        return self.receipt(actor_id, command.execution_id)

    def receipt(self, actor_id, execution_id):
        with self.sessions() as session:
            row = session.scalar(
                select(ResultReviewRow).where(ResultReviewRow.execution_id == execution_id)
            )
            if row is None:
                conflict("The review receipt is missing.")
            receipt = ResultReviewReceipt.model_validate(row.payload)
            self.authorize(actor_id, receipt.taskId, session=session)
            return receipt

    def artifact_content(self, actor_id, artifact_id):
        with self.sessions() as session:
            artifact = session.get(ArtifactRow, artifact_id)
            if artifact is None:
                raise DomainError("ARTIFACT_NOT_FOUND", "Result artifact was not found.", 404)
            self.authorize(actor_id, artifact.task_id, session=session)
            content = session.get(ResultArtifactContentRow, artifact_id)
            if (
                content is None
                or sha256(content.content.encode()).hexdigest() != content.content_hash
            ):
                conflict("Result artifact integrity failed.")
            review = session.get(ResultReviewRow, content.review_id)
            if (
                review is None
                or review.result_hash != content.content_hash
                or artifact.metadata_json.get("resultHash") != content.content_hash
            ):
                conflict("Result artifact provenance failed.")
            return content.content

    def artifacts(self, actor_id):
        with self.sessions() as session:
            rows = candidate_rows(
                session,
                select(ArtifactRow, ResultReviewRow)
                .join(
                    ResultArtifactContentRow, ResultArtifactContentRow.artifact_id == ArtifactRow.id
                )
                .join(ResultReviewRow, ResultReviewRow.id == ResultArtifactContentRow.review_id)
                .order_by(ArtifactRow.created_at.desc(), ArtifactRow.id.desc()),
            )
            items = []
            for artifact, review in rows:
                if not self.identity.check_permission_resource_access(
                    actor_id, "results.read", "task", artifact.task_id, session=session
                ).allowed:
                    continue
                items.append(
                    ReviewedArtifact(
                        id=artifact.id,
                        taskId=artifact.task_id,
                        name=artifact.name,
                        summary=artifact.description,
                        createdAt=utc(artifact.created_at),
                        resultHash=review.result_hash,
                        review=ResultReviewReceipt.model_validate(review.payload),
                    )
                )
                if len(items) == 100:
                    break
            return items

    def task_executions(self, actor_id, task_id):
        with self.sessions() as session:
            self.authorize(actor_id, task_id, session=session)
            rows = session.scalars(
                select(ModelExecutionRow)
                .where(ModelExecutionRow.task_id == task_id)
                .order_by(ModelExecutionRow.created_at.desc())
                .limit(20)
            )
            return [ModelExecutionRepository._contract(row) for row in rows]
