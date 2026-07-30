from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from pydantic import ValidationError

from app.agent_runtime.authorization import RuntimeActorContext
from app.agent_runtime.errors import RuntimePermissionDeniedError
from app.agent_runtime.service import AgentRuntimeService
from app.autonomous_worker.errors import AutonomousWorkerError
from app.autonomous_worker.repository import ModelExecutionRepository, canonical_json
from app.core.config import Settings
from app.core.errors import DomainError
from app.model_providers.budget import TaskBudget
from app.model_providers.contracts import (
    MessageRole,
    ModelCapability,
    ModelExecutionRequest,
    ModelExecutionResponse,
    ModelMessage,
)
from app.model_providers.errors import (
    BudgetExceededError,
    ModelProviderError,
    ProviderExecutionDisabledError,
    RequestTimeoutError,
    UnknownProviderError,
)
from app.model_providers.router import ModelRouter, RoutingRequirements
from app.models.agent_runtime import (
    AbandonAttemptCommand,
    AgentRunSnapshot,
    AgentRunState,
    BeginAttemptCommand,
    ClaimAgentRunCommand,
    CompleteAgentRunCommand,
    CompleteAttemptCommand,
    ConfirmPauseCommand,
    FailAgentRunCommand,
    FailAttemptCommand,
    FailureClassification,
    RecordCheckpointCommand,
    RequestPauseCommand,
    RuntimeCommand,
    StartAttemptCommand,
)
from app.models.autonomous_worker import (
    ModelExecutionResult,
    PlanningReviewResult,
)
from app.models.context import ContextAssembly
from app.repositories.task_leases import TaskLeaseRepository

PRE_EXECUTION_REVIEW_CODES = frozenset(
    {
        "runtime_execution_not_eligible",
        "context_assembly_required",
        "context_assembly_unavailable",
        "context_assembly_review_required",
        "context_assembly_mismatch",
    }
)


