from __future__ import annotations

from app.agent_runtime.errors import (
    InvalidTransitionError,
    RuntimeActorInactiveError,
    RuntimePermissionDeniedError,
    VersionConflictError,
)
from app.agent_runtime.repository import RuntimeExecutionFence
from app.autonomous_worker.errors import AutonomousWorkerError
from app.autonomous_worker.provisioning import configure_task_actor
from app.core.errors import DomainError
from app.models.agent_runtime import (
    AbandonAttemptCommand,
    AgentRunSpecification,
    AgentRunState,
    AutonomousExecutionSpecification,
    BeginAttemptCommand,
    ClaimAgentRunCommand,
    CompleteAgentRunCommand,
    CompleteAttemptCommand,
    CreateAgentRunCommand,
    QueueAgentRunCommand,
    RecordCheckpointCommand,
    StartAttemptCommand,
)
from app.models.autonomous_worker import WorkspacePlanResult
from app.tool_execution.filesystem import WorkspaceToolRegistry
from app.tool_execution.repository import ToolExecutionRepository, digest, fail


class ToolExecutionService:
    """Adapter executing fixed approved steps through the existing worker/runtime."""

    def __init__(self, app):
        self.app = app
        self.settings = app.state.settings
        self.worker = app.state.autonomous_worker_service
        self.runtime = app.state.agent_runtime_service
        self.leases = app.state.task_leases
        self.repository = ToolExecutionRepository(self.leases)

    def registry(self):
        return WorkspaceToolRegistry(self.settings.tool_workspaces_json)

    def workspaces(self):
        if not self.settings.tool_execution_enabled:
            return []
        return self.registry().workspaces()

    def _enabled(self):
        if not self.settings.tool_execution_enabled:
            fail("TOOL_EXECUTION_DISABLED", "Workspace tool execution is disabled.", 503)
        self.worker.validate_enabled()

    def _read(self, result, actor):
        # New workspace observations retain the linked execution task's own
        # read boundary; source-only access must not silently gain file data.
        self.worker.read_result_authorized(result.sourceExecutionId, actor)
        if (
            result.stage != "preparing"
            or self.runtime.repository.load_run(result.runtimeRunId) is not None
        ):
            self.runtime.read_run_authorized(result.runtimeRunId, actor)
        return result

    def read(self, execution_id, actor):
        return self._read(self.repository.get(execution_id), actor)

    def list_task(self, task_id, actor):
        rows = self.repository.list_task(task_id)
        if not rows:
            # Preserve the existing task-read authorization boundary for empty lists.
            self.worker.list_task_results_authorized(task_id, actor)
        return [self._read(row, actor) for row in rows]

    def artifact(self, artifact_id, actor):
        result, artifact = self.repository.artifact(artifact_id)
        self._read(result, actor)
        return artifact

    def authorize(self, request, actor):
        request_hash = digest(
            {"actorId": actor.actor_id, "request": request.model_dump(mode="json")}
        )
        existing = self.repository.find_command(actor.actor_id, request.commandId, request_hash)
        if existing is not None:
            self._read(existing, actor)
            if existing.stage != "preparing":
                return existing
        self._enabled()
        if actor.actor_id != self.settings.autonomous_worker_actor_id:
            fail(
                "TOOL_WORKER_IDENTITY_MISMATCH",
                "Select the configured local worker identity before authorizing tools.",
                403,
            )
        source = self.worker.read_result_authorized(request.sourceExecutionId, actor)
        snapshot = self.runtime.read_run_authorized(source.runtimeRunId, actor)
        specification = snapshot.specification.autonomous_execution
        if (
            source.stage != "completed"
            or not isinstance(source.result, WorkspacePlanResult)
            or specification is None
            or specification.execution_type != "workspace_plan"
        ):
            fail(
                "TOOL_PLAN_NOT_READY",
                "A completed workspace plan is required before authorization.",
            )
        if source.resultHash != request.expectedPlanHash:
            fail("TOOL_PLAN_CHANGED", "The reviewed plan hash does not match the durable result.")
        if len(source.result.steps) > request.scope.maximumSteps:
            fail("TOOL_SCOPE_DENIED", "The plan exceeds the explicitly authorized step limit.", 403)
        # Read access alone must never mint execution permissions on a child task.
        for operation in ("create", "queue", "start_attempt"):
            self.runtime.authorizer.authorize(actor, operation, snapshot=snapshot)
        registry = self.registry()
        registry.validate_scope(request.scope)
        for step in source.result.steps:
            registry.validate_step(step, request.scope)
        intent = existing or self.repository.create_intent(request, actor, source, request_hash)
        # Explicit trusted operator action grants only the new task. Existing
        # definitions/denials are preserved by the shared provisioning service.
        configure_task_actor(self.app, intent.taskId, actor.stable_key)
        spec = AgentRunSpecification(
            run_id=intent.runtimeRunId,
            task_id=intent.taskId,
            agent_id=intent.targetAgentId,
            requested_operation="Execute explicitly authorized workspace steps",
            created_at=intent.createdAt,
            parent_run_id=source.runtimeRunId,
            correlation_id=intent.taskId,
            idempotency_key=intent.executionId,
            maximum_permitted_attempts=1,
            metadata={
                "toolExecutionId": intent.executionId,
                "sourceExecutionId": source.executionId,
                "planHash": intent.planHash,
            },
            autonomous_execution=AutonomousExecutionSpecification(
                execution_type="workspace_tools",
                context_assembly_id=source.contextAssemblyId,
                tool_execution_id=intent.executionId,
            ),
        )
        current = self.runtime.repository.load_run(intent.runtimeRunId)
        if current is None:
            created = self.runtime.handle_authorized(
                CreateAgentRunCommand(
                    specification=spec,
                    command_id=f"{intent.executionId}-create",
                    timestamp=intent.createdAt,
                ),
                actor,
                require_execution_enabled=True,
            )
            current = created.snapshot
        elif current.specification != spec:
            fail("TOOL_RECORD_INVALID", "The tool runtime does not match its authorization.")
        if current.state == AgentRunState.CREATED:
            self.worker._handle(
                QueueAgentRunCommand, current, actor, "tool-queue", require_execution_enabled=True
            )
        elif current.state != AgentRunState.QUEUED:
            fail("TOOL_RUNTIME_NOT_READY", "The authorized runtime is not available for queueing.")
        return self.repository.mark(intent.executionId, "queued")

    def _policy(self, snapshot, actor):
        self._enabled()
        self.runtime.authenticate_actor(actor.actor_id)
        if not self.worker.executions.target_identity_active(snapshot.specification.agent_id):
            fail("TOOL_TARGET_INACTIVE", "The assigned identity is no longer active.", 403)
        self.runtime.authorizer.authorize(actor, "start_attempt", snapshot=snapshot)

    async def run_once(self, worker_id, actor):
        try:
            return await self._run_once(worker_id, actor)
        except (RuntimePermissionDeniedError, RuntimeActorInactiveError):
            raise AutonomousWorkerError("EXECUTION_AUTHORIZATION_REVOKED") from None
        except (VersionConflictError, InvalidTransitionError):
            # A concurrent operator command won; re-read durable state next poll.
            return None
        except DomainError as error:
            if error.code == "EMERGENCY_STOP_ACTIVE":
                raise AutonomousWorkerError(
                    "EXECUTION_EMERGENCY_STOPPED", status_code=423
                ) from None
            if error.code in {"TASK_LEASE_LOST", "TASK_CLAIM_CONFLICT"}:
                raise AutonomousWorkerError("EXECUTION_LEASE_LOST") from None
            raise

    async def _run_once(self, worker_id, actor):
        if not self.settings.tool_execution_enabled:
            return None
        for execution_id in self.repository.candidates(actor.actor_id):
            result = self.repository.get(execution_id)
            try:
                snapshot = self.runtime.read_run_authorized(result.runtimeRunId, actor)
                self.runtime.authorizer.authorize(actor, "start_attempt", snapshot=snapshot)
            except RuntimePermissionDeniedError:
                continue
            task_state = self.leases.task_recovery_state(result.taskId)
            if snapshot.state in {
                AgentRunState.CANCEL_REQUESTED,
                AgentRunState.CANCELLING,
                AgentRunState.CANCELLED,
            } or (task_state is not None and task_state[0] == "cancelled"):
                if self.worker._best_effort_cancel(snapshot, actor):
                    return self.repository.mark(result.executionId, "failed", "EXECUTION_CANCELLED")
                continue
            if task_state == ("completed", f"tool-execution:{result.executionId}"):
                return self._finalize(result, snapshot, actor)
            if snapshot.state in {
                AgentRunState.FAILED,
                AgentRunState.TIMED_OUT,
                AgentRunState.ABANDONED,
            } or (task_state is not None and task_state[0] == "failed"):
                return self.repository.mark(result.executionId, "failed", "TOOL_RUNTIME_FAILED")
            if snapshot.state in {AgentRunState.PAUSED, AgentRunState.BLOCKED}:
                return self.repository.mark(result.executionId, "paused", "TOOL_RUNTIME_PAUSED")
            if snapshot.state not in {
                AgentRunState.QUEUED,
                AgentRunState.CLAIMED,
                AgentRunState.STARTING,
                AgentRunState.RUNNING,
            }:
                continue
            lease = self.repository.current_lease(result.taskId, worker_id)
            if lease is None:
                acquired = self.leases.acquire_task(
                    worker_id, self.settings.autonomous_worker_lease_seconds, result.taskId
                )
                if acquired is None:
                    continue
                _, lease = acquired
            return self._execute(result, snapshot, actor, worker_id, lease.leaseToken)
        return None

    def _execute(self, result, snapshot, actor, worker_id, token):
        fence = RuntimeExecutionFence(task_id=result.taskId, worker_id=worker_id, lease_token=token)
        attempt_id = f"attempt-{result.executionId}"
        try:
            self._policy(snapshot, actor)
            if snapshot.state == AgentRunState.QUEUED:
                snapshot = self.worker._handle(
                    ClaimAgentRunCommand,
                    snapshot,
                    actor,
                    "tool-claim",
                    execution_fence=fence,
                    executor_reference=worker_id,
                )
            if snapshot.state == AgentRunState.CLAIMED:
                snapshot = self.worker._handle(
                    BeginAttemptCommand,
                    snapshot,
                    actor,
                    "tool-begin",
                    execution_fence=fence,
                    attempt_id=attempt_id,
                    executor_reference=worker_id,
                )
            if snapshot.state == AgentRunState.STARTING:
                snapshot = self.worker._handle(
                    StartAttemptCommand,
                    snapshot,
                    actor,
                    "tool-start",
                    execution_fence=fence,
                    attempt_id=attempt_id,
                )
            for index in range(len(result.steps)):
                result = self.repository.get(result.executionId)
                if result.steps[index].status != "completed":
                    self.repository.step(
                        result.executionId,
                        index,
                        worker_id,
                        token,
                        self.registry(),
                        lambda current: self._policy(current, actor),
                        start_only=True,
                    )
                    result = self.repository.step(
                        result.executionId,
                        index,
                        worker_id,
                        token,
                        self.registry(),
                        lambda current: self._policy(current, actor),
                    )
                # A committed step and its runtime checkpoint are independently
                # recoverable. Existing checkpoints are verified, never repeated.
                checkpoint_id = f"checkpoint-{result.executionId}-{index}"
                step_digest = digest(result.steps[index].model_dump(mode="json"))
                checkpoints = self.runtime.checkpoints_authorized(result.runtimeRunId, actor)
                existing = next(
                    (item for item in checkpoints if item.checkpoint_id == checkpoint_id), None
                )
                if existing is not None:
                    if (
                        existing.integrity_digest != f"sha256:{step_digest}"
                        or existing.state_reference != f"tool-execution:{result.executionId}"
                    ):
                        fail(
                            "TOOL_RECORD_INVALID",
                            "The runtime checkpoint differs from the durable tool result.",
                        )
                else:
                    snapshot = self.runtime.read_run_authorized(result.runtimeRunId, actor)
                    self.worker._handle(
                        RecordCheckpointCommand,
                        snapshot,
                        actor,
                        f"tool-step-{index}",
                        execution_fence=fence,
                        checkpoint_id=checkpoint_id,
                        attempt_id=attempt_id,
                        state_reference=f"tool-execution:{result.executionId}",
                        integrity_digest=f"sha256:{step_digest}",
                        resume_cursor=str(index + 1),
                        checkpoint_metadata={
                            "executionId": result.executionId,
                            "planHash": result.planHash,
                            "completedSteps": index + 1,
                        },
                    )
            self.leases.complete_task(
                result.taskId,
                worker_id,
                token,
                f"tool-execution:{result.executionId}",
                completion_guard=lambda session: self.repository.completion_guard(
                    session, result.executionId, lambda current: self._policy(current, actor)
                ),
            )
            snapshot = self.runtime.read_run_authorized(result.runtimeRunId, actor)
            return self._finalize(result, snapshot, actor)
        except DomainError as error:
            if error.code.startswith("TOOL_"):
                current = self.runtime.read_run_authorized(result.runtimeRunId, actor)
                if current.state in {
                    AgentRunState.CANCEL_REQUESTED,
                    AgentRunState.CANCELLING,
                    AgentRunState.CANCELLED,
                }:
                    if self.worker._best_effort_cancel(current, actor):
                        return self.repository.mark(
                            result.executionId, "failed", "EXECUTION_CANCELLED"
                        )
                    raise AutonomousWorkerError("EXECUTION_CANCELLED") from None
                if current.state == AgentRunState.STARTING:
                    self.worker._handle(
                        AbandonAttemptCommand,
                        current,
                        actor,
                        "tool-prestart-failure",
                        attempt_id=attempt_id,
                        detail="Workspace authorization became unavailable before execution",
                    )
                else:
                    self.worker._confirm_review_pause(
                        current,
                        actor,
                        reason_code=error.code.lower(),
                        request_suffix="tool-failure-pause",
                        request_detail="Bounded tool execution requires operator review",
                        confirm_suffix="tool-failure-confirm",
                        confirm_detail="No further workspace steps will run",
                        requestable_states={
                            AgentRunState.QUEUED,
                            AgentRunState.CLAIMED,
                            AgentRunState.RUNNING,
                        },
                    )
                self.leases.pause_for_review(
                    result.taskId, worker_id, token, f"tool-execution:{result.executionId}"
                )
                return self.repository.mark(result.executionId, "paused", error.code)
            if error.code == "EMERGENCY_STOP_ACTIVE":
                raise AutonomousWorkerError(
                    "EXECUTION_EMERGENCY_STOPPED", status_code=423
                ) from None
            raise

    def _finalize(self, result, snapshot, actor):
        self.leases.assert_execution_enabled()
        if not all(step.status == "completed" for step in result.steps):
            fail("TOOL_RECORD_INVALID", "Incomplete tool steps cannot finalize.")
        if snapshot.state == AgentRunState.RUNNING:
            snapshot = self.worker._handle(
                CompleteAttemptCommand,
                snapshot,
                actor,
                "tool-complete-attempt",
                require_execution_enabled=True,
                attempt_id=f"attempt-{result.executionId}",
                detail="Reviewed tool steps and artifacts persisted",
            )
        if snapshot.state == AgentRunState.CLAIMED:
            snapshot = self.worker._handle(
                CompleteAgentRunCommand,
                snapshot,
                actor,
                "tool-complete-run",
                require_execution_enabled=True,
                detail="Authorized workspace execution completed",
            )
        if snapshot.state != AgentRunState.SUCCEEDED:
            fail("TOOL_RUNTIME_NOT_READY", "The tool runtime cannot finalize.")
        return self.repository.mark(result.executionId, "completed")
