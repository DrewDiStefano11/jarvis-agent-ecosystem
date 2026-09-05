from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256

from sqlalchemy import or_, select

from app.autonomous_worker.repository import canonical_json
from app.core.errors import DomainError
from app.db.models import (
    AgentRuntimeRunRow,
    ModelExecutionRow,
    TaskLeaseRow,
    TaskRow,
    ToolExecutionRow,
)
from app.models.agent_runtime import AgentRunSnapshot, AgentRunState
from app.models.autonomous_worker import WorkspacePlanResult
from app.models.domain import Task
from app.models.tool_execution import (
    ToolArtifact,
    ToolArtifactContent,
    ToolExecutionResult,
    ToolScope,
    ToolStepRecord,
)


def digest(value) -> str:
    return sha256(canonical_json(value).encode()).hexdigest()


def fail(code: str, message: str, status: int = 409):
    raise DomainError(code, message, status)


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class ToolExecutionRepository:
    """Bounded tool journal, using the existing task transaction and outbox boundary."""

    def __init__(self, leases):
        self.leases = leases
        self.sessions = leases.session_factory

    def _row(self, session, execution_id):
        row = session.get(ToolExecutionRow, execution_id)
        if row is None:
            fail("TOOL_EXECUTION_NOT_FOUND", "Tool execution was not found.", 404)
        self._integrity(row)
        return row

    def _integrity(self, row):
        try:
            plan = WorkspacePlanResult.model_validate(row.plan_json)
            scope = ToolScope.model_validate(row.scope_json)
            if digest(plan.model_dump(mode="json")) != row.plan_hash:
                raise ValueError("plan hash")
            request = {
                "actorId": row.actor_id,
                "request": {
                    "commandId": row.command_id,
                    "sourceExecutionId": row.source_execution_id,
                    "expectedPlanHash": row.plan_hash,
                    "scope": scope.model_dump(mode="json"),
                },
            }
            key = row.request_hash[:32]
            if (row.execution_id, row.task_id, row.runtime_run_id) != (
                f"tool-{key}",
                f"task-tool-{key}",
                f"run-tool-{key}",
            ):
                raise ValueError("execution identity")
            if digest(request) != row.request_hash or len(row.steps_json) != len(plan.steps):
                raise ValueError("authorization hash")
            for index, (record, step) in enumerate(zip(row.steps_json, plan.steps, strict=True)):
                public = ToolStepRecord.model_validate(
                    {k: v for k, v in record.items() if k not in {"stepHash", "observationHash"}}
                )
                if (
                    public.stepIndex != index
                    or public.tool != step.tool
                    or public.path != step.path
                    or record["stepHash"] != digest(step.model_dump(mode="json"))
                ):
                    raise ValueError("step hash")
                if public.status == "completed" and (
                    public.observation is None
                    or record.get("observationHash")
                    != digest(public.observation.model_dump(mode="json"))
                ):
                    raise ValueError("observation hash")
            for item in row.artifacts_json:
                artifact = ToolArtifactContent.model_validate(item)
                if (
                    artifact.executionId != row.execution_id
                    or artifact.taskId != row.task_id
                    or sha256(artifact.content.encode()).hexdigest() != artifact.contentHash
                    or len(artifact.content.encode()) != artifact.byteCount
                ):
                    raise ValueError("artifact hash")
        except (ValueError, KeyError, TypeError):
            fail(
                "TOOL_RECORD_INVALID",
                "The durable tool authorization or result failed integrity validation.",
            )

    def _contract(self, row):
        self._integrity(row)
        return ToolExecutionResult(
            executionId=row.execution_id,
            sourceExecutionId=row.source_execution_id,
            sourceTaskId=row.source_task_id,
            taskId=row.task_id,
            runtimeRunId=row.runtime_run_id,
            targetAgentId=row.target_agent_id,
            workspaceId=row.scope_json["workspaceId"],
            planHash=row.plan_hash,
            scope=ToolScope.model_validate(row.scope_json),
            stage=row.stage,
            steps=[
                ToolStepRecord.model_validate(
                    {k: v for k, v in item.items() if k not in {"stepHash", "observationHash"}}
                )
                for item in row.steps_json
            ],
            artifacts=[
                ToolArtifact.model_validate({k: v for k, v in item.items() if k != "content"})
                for item in row.artifacts_json
            ],
            failureCode=row.failure_code,
            createdAt=utc(row.created_at),
            updatedAt=utc(row.updated_at),
            completedAt=None if row.completed_at is None else utc(row.completed_at),
        )

    def get(self, execution_id):
        with self.sessions() as session:
            return self._contract(self._row(session, execution_id))

    def list_task(self, task_id):
        with self.sessions() as session:
            return [
                self._contract(row)
                for row in session.scalars(
                    select(ToolExecutionRow)
                    .where(
                        or_(
                            ToolExecutionRow.source_task_id == task_id,
                            ToolExecutionRow.task_id == task_id,
                        )
                    )
                    .order_by(ToolExecutionRow.created_at)
                )
            ]

    def find_command(self, actor_id, command_id, request_hash):
        with self.sessions() as session:
            row = session.scalar(
                select(ToolExecutionRow).where(
                    ToolExecutionRow.actor_id == actor_id, ToolExecutionRow.command_id == command_id
                )
            )
            if row is None:
                return None
            if row.request_hash != request_hash:
                fail(
                    "IDEMPOTENCY_KEY_CONFLICT",
                    "This tool authorization ID belongs to a different request.",
                )
            return self._contract(row)

    def emit(self, session, row, event_type, **payload):
        self.leases._add_event(
            session,
            event_type,
            "Bounded workspace tool execution updated",
            task_id=row.task_id,
            worker_id=row.actor_id,
            payload={
                "executionId": row.execution_id,
                "sourceExecutionId": row.source_execution_id,
                "sourceTaskId": row.source_task_id,
                "planHash": row.plan_hash,
                **payload,
            },
        )

    def create_intent(self, request, actor, source, request_hash):
        with self.leases._write() as session:
            existing = session.scalar(
                select(ToolExecutionRow).where(
                    ToolExecutionRow.actor_id == actor.actor_id,
                    ToolExecutionRow.command_id == request.commandId,
                )
            )
            if existing is not None:
                if existing.request_hash != request_hash:
                    fail(
                        "IDEMPOTENCY_KEY_CONFLICT",
                        "This tool authorization ID belongs to a different request.",
                    )
                return self._contract(existing)
            self.leases._require_execution_enabled(session)
            original = session.get(ModelExecutionRow, source.executionId)
            if (
                original is None
                or original.stage != "completed"
                or original.result_hash != request.expectedPlanHash
                or digest(original.result_json) != request.expectedPlanHash
            ):
                fail("TOOL_PLAN_NOT_READY", "A completed, unchanged workspace plan is required.")
            task_row = session.get(TaskRow, source.taskId)
            if task_row is None:
                fail("TASK_NOT_FOUND", "The source task was not found.", 404)
            now = datetime.now(UTC)
            key = request_hash[:32]
            task = Task(
                id=f"task-tool-{key}",
                title=f"Execute reviewed plan: {task_row.title}"[:200],
                description="Execute the fixed workspace steps explicitly authorized by the operator.",
                request="Execute the fixed reviewed workspace plan.",
                parentTaskId=source.taskId,
                projectId=task_row.project_id,
                createdBy=actor.actor_id,
                assignedManagerId=task_row.assigned_manager_id,
                createdAt=now,
                updatedAt=now,
            )
            session.add(self.leases.repository._task_row(task))
            session.flush()
            plan = source.result
            row = ToolExecutionRow(
                execution_id=f"tool-{key}",
                source_execution_id=source.executionId,
                source_task_id=source.taskId,
                task_id=task.id,
                runtime_run_id=f"run-tool-{key}",
                actor_id=actor.actor_id,
                target_agent_id=source.targetAgentId,
                command_id=request.commandId,
                request_hash=request_hash,
                plan_hash=request.expectedPlanHash,
                plan_json=plan.model_dump(mode="json"),
                scope_json=request.scope.model_dump(mode="json"),
                steps_json=[
                    {
                        **ToolStepRecord(
                            stepIndex=i, tool=step.tool, path=step.path, status="pending"
                        ).model_dump(mode="json"),
                        "stepHash": digest(step.model_dump(mode="json")),
                    }
                    for i, step in enumerate(plan.steps)
                ],
                artifacts_json=[],
                stage="preparing",
                failure_code=None,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            self.emit(
                session,
                row,
                "tool.execution.authorized",
                workspaceId=request.scope.workspaceId,
                stepCount=len(plan.steps),
            )
            result = self._contract(row)
        self.leases.repository.reload()
        return result

    def candidates(self, actor_id):
        # Keyset pages prevent an old inaccessible or leased head from hiding
        # eligible work beyond the first bounded database page.
        after = ""
        while True:
            with self.sessions() as session:
                rows = list(
                    session.scalars(
                        select(ToolExecutionRow.execution_id)
                        .where(
                            ToolExecutionRow.actor_id == actor_id,
                            ToolExecutionRow.stage.in_(["queued", "running"]),
                            ToolExecutionRow.execution_id > after,
                        )
                        .order_by(ToolExecutionRow.execution_id)
                        .limit(32)
                    )
                )
            if not rows:
                return
            yield from rows
            after = rows[-1]

    def mark(self, execution_id, stage, failure_code=None):
        with self.leases._write() as session:
            row = self._row(session, execution_id)
            row.stage = stage
            row.failure_code = failure_code
            row.updated_at = datetime.now(UTC)
            if stage in {"completed", "failed"}:
                row.completed_at = row.updated_at
            self.emit(session, row, f"tool.execution.{stage}", failureCode=failure_code)
            return self._contract(row)

    def current_lease(self, task_id, worker_id):
        with self.sessions() as session:
            row = session.get(TaskLeaseRow, task_id)
            if (
                row is not None
                and row.worker_id == worker_id
                and utc(row.expires_at) > datetime.now(UTC)
            ):
                return self.leases._lease(row)
            return None

    def plan(self, execution_id):
        with self.sessions() as session:
            return WorkspacePlanResult.model_validate(self._row(session, execution_id).plan_json)

    def step(
        self,
        execution_id,
        index,
        worker_id,
        lease_token,
        registry,
        policy_check,
        *,
        start_only=False,
    ):
        # SQLite BEGIN IMMEDIATE serializes policy/lease/emergency writes with the
        # bounded file operation. The durable started marker precedes any effect.
        with self.leases._write() as session:
            row = self._row(session, execution_id)
            self.leases._require_execution_enabled(session)
            self.leases._require_lease(
                session, row.task_id, worker_id, lease_token, datetime.now(UTC)
            )
            runtime_row = session.get(AgentRuntimeRunRow, row.runtime_run_id)
            snapshot = (
                None
                if runtime_row is None
                else AgentRunSnapshot.model_validate(json.loads(runtime_row.snapshot_json))
            )
            if (
                snapshot is None
                or snapshot.state != AgentRunState.RUNNING
                or row.stage not in {"queued", "running"}
            ):
                fail("TOOL_RUNTIME_NOT_RUNNING", "The authorized runtime is not running.")
            if (
                snapshot.specification.task_id != row.task_id
                or snapshot.specification.agent_id != row.target_agent_id
                or snapshot.specification.autonomous_execution is None
                or snapshot.specification.autonomous_execution.tool_execution_id != row.execution_id
            ):
                fail("TOOL_RECORD_INVALID", "Runtime binding differs from the tool authorization.")
            policy_check(snapshot)
            records = [dict(item) for item in row.steps_json]
            record = records[index]
            if record["status"] == "completed":
                return self._contract(row)
            if any(item["status"] != "completed" for item in records[:index]):
                fail("TOOL_RECORD_INVALID", "Earlier reviewed steps have not completed.")
            step = WorkspacePlanResult.model_validate(row.plan_json).steps[index]
            scope = ToolScope.model_validate(row.scope_json)
            registry.validate_step(step, scope)
            if start_only:
                record["status"] = "started"
            else:
                if record["status"] != "started":
                    fail("TOOL_RECORD_INVALID", "The tool step has no durable start marker.")
                observation = registry.execute(step, scope)
                record.update(
                    status="completed",
                    observation=observation.model_dump(mode="json"),
                    observationHash=digest(observation.model_dump(mode="json")),
                )
                if step.tool in {"workspace.write", "workspace.report"}:
                    artifact = ToolArtifactContent(
                        artifactId=f"artifact-{row.execution_id}-{index}",
                        executionId=row.execution_id,
                        taskId=row.task_id,
                        relativePath=step.path,
                        contentHash=sha256(step.content.encode()).hexdigest(),
                        byteCount=len(step.content.encode()),
                        content=step.content,
                        mediaType="text/markdown; charset=utf-8"
                        if step.path.endswith(".md")
                        else "text/plain; charset=utf-8",
                    )
                    row.artifacts_json = [*row.artifacts_json, artifact.model_dump(mode="json")]
                    record["artifactId"] = artifact.artifactId
            row.steps_json = records
            row.stage = "running"
            row.updated_at = datetime.now(UTC)
            self.emit(
                session,
                row,
                "tool.step.started" if start_only else "tool.step.completed",
                stepIndex=index,
                tool=step.tool,
                observationHash=record.get("observationHash"),
                artifactId=record.get("artifactId"),
            )
            return self._contract(row)

    def completion_guard(self, session, execution_id, policy_check):
        row = self._row(session, execution_id)
        if not all(item["status"] == "completed" for item in row.steps_json):
            fail("TOOL_RECORD_INVALID", "Incomplete tool steps cannot complete a task.")
        runtime = session.get(AgentRuntimeRunRow, row.runtime_run_id)
        snapshot = (
            None if runtime is None else AgentRunSnapshot.model_validate_json(runtime.snapshot_json)
        )
        if snapshot is None or snapshot.state != AgentRunState.RUNNING:
            fail("TOOL_RUNTIME_NOT_RUNNING", "The tool runtime is no longer running.")
        policy_check(snapshot)

    def artifact(self, artifact_id):
        # Artifact IDs carry the parent execution key; never query an arbitrary path.
        if not artifact_id.startswith("artifact-tool-") or len(artifact_id) != 48:
            fail("TOOL_ARTIFACT_NOT_FOUND", "The artifact was not found.", 404)
        execution_id = artifact_id.removeprefix("artifact-").rsplit("-", 1)[0]
        with self.sessions() as session:
            row = self._row(session, execution_id)
            item = next(
                (item for item in row.artifacts_json if item["artifactId"] == artifact_id), None
            )
            if item is None:
                fail("TOOL_ARTIFACT_NOT_FOUND", "The artifact was not found.", 404)
            return self._contract(row), ToolArtifactContent.model_validate(item)