class AutonomousWorkerService:
    """One-task local planning/review executor with durable staged recovery."""

    def __init__(
        self,
        *,
        settings: Settings,
        executions: ModelExecutionRepository,
        task_leases: TaskLeaseRepository,
        runtime: AgentRuntimeService,
        router: ModelRouter,
    ) -> None:
        self.settings = settings
        self.executions = executions
        self.task_leases = task_leases
        self.runtime = runtime
        self.router = router

    def validate_enabled(self) -> None:
        if not self.settings.autonomous_worker_enabled:
            raise AutonomousWorkerError("AUTONOMOUS_WORKER_DISABLED", status_code=503)
        if self.settings.model_execution_mode != "local_only":
            raise AutonomousWorkerError("MODEL_EXECUTION_DISABLED", status_code=503)
        providers = self.router.registry.list()
        if any(not provider.is_local for provider in providers):
            raise AutonomousWorkerError("LOCAL_PROVIDER_REQUIRED", status_code=503)
        if not providers:
            raise AutonomousWorkerError("NO_LOCAL_PROVIDER_AVAILABLE", status_code=503)

    async def run_once(self, worker_id: str) -> ModelExecutionResult | None:
        self.validate_enabled()
        actor = self.runtime.authenticate_actor(self.settings.autonomous_worker_actor_id)
        try:
            if self._recover_pre_execution_pause(worker_id, actor):
                return None
            recovered = await self._recover_finalization(worker_id, actor)
            if recovered is not None:
                return recovered
            work = self._acquire_work(worker_id, actor)
        except DomainError as exc:
            if exc.code == "EMERGENCY_STOP_ACTIVE":
                raise AutonomousWorkerError(
                    "EXECUTION_EMERGENCY_STOPPED", status_code=423
                ) from None
            raise
        if work is None:
            return None
        snapshot, execution, lease = work
        request = snapshot.specification.autonomous_execution
        if request is None:
            raise AutonomousWorkerError("CONTEXT_ASSEMBLY_REQUIRED")
        attempt_id = (
            execution.runtimeAttemptId
            if execution is not None
            else self._attempt_id(snapshot.specification.run_id)
        )
        try:
            if not self.executions.target_identity_active(snapshot.specification.agent_id):
                raise AutonomousWorkerError("RUNTIME_EXECUTION_NOT_ELIGIBLE")
            assembly = self.executions.load_context_assembly(request.context_assembly_id)
            self._validate_assembly(snapshot, assembly)
            snapshot = self._claim_and_start(snapshot, actor, worker_id, attempt_id)
            messages, execution_request_hash = self._execution_messages(assembly)
            recovered_uncommitted = execution is not None
            if execution is None:
                execution = self.executions.prepare(
                    snapshot=snapshot,
                    attempt_id=attempt_id,
                    assembly=assembly,
                    worker_id=worker_id,
                    task_attempt_number=lease.attemptNumber,
                    lease_token=lease.leaseToken,
                    execution_request_hash=execution_request_hash,
                )
            elif (
                execution.contextAssemblyId != assembly.id
                or execution.executionRequestHash != execution_request_hash
            ):
                raise AutonomousWorkerError("CONTEXT_ASSEMBLY_MISMATCH")
            snapshot = self._checkpoint(
                snapshot,
                actor,
                execution,
                attempt_id,
                (f"recovered-{lease.attemptNumber}" if recovered_uncommitted else "prepared"),
            )
            try:
                async with asyncio.timeout(
                    min(
                        request.maximum_execution_seconds,
                        self.settings.autonomous_worker_max_execution_seconds,
                    )
                ):
                    result = await self._call_and_validate(
                        snapshot=snapshot,
                        actor=actor,
                        worker_id=worker_id,
                        lease_token=lease.leaseToken,
                        execution=execution,
                        messages=messages,
                    )
            except TimeoutError as exc:
                raise AutonomousWorkerError("MODEL_EXECUTION_TIMEOUT") from exc
            self._assert_live_policy(snapshot, actor, worker_id, lease.leaseToken)
            execution = self.executions.persist_result(
                execution.executionId,
                worker_id=worker_id,
                lease_token=lease.leaseToken,
                result=result[0],
                provider=result[1].provider,
                model=result[1].model,
                input_tokens=result[2],
                output_tokens=result[3],
                request_count=result[4],
                latency_ms=result[5],
                finish_reason=result[1].finish_reason,
                estimated_cost_usd=result[6],
            )
            snapshot = self._checkpoint(
                self.runtime.read_run_authorized(snapshot.specification.run_id, actor),
                actor,
                execution,
                attempt_id,
                "result-persisted",
            )
            if execution.requiresHumanReview:
                return self._pause_for_review(
                    snapshot, actor, execution, worker_id, lease.leaseToken
                )
            return self._finalize(snapshot, actor, execution, worker_id, lease.leaseToken)
        except AutonomousWorkerError as exc:
            if execution is None and exc.code in {
                "RUNTIME_EXECUTION_NOT_ELIGIBLE",
                "CONTEXT_ASSEMBLY_REQUIRED",
                "CONTEXT_ASSEMBLY_UNAVAILABLE",
                "CONTEXT_ASSEMBLY_REVIEW_REQUIRED",
                "CONTEXT_ASSEMBLY_MISMATCH",
            }:
                self._pause_pre_execution(
                    snapshot,
                    actor,
                    worker_id,
                    lease.leaseToken,
                    exc.code.lower(),
                )
                return None
            if execution is not None and exc.code in {
                "RUNTIME_EXECUTION_NOT_ELIGIBLE",
                "MODEL_OUTPUT_INVALID",
                "MODEL_OUTPUT_REPAIR_EXHAUSTED",
                "NO_LOCAL_PROVIDER_AVAILABLE",
                "MODEL_EXECUTION_BUDGET_EXCEEDED",
                "MODEL_EXECUTION_TIMEOUT",
                "MODEL_EXECUTION_DISABLED",
                "LOCAL_PROVIDER_REQUIRED",
            }:
                return self._safe_pause_after_failure(
                    snapshot,
                    actor,
                    execution,
                    worker_id,
                    lease.leaseToken,
                    exc.code.lower(),
                )
            if exc.code == "EXECUTION_CANCELLED":
                cancelled = self._best_effort_cancel(snapshot, actor)
                if execution is not None and cancelled:
                    self.executions.mark_failed(execution.executionId, "execution_cancelled")
                raise
            if exc.code == "EXECUTION_EMERGENCY_STOPPED":
                self._best_effort_emergency_pause(snapshot, actor, worker_id, lease.leaseToken)
                raise
            if exc.code == "EXECUTION_LEASE_LOST":
                self._best_effort_abandon(snapshot, actor, attempt_id)
            raise
        except DomainError as exc:
            if exc.code == "TASK_LEASE_LOST":
                if self.task_leases.task_status(snapshot.specification.task_id) == "cancelled":
                    cancelled = self._best_effort_cancel(snapshot, actor)
                    if execution is not None and cancelled:
                        self.executions.mark_failed(
                            execution.executionId,
                            "execution_cancelled",
                        )
                    raise AutonomousWorkerError("EXECUTION_CANCELLED") from None
                self._best_effort_abandon(snapshot, actor, attempt_id)
                raise AutonomousWorkerError("EXECUTION_LEASE_LOST") from None
            if exc.code == "EMERGENCY_STOP_ACTIVE":
                self._best_effort_emergency_pause(snapshot, actor, worker_id, lease.leaseToken)
                raise AutonomousWorkerError(
                    "EXECUTION_EMERGENCY_STOPPED", status_code=423
                ) from None
            raise
        except RuntimePermissionDeniedError:
            raise AutonomousWorkerError("EXECUTION_AUTHORIZATION_REVOKED") from None

    def _acquire_work(
        self,
        worker_id: str,
        actor: RuntimeActorContext,
    ) -> tuple[AgentRunSnapshot, ModelExecutionResult | None, Any] | None:
        for page in self.executions.iter_preparation_transition_recovery_pages():
            for candidate in page:
                try:
                    recovered = self._recover_preparation_transition(
                        candidate,
                        worker_id,
                        actor,
                    )
                except RuntimePermissionDeniedError:
                    continue
                if recovered is not None:
                    return recovered

        for page in self.executions.iter_recoverable_uncommitted_pages():
            for execution in page:
                try:
                    snapshot = self.runtime.read_run_authorized(execution.runtimeRunId, actor)
                    if self._reconcile_cancelled_recovery(snapshot, actor, execution):
                        continue
                    if self._reconcile_failed_recovery(snapshot, actor, execution):
                        continue
                    if self._reconcile_uncommitted_review(snapshot, execution):
                        continue
                    if self.runtime.authorizer is not None:
                        self.runtime.authorizer.authorize(
                            actor,
                            "start_attempt",
                            snapshot=snapshot,
                        )
                except RuntimePermissionDeniedError:
                    continue
                request = snapshot.specification.autonomous_execution
                if request is None:
                    continue
                acquired = self.task_leases.acquire_task(
                    worker_id,
                    min(
                        self.settings.autonomous_worker_lease_seconds,
                        request.maximum_execution_seconds,
                    ),
                    execution.taskId,
                )
                if acquired is None:
                    continue
                _, lease = acquired
                execution = self.executions.reclaim_uncommitted(
                    execution.executionId,
                    worker_id=worker_id,
                    lease_token=lease.leaseToken,
                    task_attempt_number=lease.attemptNumber,
                )
                return snapshot, execution, lease

        for page in self.executions.iter_queued_autonomous_run_pages():
            for snapshot in page:
                try:
                    snapshot = self.runtime.read_run_authorized(
                        snapshot.specification.run_id, actor
                    )
                    if self.runtime.authorizer is not None:
                        self.runtime.authorizer.authorize(actor, "claim", snapshot=snapshot)
                except RuntimePermissionDeniedError:
                    continue
                request = snapshot.specification.autonomous_execution
                if request is None:
                    continue
                acquired = self.task_leases.acquire_task(
                    worker_id,
                    min(
                        self.settings.autonomous_worker_lease_seconds,
                        request.maximum_execution_seconds,
                    ),
                    snapshot.specification.task_id,
                )
                if acquired is not None:
                    _, lease = acquired
                    return snapshot, None, lease
        return None

    def _recover_preparation_transition(
        self,
        candidate: AgentRunSnapshot,
        worker_id: str,
        actor: RuntimeActorContext,
    ) -> tuple[AgentRunSnapshot, None, Any] | None:
        snapshot = self.runtime.read_run_authorized(
            candidate.specification.run_id,
            actor,
        )
        task_status = self.task_leases.task_status(snapshot.specification.task_id)
        if task_status == "cancelled":
            self._cancel_runtime(
                snapshot,
                actor,
                reason_code="task_cancelled",
                detail="Authoritative task cancellation observed",
            )
            return None
        if task_status == "failed":
            self._fail_runtime_for_task(snapshot, actor)
            return None
        if task_status == "completed":
            self._cancel_runtime(
                snapshot,
                actor,
                reason_code="task_completed_elsewhere",
                detail="Authoritative task completion superseded autonomous execution",
            )
            return None
        if self.runtime.authorizer is not None:
            self.runtime.authorizer.authorize(
                actor,
                "start_attempt",
                snapshot=snapshot,
            )
        request = snapshot.specification.autonomous_execution
        if request is None:
            return None
        acquired = self.task_leases.acquire_task(
            worker_id,
            min(
                self.settings.autonomous_worker_lease_seconds,
                request.maximum_execution_seconds,
            ),
            snapshot.specification.task_id,
        )
        if acquired is None:
            return None
        _, lease = acquired
        return snapshot, None, lease

    def read_result_authorized(
        self, execution_id: str, actor: RuntimeActorContext
    ) -> ModelExecutionResult:
        result = self.executions.get(execution_id)
        if result is None:
            raise AutonomousWorkerError("MODEL_RESULT_PERSISTENCE_FAILED", status_code=404)
        self.runtime.read_run_authorized(result.runtimeRunId, actor)
        return result

    def list_task_results_authorized(
        self, task_id: str, actor: RuntimeActorContext
    ) -> list[ModelExecutionResult]:
        results = self.executions.list_for_task(task_id)
        authorized: list[ModelExecutionResult] = []
        for result in results:
            self.runtime.read_run_authorized(result.runtimeRunId, actor)
            authorized.append(result)
        return authorized

    def _claim_and_start(
        self,
        snapshot: AgentRunSnapshot,
        actor: RuntimeActorContext,
        worker_id: str,
        attempt_id: str,
    ) -> AgentRunSnapshot:
        if snapshot.state == AgentRunState.QUEUED:
            snapshot = self._handle(
                ClaimAgentRunCommand,
                snapshot,
                actor,
                "claim",
                executor_reference=worker_id,
                detail="Autonomous planning execution claimed",
            )
        if snapshot.state == AgentRunState.CLAIMED:
            snapshot = self._handle(
                BeginAttemptCommand,
                snapshot,
                actor,
                "begin",
                attempt_id=attempt_id,
                executor_reference=worker_id,
                detail="Autonomous planning execution start requested",
            )
        if snapshot.state == AgentRunState.STARTING:
            snapshot = self._handle(
                StartAttemptCommand,
                snapshot,
                actor,
                "start",
                attempt_id=attempt_id,
                detail="Autonomous planning execution started",
            )
        if snapshot.state != AgentRunState.RUNNING:
            raise AutonomousWorkerError("RUNTIME_EXECUTION_NOT_ELIGIBLE")
        return snapshot

    async def _call_and_validate(
        self,
        *,
        snapshot: AgentRunSnapshot,
        actor: RuntimeActorContext,
        worker_id: str,
        lease_token: str,
        execution: ModelExecutionResult,
        messages: list[ModelMessage],
    ) -> tuple[
        PlanningReviewResult,
        ModelExecutionResponse,
        int | None,
        int | None,
        int,
        float,
        float | None,
    ]:
        request = snapshot.specification.autonomous_execution
        assert request is not None
        responses: list[ModelExecutionResponse] = []
        provider_requests = 0
        initial = ModelExecutionRequest(
            messages=messages,
            model=request.model_name,
            temperature=0,
            max_output_tokens=request.maximum_output_tokens,
            timeout_seconds=min(
                request.maximum_execution_seconds,
                self.settings.autonomous_worker_max_execution_seconds,
            ),
            task_id=snapshot.specification.task_id,
            correlation_id=snapshot.specification.correlation_id or snapshot.specification.run_id,
            required_capability=ModelCapability.CHAT,
        )
        response = await self._provider_call(
            snapshot,
            actor,
            worker_id,
            lease_token,
            execution.executionId,
            initial,
            provider_requests + 1,
        )
        provider_requests += 1
        responses.append(response)
        parsed, errors = self._parse_result(response.content)
        if parsed is None:
            self.executions.record_validation_failed(
                execution.executionId,
                worker_id=worker_id,
                lease_token=lease_token,
                request_count=provider_requests,
            )
            if (
                request.maximum_repair_calls == 0
                or self.settings.autonomous_worker_max_repair_calls == 0
                or provider_requests >= request.maximum_provider_requests
                or len(response.content) > 40_000
            ):
                raise AutonomousWorkerError("MODEL_OUTPUT_REPAIR_EXHAUSTED")
            repair = ModelExecutionRequest(
                messages=[
                    ModelMessage(
                        role=MessageRole.SYSTEM,
                        content=PlanningReviewResult.model_json_schema_instruction(),
                    ),
                    ModelMessage(
                        role=MessageRole.USER,
                        content=(
                            "Invalid generated output:\n"
                            f"{response.content}\n"
                            "Validation errors:\n" + "\n".join(errors[:12])
                        ),
                    ),
                ],
                model=request.model_name,
                temperature=0,
                max_output_tokens=request.maximum_output_tokens,
                timeout_seconds=min(
                    request.maximum_execution_seconds,
                    self.settings.autonomous_worker_max_execution_seconds,
                ),
                task_id=snapshot.specification.task_id,
                correlation_id=snapshot.specification.correlation_id
                or snapshot.specification.run_id,
                required_capability=ModelCapability.CHAT,
            )
            response = await self._provider_call(
                snapshot,
                actor,
                worker_id,
                lease_token,
                execution.executionId,
                repair,
                provider_requests + 1,
            )
            provider_requests += 1
            responses.append(response)
            parsed, _ = self._parse_result(response.content)
            if parsed is None:
                raise AutonomousWorkerError("MODEL_OUTPUT_REPAIR_EXHAUSTED")
        return (
            parsed,
            response,
            self._sum_optional(item.input_tokens for item in responses),
            self._sum_optional(item.output_tokens for item in responses),
            provider_requests,
            sum(item.latency_ms for item in responses),
            self._sum_optional_float(item.estimated_cost_usd for item in responses),
        )

    async def _provider_call(
        self,
        snapshot: AgentRunSnapshot,
        actor: RuntimeActorContext,
        worker_id: str,
        lease_token: str,
        execution_id: str,
        request_payload: ModelExecutionRequest,
        request_count: int,
    ) -> ModelExecutionResponse:
        request = snapshot.specification.autonomous_execution
        assert request is not None
        self._assert_live_policy(snapshot, actor, worker_id, lease_token)
        self.executions.record_call_started(
            execution_id,
            worker_id=worker_id,
            lease_token=lease_token,
            request_count=request_count,
        )
        requirements = RoutingRequirements(
            requested_provider=request.provider_preference,
            required_capability=ModelCapability.CHAT,
            preferred_model=request.model_name,
            prefer_local=True,
            allow_remote=False,
            allow_fallback=False,
            maximum_fallbacks=0,
        )
        budget = TaskBudget(
            maximum_requests=1,
            maximum_output_tokens=request.maximum_output_tokens,
        )
        timeout = min(
            request.maximum_execution_seconds,
            self.settings.autonomous_worker_max_execution_seconds,
        )
        heartbeat = asyncio.create_task(
            self._lease_heartbeat(
                snapshot.specification.task_id,
                worker_id,
                lease_token,
            )
        )
        try:
            async with asyncio.timeout(timeout):
                response = await self.router.execute(
                    request=request_payload,
                    requirements=requirements,
                    budget=budget,
                )
        except TimeoutError as exc:
            raise AutonomousWorkerError("MODEL_EXECUTION_TIMEOUT") from exc
        except RequestTimeoutError as exc:
            raise AutonomousWorkerError("MODEL_EXECUTION_TIMEOUT") from exc
        except BudgetExceededError as exc:
            raise AutonomousWorkerError("MODEL_EXECUTION_BUDGET_EXCEEDED") from exc
        except ProviderExecutionDisabledError as exc:
            raise AutonomousWorkerError("MODEL_EXECUTION_DISABLED", status_code=503) from exc
        except UnknownProviderError as exc:
            raise AutonomousWorkerError("NO_LOCAL_PROVIDER_AVAILABLE", status_code=503) from exc
        except ModelProviderError as exc:
            raise AutonomousWorkerError("NO_LOCAL_PROVIDER_AVAILABLE", status_code=503) from exc
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass
        self._assert_live_policy(snapshot, actor, worker_id, lease_token)
        self.executions.record_response_received(
            execution_id,
            worker_id=worker_id,
            lease_token=lease_token,
            request_count=request_count,
        )
        return response

    async def _lease_heartbeat(self, task_id: str, worker_id: str, lease_token: str) -> None:
        while True:
            await asyncio.sleep(self.settings.autonomous_worker_heartbeat_interval_seconds)
            self.task_leases.renew_lease(
                task_id,
                worker_id,
                lease_token,
                self.settings.autonomous_worker_lease_seconds,
            )

    def _assert_live_policy(
        self,
        snapshot: AgentRunSnapshot,
        actor: RuntimeActorContext,
        worker_id: str,
        lease_token: str,
    ) -> None:
        self.validate_enabled()
        current = self.runtime.read_run_authorized(snapshot.specification.run_id, actor)
        if not self.executions.target_identity_active(current.specification.agent_id):
            raise AutonomousWorkerError("RUNTIME_EXECUTION_NOT_ELIGIBLE")
        if current.state in {
            AgentRunState.CANCEL_REQUESTED,
            AgentRunState.CANCELLING,
            AgentRunState.CANCELLED,
        }:
            raise AutonomousWorkerError("EXECUTION_CANCELLED")
        if self.runtime.authorizer is not None:
            self.runtime.authorizer.authorize(actor, "start_attempt", snapshot=current)
        self.task_leases.assert_current(
            snapshot.specification.task_id,
            worker_id,
            lease_token,
        )

    def _checkpoint(
        self,
        snapshot: AgentRunSnapshot,
        actor: RuntimeActorContext,
        execution: ModelExecutionResult,
        attempt_id: str,
        name: str,
    ) -> AgentRunSnapshot:
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
        return self._handle(
            RecordCheckpointCommand,
            snapshot,
            actor,
            f"checkpoint-{name}",
            checkpoint_id=f"checkpoint-{execution.executionId[-32:]}-{name}",
            attempt_id=attempt_id,
            state_reference=f"model-execution:{execution.executionId}:{name}",
            integrity_digest=digest,
            checkpoint_metadata={
                "executionId": execution.executionId,
                "stage": name,
            },
        )

    def _finalize(
        self,
        snapshot: AgentRunSnapshot,
        actor: RuntimeActorContext,
        execution: ModelExecutionResult,
        worker_id: str,
        lease_token: str,
    ) -> ModelExecutionResult:
        self._assert_live_policy(snapshot, actor, worker_id, lease_token)
        execution = self.executions.mark_finalization_pending(
            execution.executionId,
            worker_id=worker_id,
            lease_token=lease_token,
        )
        self.task_leases.complete_task(
            execution.taskId,
            worker_id,
            lease_token,
            f"model-execution:{execution.executionId}",
        )
        snapshot = self.runtime.read_run_authorized(execution.runtimeRunId, actor)
        return self._finalize_committed_task(snapshot, actor, execution)

    def _finalize_committed_task(
        self,
        snapshot: AgentRunSnapshot,
        actor: RuntimeActorContext,
        execution: ModelExecutionResult,
    ) -> ModelExecutionResult:
        self.task_leases.assert_execution_enabled()
        task_state = self.task_leases.task_recovery_state(execution.taskId)
        if (
            task_state is None
            or task_state[0] != "completed"
            or task_state[1] != f"model-execution:{execution.executionId}"
        ):
            raise AutonomousWorkerError("MODEL_RESULT_CONFLICT")
        if snapshot.state == AgentRunState.RUNNING:
            snapshot = self._handle(
                CompleteAttemptCommand,
                snapshot,
                actor,
                "complete-attempt",
                require_execution_enabled=True,
                attempt_id=execution.runtimeAttemptId,
                detail="Validated planning result persisted",
            )
        if snapshot.state == AgentRunState.CLAIMED:
            snapshot = self._handle(
                CompleteAgentRunCommand,
                snapshot,
                actor,
                "complete-run",
                require_execution_enabled=True,
                detail="Autonomous planning execution completed",
            )
        if snapshot.state != AgentRunState.SUCCEEDED:
            raise AutonomousWorkerError("RUNTIME_EXECUTION_NOT_ELIGIBLE")
        return self.executions.mark_completed(execution.executionId)

    def _pause_for_review(
        self,
        snapshot: AgentRunSnapshot,
        actor: RuntimeActorContext,
        execution: ModelExecutionResult,
        worker_id: str,
        lease_token: str,
    ) -> ModelExecutionResult:
        snapshot = self._confirm_review_pause(
            snapshot,
            actor,
            reason_code="model_result_review_required",
            request_suffix="request-review",
            request_detail="Validated result requires human review",
            confirm_suffix="confirm-review",
            confirm_detail="Execution paused for human review",
        )
        self.task_leases.pause_for_review(
            execution.taskId,
            worker_id,
            lease_token,
            f"model-execution:{execution.executionId}",
        )
        return self.executions.mark_failed(
            execution.executionId,
            "human_review_required",
            human_review=True,
        )

    def _safe_pause_after_failure(
        self,
        snapshot: AgentRunSnapshot,
        actor: RuntimeActorContext,
        execution: ModelExecutionResult,
        worker_id: str,
        lease_token: str,
        failure_code: str,
    ) -> ModelExecutionResult:
        current = self.runtime.read_run_authorized(snapshot.specification.run_id, actor)
        if (
            current.state in {AgentRunState.PAUSE_REQUESTED, AgentRunState.PAUSED}
            and current.pause_reason is not None
        ):
            failure_code = current.pause_reason.code
        current = self._confirm_review_pause(
            current,
            actor,
            reason_code=failure_code,
            request_suffix="request-failure-review",
            request_detail="Autonomous execution requires operator review",
            confirm_suffix="confirm-failure-review",
            confirm_detail="Execution paused for operator review",
        )
        self.task_leases.pause_for_review(
            execution.taskId,
            worker_id,
            lease_token,
            None,
        )
        return self.executions.mark_failed(
            execution.executionId,
            failure_code,
            human_review=True,
        )

    def _reconcile_uncommitted_review(
        self,
        snapshot: AgentRunSnapshot,
        execution: ModelExecutionResult,
    ) -> bool:
        if (
            snapshot.state != AgentRunState.PAUSED
            or snapshot.pause_reason is None
            or self.task_leases.task_status(execution.taskId) != "under_review"
        ):
            return False
        self.executions.mark_failed(
            execution.executionId,
            snapshot.pause_reason.code,
            human_review=True,
        )
        return True

    def _pause_pre_execution(
        self,
        snapshot: AgentRunSnapshot,
        actor: RuntimeActorContext,
        worker_id: str,
        lease_token: str,
        failure_code: str,
    ) -> None:
        current = self.runtime.read_run_authorized(snapshot.specification.run_id, actor)
        current = self._confirm_review_pause(
            current,
            actor,
            reason_code=failure_code,
            request_suffix="pre-execution-review-request",
            request_detail="Autonomous execution request requires operator review",
            confirm_suffix="pre-execution-review-confirm",
            confirm_detail="Autonomous execution paused before model access",
            requestable_states={
                AgentRunState.QUEUED,
                AgentRunState.CLAIMED,
                AgentRunState.RUNNING,
            },
        )
        self.task_leases.pause_for_review(
            snapshot.specification.task_id,
            worker_id,
            lease_token,
            None,
        )

    def _recover_pre_execution_pause(
        self,
        worker_id: str,
        actor: RuntimeActorContext,
    ) -> bool:
        for page in self.executions.iter_pre_execution_pause_recovery_pages():
            for candidate in page:
                try:
                    if self._recover_pre_execution_pause_candidate(
                        candidate,
                        worker_id,
                        actor,
                    ):
                        return True
                except RuntimePermissionDeniedError:
                    continue
        return False

    def _recover_pre_execution_pause_candidate(
        self,
        candidate: AgentRunSnapshot,
        worker_id: str,
        actor: RuntimeActorContext,
    ) -> bool:
        current = self.runtime.read_run_authorized(
            candidate.specification.run_id,
            actor,
        )
        pause_reason = current.pause_reason
        if pause_reason is None or pause_reason.code not in PRE_EXECUTION_REVIEW_CODES:
            return False
        task_id = current.specification.task_id
        task_status = self.task_leases.task_status(task_id)
        if task_status == "cancelled":
            self._authorize_recovery_action(current, actor, "confirm_cancellation")
            self._cancel_runtime(
                current,
                actor,
                reason_code="task_cancelled",
                detail="Authoritative task cancellation observed",
            )
            return True
        if task_status == "failed":
            self._authorize_recovery_action(current, actor, "fail_run")
            self._fail_runtime_for_task(current, actor)
            return True
        if task_status == "completed":
            self._authorize_recovery_action(current, actor, "confirm_cancellation")
            self._cancel_runtime(
                current,
                actor,
                reason_code="task_completed_elsewhere",
                detail="Authoritative task completion superseded autonomous execution",
            )
            return True
        if current.state == AgentRunState.PAUSED and task_status == "under_review":
            return False
        request = current.specification.autonomous_execution
        if request is None:
            return False
        self._authorize_recovery_action(current, actor, "confirm_pause")
        acquired = self.task_leases.acquire_task(
            worker_id,
            min(
                self.settings.autonomous_worker_lease_seconds,
                request.maximum_execution_seconds,
            ),
            task_id,
        )
        if acquired is None:
            return False
        _, lease = acquired
        current = self.runtime.read_run_authorized(
            current.specification.run_id,
            actor,
        )
        current = self._confirm_review_pause(
            current,
            actor,
            reason_code=pause_reason.code,
            request_suffix="pre-execution-review-request",
            request_detail="Autonomous execution request requires operator review",
            confirm_suffix="pre-execution-review-confirm",
            confirm_detail="Autonomous execution paused before model access",
            requestable_states={
                AgentRunState.QUEUED,
                AgentRunState.CLAIMED,
                AgentRunState.RUNNING,
            },
        )
        try:
            self.task_leases.pause_for_review(
                task_id,
                worker_id,
                lease.leaseToken,
                None,
            )
        except DomainError as exc:
            if exc.code != "TASK_LEASE_LOST":
                raise
            task_status = self.task_leases.task_status(task_id)
            if task_status == "cancelled":
                self._cancel_runtime(
                    current,
                    actor,
                    reason_code="task_cancelled",
                    detail="Authoritative task cancellation observed",
                )
                return True
            if task_status == "failed":
                self._fail_runtime_for_task(current, actor)
                return True
            if task_status == "completed":
                self._cancel_runtime(
                    current,
                    actor,
                    reason_code="task_completed_elsewhere",
                    detail="Authoritative task completion superseded autonomous execution",
                )
                return True
            raise AutonomousWorkerError("EXECUTION_LEASE_LOST") from None
        return True

    def _confirm_review_pause(
        self,
        snapshot: AgentRunSnapshot,
        actor: RuntimeActorContext,
        *,
        reason_code: str,
        request_suffix: str,
        request_detail: str,
        confirm_suffix: str,
        confirm_detail: str,
        requestable_states: set[AgentRunState] | None = None,
    ) -> AgentRunSnapshot:
        if self.runtime.authorizer is not None:
            self.runtime.authorizer.authorize(actor, "confirm_pause", snapshot=snapshot)
        if snapshot.state in (
            requestable_states if requestable_states is not None else {AgentRunState.RUNNING}
        ):
            snapshot = self._handle(
                RequestPauseCommand,
                snapshot,
                actor,
                request_suffix,
                reason_code=reason_code,
                detail=request_detail,
            )
        if snapshot.state == AgentRunState.PAUSE_REQUESTED:
            snapshot = self._handle(
                ConfirmPauseCommand,
                snapshot,
                actor,
                confirm_suffix,
                detail=confirm_detail,
            )
        if snapshot.state != AgentRunState.PAUSED:
            raise AutonomousWorkerError("RUNTIME_EXECUTION_NOT_ELIGIBLE")
        return snapshot

    async def _recover_finalization(
        self, worker_id: str, actor: RuntimeActorContext
    ) -> ModelExecutionResult | None:
        for page in self.executions.iter_recoverable_result_pages():
            for execution in page:
                try:
                    recovered = self._recover_finalization_candidate(
                        execution,
                        worker_id,
                        actor,
                    )
                except RuntimePermissionDeniedError:
                    continue
                if recovered is not None:
                    return recovered
        return None

    def _recover_finalization_candidate(
        self,
        execution: ModelExecutionResult,
        worker_id: str,
        actor: RuntimeActorContext,
    ) -> ModelExecutionResult | None:
        snapshot = self.runtime.read_run_authorized(execution.runtimeRunId, actor)
        task_state = self.task_leases.task_recovery_state(execution.taskId)
        task_status = None if task_state is None else task_state[0]
        if task_status == "cancelled":
            self._authorize_recovery_action(snapshot, actor, "confirm_cancellation")
        elif task_status == "failed":
            self._authorize_recovery_action(snapshot, actor, "fail_run")
        elif execution.requiresHumanReview:
            self._authorize_recovery_action(snapshot, actor, "confirm_pause")
        else:
            self._authorize_recovery_action(snapshot, actor, "complete_run")
        if self._reconcile_cancelled_recovery(snapshot, actor, execution):
            return None
        if self._reconcile_failed_recovery(snapshot, actor, execution):
            return None
        if execution.requiresHumanReview:
            if (
                snapshot.state == AgentRunState.PAUSED
                and task_state is not None
                and task_state[0] == "under_review"
            ):
                return self.executions.mark_failed(
                    execution.executionId,
                    "human_review_required",
                    human_review=True,
                )
        elif task_state is not None and task_state[0] == "completed":
            return self._finalize_committed_task(snapshot, actor, execution)
        acquired = self.task_leases.acquire_task(
            worker_id,
            self.settings.autonomous_worker_lease_seconds,
            execution.taskId,
        )
        if acquired is None:
            return None
        _, lease = acquired
        if execution.requiresHumanReview:
            return self._pause_for_review(
                snapshot,
                actor,
                execution,
                worker_id,
                lease.leaseToken,
            )
        return self._finalize(
            snapshot,
            actor,
            execution,
            worker_id,
            lease.leaseToken,
        )

    def _authorize_recovery_action(
        self,
        snapshot: AgentRunSnapshot,
        actor: RuntimeActorContext,
        operation: str,
    ) -> None:
        if self.runtime.authorizer is not None:
            self.runtime.authorizer.authorize(actor, operation, snapshot=snapshot)

    def _reconcile_cancelled_recovery(
        self,
        snapshot: AgentRunSnapshot,
        actor: RuntimeActorContext,
        execution: ModelExecutionResult,
    ) -> bool:
        if self.task_leases.task_status(execution.taskId) != "cancelled":
            return False
        self._cancel_runtime(
            snapshot,
            actor,
            reason_code="task_cancelled",
            detail="Authoritative task cancellation observed",
        )
        self.executions.mark_failed(execution.executionId, "execution_cancelled")
        return True

    def _reconcile_failed_recovery(
        self,
        snapshot: AgentRunSnapshot,
        actor: RuntimeActorContext,
        execution: ModelExecutionResult,
    ) -> bool:
        if self.task_leases.task_status(execution.taskId) != "failed":
            return False
        self._fail_runtime_for_task(snapshot, actor)
        self.executions.mark_failed(execution.executionId, "task_failed")
        return True

    def _fail_runtime_for_task(
        self,
        snapshot: AgentRunSnapshot,
        actor: RuntimeActorContext,
    ) -> AgentRunSnapshot:
        current = self.runtime.read_run_authorized(snapshot.specification.run_id, actor)
        if current.active_attempt_id is not None and current.state in {
            AgentRunState.STARTING,
            AgentRunState.RUNNING,
            AgentRunState.PAUSE_REQUESTED,
            AgentRunState.PAUSED,
        }:
            current = self._handle(
                FailAttemptCommand,
                current,
                actor,
                "task-failed-attempt",
                attempt_id=current.active_attempt_id,
                failure_category=FailureClassification.EXECUTION,
                failure_detail="The authoritative task failed after lease recovery",
            )
        if current.state in {
            AgentRunState.CREATED,
            AgentRunState.QUEUED,
            AgentRunState.CLAIMED,
            AgentRunState.BLOCKED,
        }:
            current = self._handle(
                FailAgentRunCommand,
                current,
                actor,
                "task-failed-run",
                failure_category=FailureClassification.EXECUTION,
                failure_detail="The authoritative task failed after lease recovery",
            )
        if current.state != AgentRunState.FAILED:
            raise AutonomousWorkerError("RUNTIME_EXECUTION_NOT_ELIGIBLE")
        return current

    def _handle(
        self,
        command_type: type[RuntimeCommand],
        snapshot: AgentRunSnapshot,
        actor: RuntimeActorContext,
        suffix: str,
        require_execution_enabled: bool = False,
        **fields: Any,
    ) -> AgentRunSnapshot:
        command_id = (
            f"aw-{sha256(snapshot.specification.run_id.encode()).hexdigest()[:24]}-{suffix}"
        )
        timestamp = self._command_timestamp(snapshot.specification.run_id, command_id)
        command = command_type(
            run_id=snapshot.specification.run_id,
            command_id=command_id,
            expected_run_version=snapshot.version,
            timestamp=timestamp,
            **fields,
        )
        result = self.runtime.handle_authorized(
            command,
            actor,
            require_execution_enabled=require_execution_enabled,
        )
        if result.snapshot is None:
            raise AutonomousWorkerError("RUNTIME_EXECUTION_NOT_ELIGIBLE")
        return result.snapshot

    def _command_timestamp(self, run_id: str, command_id: str) -> datetime:
        for event in self.runtime.repository.list_events(run_id):
            if event.command_id == command_id:
                return event.timestamp
        return datetime.now(UTC)

    @staticmethod
    def _attempt_id(run_id: str) -> str:
        return f"attempt-aw-{sha256(run_id.encode()).hexdigest()[:40]}"

    @staticmethod
    def _execution_messages(
        assembly: ContextAssembly,
    ) -> tuple[list[ModelMessage], str]:
        assert assembly.modelRequest is not None
        messages: list[ModelMessage] = []
        for message in assembly.modelRequest.messages:
            role = {
                "system": MessageRole.SYSTEM,
                "developer": MessageRole.USER,
                "user": MessageRole.USER,
            }[message.role]
            content = (
                f"[ASSEMBLY_DEVELOPER_DATA]\n{message.content}"
                if message.role == "developer"
                else message.content
            )
            messages.append(ModelMessage(role=role, content=content))
        messages.append(
            ModelMessage(
                role=MessageRole.SYSTEM,
                content=PlanningReviewResult.model_json_schema_instruction(),
            )
        )
        digest = sha256(
            canonical_json(
                {
                    "assemblyRequestHash": assembly.requestHash,
                    "messages": [message.model_dump(mode="json") for message in messages],
                    "executionType": "planning_review",
                    "outputSchemaVersion": "1.0",
                }
            ).encode()
        ).hexdigest()
        return messages, digest

    @staticmethod
    def _validate_assembly(snapshot: AgentRunSnapshot, assembly: ContextAssembly) -> None:
        request = snapshot.specification.autonomous_execution
        if request is None:
            raise AutonomousWorkerError("CONTEXT_ASSEMBLY_REQUIRED")
        if (
            request.context_assembly_id != assembly.id
            or assembly.taskId != snapshot.specification.task_id
        ):
            raise AutonomousWorkerError("CONTEXT_ASSEMBLY_MISMATCH")
        if assembly.status == "review_required":
            raise AutonomousWorkerError("CONTEXT_ASSEMBLY_REVIEW_REQUIRED")
        if assembly.status != "completed" or assembly.modelRequest is None:
            raise AutonomousWorkerError("CONTEXT_ASSEMBLY_UNAVAILABLE")

    @staticmethod
    def _parse_result(content: str) -> tuple[PlanningReviewResult | None, list[str]]:
        try:
            payload = json.loads(content)
            if not isinstance(payload, dict):
                return None, ["root: expected a JSON object"]
            return PlanningReviewResult.model_validate(payload), []
        except json.JSONDecodeError:
            return None, ["root: invalid JSON"]
        except ValidationError as exc:
            errors = [
                f"{'.'.join(str(item) for item in error['loc'])}: {error['type']}"
                for error in exc.errors(include_input=False, include_url=False)
            ]
            return None, errors

    @staticmethod
    def _sum_optional(values: Any) -> int | None:
        items = list(values)
        return None if any(item is None for item in items) else sum(items)

    @staticmethod
    def _sum_optional_float(values: Any) -> float | None:
        items = list(values)
        return None if any(item is None for item in items) else sum(items)

    def _best_effort_abandon(
        self,
        snapshot: AgentRunSnapshot,
        actor: RuntimeActorContext,
        attempt_id: str,
    ) -> None:
        try:
            current = self.runtime.read_run_authorized(snapshot.specification.run_id, actor)
            if current.state in {AgentRunState.RUNNING, AgentRunState.STARTING}:
                self._handle(
                    AbandonAttemptCommand,
                    current,
                    actor,
                    "lease-lost",
                    attempt_id=attempt_id,
                    detail="Execution ownership was lost",
                )
        except Exception:
            return

    def _best_effort_cancel(
        self,
        snapshot: AgentRunSnapshot,
        actor: RuntimeActorContext,
        *,
        reason_code: str = "task_cancelled",
        detail: str = "Authoritative task cancellation observed",
    ) -> bool:
        try:
            self._cancel_runtime(
                snapshot,
                actor,
                reason_code=reason_code,
                detail=detail,
            )
        except Exception:
            return False
        try:
            self.task_leases.cancel_task(snapshot.specification.task_id)
        except Exception:
            pass
        return True

    def _cancel_runtime(
        self,
        snapshot: AgentRunSnapshot,
        actor: RuntimeActorContext,
        *,
        reason_code: str,
        detail: str,
    ) -> AgentRunSnapshot:
        from app.models.agent_runtime import (
            ConfirmCancellationCommand,
            ConfirmCancellationStartCommand,
            RequestCancellationCommand,
        )

        current = self.runtime.read_run_authorized(snapshot.specification.run_id, actor)
        if current.state in {
            AgentRunState.QUEUED,
            AgentRunState.CLAIMED,
            AgentRunState.STARTING,
            AgentRunState.RUNNING,
            AgentRunState.PAUSE_REQUESTED,
            AgentRunState.PAUSED,
            AgentRunState.BLOCKED,
        }:
            current = self._handle(
                RequestCancellationCommand,
                current,
                actor,
                "cancel-request",
                reason_code=reason_code,
                detail=detail,
                requester_reference=actor.actor_id,
            )
        if current.state == AgentRunState.CANCEL_REQUESTED:
            if current.active_attempt_id is not None:
                current = self._handle(
                    ConfirmCancellationStartCommand,
                    current,
                    actor,
                    "cancel-start",
                    detail="Autonomous execution cancellation started",
                )
        if current.state in {
            AgentRunState.CANCEL_REQUESTED,
            AgentRunState.CANCELLING,
        }:
            current = self._handle(
                ConfirmCancellationCommand,
                current,
                actor,
                "cancel-confirm",
                detail="Autonomous execution cancelled",
            )
        if current.state != AgentRunState.CANCELLED:
            raise AutonomousWorkerError("RUNTIME_EXECUTION_NOT_ELIGIBLE")
        return current

    def _best_effort_emergency_pause(
        self,
        snapshot: AgentRunSnapshot,
        actor: RuntimeActorContext,
        worker_id: str,
        lease_token: str,
    ) -> None:
        try:
            current = self.runtime.read_run_authorized(snapshot.specification.run_id, actor)
            if current.state == AgentRunState.RUNNING:
                current = self._handle(
                    RequestPauseCommand,
                    current,
                    actor,
                    "emergency-pause-request",
                    reason_code="emergency_stop",
                    detail="Emergency stop paused autonomous execution",
                )
                self._handle(
                    ConfirmPauseCommand,
                    current,
                    actor,
                    "emergency-pause-confirm",
                    detail="Autonomous execution paused by emergency stop",
                )
            self.task_leases.release_lease(snapshot.specification.task_id, worker_id, lease_token)
        except Exception:
            return
