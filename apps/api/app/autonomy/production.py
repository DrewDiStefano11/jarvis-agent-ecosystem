"""Production adapters: real main-branch stages used by the harness.

This module adapts functionality that already exists on ``main``:

- :class:`ContextGroundingAdapter` — the real
  :class:`app.context.assembler.ContextAssembler` (ground-context stage).
- :class:`RuntimeExecutionAdapter` — the real
  :class:`app.agent_runtime.service.AgentRuntimeService` command ledger
  (durable attempts, validated checkpoints, recovery plans, cancellation).

Security properties enforced here:

- Only *validated* output summaries are ever persisted to runtime checkpoints.
  Raw specialist/model text is untrusted data and never becomes authoritative
  state (see :class:`ValidatedCheckpointPayload`).
- All commands flow through ``handle_authorized`` so identity/RBAC allow/deny
  decisions are enforced exactly as in production.
- Runtime errors are mapped to machine-readable refusal codes; raw exception
  internals never reach evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Any

from app.agent_runtime.errors import (
    AttemptLimitExceededError,
    AttemptNotFoundError,
    CheckpointLineageError,
    CheckpointNotAllowedError,
    CheckpointSequenceConflictError,
    CommandConflictError,
    InvalidAttemptStateError,
    InvalidTransitionError,
    RecoveryNotAllowedError,
    RunAlreadyExistsError,
    RunNotFoundError,
    RuntimeActorInactiveError,
    RuntimeActorNotFoundError,
    RuntimeAuthenticationRequiredError,
    RuntimePermissionDeniedError,
    TerminalRunImmutableError,
    VersionConflictError,
)
from app.agent_runtime.service import AgentRuntimeService
from app.autonomy.ports import StageKind, StageProvenance
from app.context.assembler import ContextAssembler
from app.models.agent_runtime import (
    AgentRunAttempt,
    AgentRunCheckpoint,
    AgentRunSnapshot,
    AgentRunSpecification,
    BeginAttemptCommand,
    ClaimAgentRunCommand,
    CompleteAgentRunCommand,
    CompleteAttemptCommand,
    ConfirmCancellationCommand,
    ConfirmCancellationStartCommand,
    CreateAgentRunCommand,
    FailAgentRunCommand,
    FailAttemptCommand,
    FailureClassification,
    QueueAgentRunCommand,
    RecordCheckpointCommand,
    RecoveryPlan,
    RequestCancellationCommand,
    RequestRecoveryPlanCommand,
    RuntimeCommandResult,
    StartAttemptCommand,
    UnblockAgentRunCommand,
)
from app.models.context import (
    ContextAssembly,
    CreateContextAssemblyRequest,
)
from app.models.domain import Task

GROUNDING_PROVENANCE = StageProvenance(
    stage=StageKind.GROUND_CONTEXT,
    implementation="production",
    detail="Real ContextAssembler over explicit bounded sources.",
)

EXECUTION_PROVENANCE = StageProvenance(
    stage=StageKind.EXECUTE_SPECIALIST,
    implementation="production",
    detail="Real AgentRuntimeService ledger; only specialist content is fixture-supplied.",
)

RECOVERY_PROVENANCE = StageProvenance(
    stage=StageKind.RECOVER_RETRY,
    implementation="production",
    detail="Real runtime recovery plans, bounded attempts, checkpoint lineage.",
)


class ContextGroundingAdapter:
    """Ground an objective through the production context assembler."""

    is_fixture = False
    provenance = GROUNDING_PROVENANCE

    def __init__(self, assembler: ContextAssembler) -> None:
        self.assembler = assembler

    def ground(
        self,
        task: Task,
        command: CreateContextAssemblyRequest,
        *,
        created_at: datetime | None = None,
    ) -> ContextAssembly:
        return self.assembler.assemble(task, command, created_at=created_at)


@dataclass(frozen=True)
class ValidatedCheckpointPayload:
    """The only output-derived data permitted in durable checkpoints.

    ``output_digest`` is the SHA-256 of the raw output (for lineage without
    storing untrusted text); ``summary`` is the harness-validated bounded
    summary. Raw model/specialist text is never stored.
    """

    node_id: str
    attempt_number: int
    output_digest: str
    summary: str
    schema_name: str = "autonomy-validated-output/v1"


def digest_output(output_text: str) -> str:
    """SHA-256 digest in the runtime's ``algorithm:hex`` checkpoint format."""
    return f"sha256:{sha256(output_text.encode('utf-8')).hexdigest()}"


@dataclass(frozen=True)
class AdapterOutcome:
    committed: bool
    snapshot: AgentRunSnapshot
    idempotent_replay: bool = False
    recovery_plan: RecoveryPlan | None = None


@dataclass(frozen=True)
class RuntimeRefusal(Exception):
    """Machine-readable refusal mapping for a runtime command error."""

    code: str
    detail: str
    operation: str


def map_runtime_error(error: Exception, operation: str) -> RuntimeRefusal:
    """Map runtime errors to stable machine-readable codes.

    Only the code plus a fixed generic detail are exposed; raw exception
    internals (tracebacks, SQL, provider text) never propagate to evidence.
    """
    table: tuple[tuple[type[Exception], str, str], ...] = (
        (RuntimePermissionDeniedError, "authorization_denied", "runtime authorization denied"),
        (
            RuntimeAuthenticationRequiredError,
            "authentication_failed",
            "runtime actor authentication failed",
        ),
        (RuntimeActorNotFoundError, "authentication_failed", "runtime actor not found"),
        (RuntimeActorInactiveError, "authentication_failed", "runtime actor is inactive"),
        (VersionConflictError, "version_conflict", "run version conflict"),
        (CommandConflictError, "command_conflict", "command idempotency conflict"),
        (AttemptLimitExceededError, "attempt_limit_exceeded", "maximum attempts exceeded"),
        (TerminalRunImmutableError, "terminal_immutable", "terminal run is immutable"),
        (AttemptNotFoundError, "invalid_state", "referenced attempt is not available"),
        (InvalidAttemptStateError, "invalid_state", "attempt state transition not allowed"),
        (InvalidTransitionError, "invalid_state", "run state transition not allowed"),
        (CheckpointLineageError, "checkpoint_conflict", "checkpoint lineage mismatch"),
        (CheckpointNotAllowedError, "checkpoint_conflict", "checkpoint not allowed"),
        (
            CheckpointSequenceConflictError,
            "checkpoint_conflict",
            "checkpoint id already used differently",
        ),
        (RecoveryNotAllowedError, "recovery_not_allowed", "recovery plan not applicable"),
        (RunAlreadyExistsError, "run_exists", "run already exists"),
        (RunNotFoundError, "run_not_found", "run not found"),
    )
    for error_type, code, detail in table:
        if isinstance(error, error_type):
            return RuntimeRefusal(code=code, detail=detail, operation=operation)
    return RuntimeRefusal(
        code="runtime_error", detail="unexpected runtime error", operation=operation
    )


class RuntimeExecutionAdapter:
    """Durable execution through the real agent-runtime command ledger.

    Command ids are deterministic (``{run_id}:{operation}`` qualified): a
    resumed harness re-derives identical ids, and already-processed commands
    are skipped via ``get_processed_command`` instead of being re-issued. This
    keeps command ids and checkpoints consistent across restarts and makes
    duplicate durable results structurally impossible.
    """

    is_fixture = False
    provenance = EXECUTION_PROVENANCE

    def __init__(self, service: AgentRuntimeService, *, actor_id: str) -> None:
        self.service = service
        self.actor_id = actor_id
        self.issued_command_ids: list[str] = []

    # -- command-id scheme -------------------------------------------------
    @staticmethod
    def command_id(run_id: str, operation: str, qualifier: str = "") -> str:
        suffix = f":{qualifier}" if qualifier else ""
        return f"{run_id}:{operation}{suffix}"

    # -- state access ------------------------------------------------------
    def load_run(self, run_id: str) -> AgentRunSnapshot | None:
        return self.service.repository.load_run(run_id)

    def load_attempts(self, run_id: str) -> tuple[AgentRunAttempt, ...]:
        return tuple(self.service.repository.load_attempt_history(run_id))

    def load_checkpoints(self, run_id: str) -> tuple[AgentRunCheckpoint, ...]:
        return tuple(self.service.repository.list_checkpoints(run_id))

    def event_count(self, run_id: str) -> int:
        return len(self.service.repository.list_events(run_id))

    def processed_command_ids(self, run_id: str) -> tuple[str, ...]:
        records = self.service.repository.list_processed_commands(run_id)
        return tuple(sorted(record.command_id for record in records))

    def is_processed(self, run_id: str, command_id: str) -> bool:
        return self.service.repository.get_processed_command(run_id, command_id) is not None

    def probe_replay(self, command: Any) -> RuntimeCommandResult:
        """Re-issue an identical command object to assert idempotent replay."""
        actor = self.service.authenticate_actor(self.actor_id)
        return self.service.handle_authorized(command, actor)

    # -- command dispatch --------------------------------------------------
    def _dispatch(self, command: Any, operation: str) -> AdapterOutcome:
        self.issued_command_ids.append(str(command.command_id))
        actor = self.service.authenticate_actor(self.actor_id)
        try:
            result = self.service.handle_authorized(command, actor)
        except Exception as exc:
            raise map_runtime_error(exc, operation) from exc
        return AdapterOutcome(
            committed=not result.idempotent_replay,
            snapshot=result.snapshot,
            idempotent_replay=result.idempotent_replay,
            recovery_plan=result.recovery_plan,
        )

    def _skip_if_processed(self, run_id: str, command_id: str) -> AdapterOutcome | None:
        if not self.is_processed(run_id, command_id):
            return None
        self.issued_command_ids.append(command_id)
        snapshot = self.load_run(run_id)
        assert snapshot is not None
        return AdapterOutcome(committed=False, snapshot=snapshot, idempotent_replay=True)

    # -- run lifecycle -----------------------------------------------------
    def build_specification(
        self,
        *,
        run_id: str,
        task_id: str,
        agent_id: str,
        operation: str,
        capability: str,
        created_at: datetime,
        max_attempts: int,
    ) -> AgentRunSpecification:
        return AgentRunSpecification(
            run_id=run_id,
            task_id=task_id,
            agent_id=agent_id,
            requested_operation=operation,
            created_at=created_at,
            deadline=created_at + timedelta(hours=1),
            correlation_id=f"autonomy-{task_id}",
            causation_id=f"autonomy-{run_id}",
            idempotency_key=f"autonomy-idem-{run_id}",
            maximum_permitted_attempts=max_attempts,
            metadata={"scope": "autonomy-acceptance"},
            requested_capabilities=(capability,),
        )

    def create_run(
        self, specification: AgentRunSpecification, *, command_id: str, timestamp: datetime
    ) -> AdapterOutcome:
        if (skipped := self._skip_if_processed(specification.run_id, command_id)) is not None:
            return skipped
        return self._dispatch(
            CreateAgentRunCommand(
                specification=specification,
                command_id=command_id,
                expected_run_version=0,
                timestamp=timestamp,
                actor_reference=self.actor_id,
                source_metadata={"source": "autonomy-harness"},
            ),
            "create_run",
        )

    def queue_run(
        self, run_id: str, *, command_id: str, expected_version: int, timestamp: datetime
    ) -> AdapterOutcome:
        if (skipped := self._skip_if_processed(run_id, command_id)) is not None:
            return skipped
        return self._dispatch(
            QueueAgentRunCommand(
                run_id=run_id,
                command_id=command_id,
                expected_run_version=expected_version,
                timestamp=timestamp,
                actor_reference=self.actor_id,
                source_metadata={"source": "autonomy-harness"},
            ),
            "queue_run",
        )

    def claim_run(
        self, run_id: str, *, command_id: str, expected_version: int, timestamp: datetime
    ) -> AdapterOutcome:
        if (skipped := self._skip_if_processed(run_id, command_id)) is not None:
            return skipped
        return self._dispatch(
            ClaimAgentRunCommand(
                run_id=run_id,
                command_id=command_id,
                expected_run_version=expected_version,
                timestamp=timestamp,
                actor_reference=self.actor_id,
                executor_reference=f"autonomy-executor:{run_id}",
                source_metadata={"source": "autonomy-harness"},
            ),
            "claim_run",
        )

    def begin_attempt(
        self,
        run_id: str,
        *,
        command_id: str,
        expected_version: int,
        timestamp: datetime,
        resume_from_checkpoint_id: str | None = None,
    ) -> AdapterOutcome:
        if (skipped := self._skip_if_processed(run_id, command_id)) is not None:
            return skipped
        return self._dispatch(
            BeginAttemptCommand(
                run_id=run_id,
                command_id=command_id,
                expected_run_version=expected_version,
                timestamp=timestamp,
                actor_reference=self.actor_id,
                executor_reference=f"autonomy-executor:{run_id}",
                resume_from_checkpoint_id=resume_from_checkpoint_id,
                source_metadata={"source": "autonomy-harness"},
            ),
            "begin_attempt",
        )

    def start_attempt(
        self, run_id: str, *, command_id: str, expected_version: int, timestamp: datetime
    ) -> AdapterOutcome:
        if (skipped := self._skip_if_processed(run_id, command_id)) is not None:
            return skipped
        return self._dispatch(
            StartAttemptCommand(
                run_id=run_id,
                command_id=command_id,
                expected_run_version=expected_version,
                timestamp=timestamp,
                actor_reference=self.actor_id,
                source_metadata={"source": "autonomy-harness"},
            ),
            "start_attempt",
        )

    def record_validated_checkpoint(
        self,
        run_id: str,
        *,
        command_id: str,
        checkpoint_id: str,
        expected_version: int,
        timestamp: datetime,
        payload: ValidatedCheckpointPayload,
    ) -> AdapterOutcome:
        if (skipped := self._skip_if_processed(run_id, command_id)) is not None:
            return skipped
        return self._dispatch(
            RecordCheckpointCommand(
                run_id=run_id,
                command_id=command_id,
                expected_run_version=expected_version,
                timestamp=timestamp,
                actor_reference=self.actor_id,
                checkpoint_id=checkpoint_id,
                state_reference=f"node:{payload.node_id}:attempt:{payload.attempt_number}",
                integrity_digest=payload.output_digest,
                resume_cursor=f"{payload.node_id}:{payload.attempt_number}",
                checkpoint_metadata={
                    "nodeId": payload.node_id,
                    "attemptNumber": payload.attempt_number,
                    "outputDigest": payload.output_digest,
                    "summaryPreview": payload.summary[:500],
                    "schemaName": payload.schema_name,
                },
                source_metadata={"source": "autonomy-harness"},
            ),
            "record_checkpoint",
        )

    def unblock_run(
        self, run_id: str, *, command_id: str, expected_version: int, timestamp: datetime
    ) -> AdapterOutcome:
        if (skipped := self._skip_if_processed(run_id, command_id)) is not None:
            return skipped
        return self._dispatch(
            UnblockAgentRunCommand(
                run_id=run_id,
                command_id=command_id,
                expected_run_version=expected_version,
                timestamp=timestamp,
                actor_reference=self.actor_id,
                detail="Autonomy recovery unblock after approved plan",
                source_metadata={"source": "autonomy-harness"},
            ),
            "unblock_run",
        )

    def complete_attempt(
        self, run_id: str, *, command_id: str, expected_version: int, timestamp: datetime
    ) -> AdapterOutcome:
        if (skipped := self._skip_if_processed(run_id, command_id)) is not None:
            return skipped
        return self._dispatch(
            CompleteAttemptCommand(
                run_id=run_id,
                command_id=command_id,
                expected_run_version=expected_version,
                timestamp=timestamp,
                actor_reference=self.actor_id,
                source_metadata={"source": "autonomy-harness"},
            ),
            "complete_attempt",
        )

    def fail_attempt(
        self,
        run_id: str,
        *,
        command_id: str,
        expected_version: int,
        timestamp: datetime,
        category: FailureClassification,
        detail: str,
    ) -> AdapterOutcome:
        if (skipped := self._skip_if_processed(run_id, command_id)) is not None:
            return skipped
        return self._dispatch(
            FailAttemptCommand(
                run_id=run_id,
                command_id=command_id,
                expected_run_version=expected_version,
                timestamp=timestamp,
                actor_reference=self.actor_id,
                failure_category=category,
                failure_detail=detail[:500],
                source_metadata={"source": "autonomy-harness"},
            ),
            "fail_attempt",
        )

    def request_recovery_plan(
        self, run_id: str, *, command_id: str, expected_version: int, timestamp: datetime
    ) -> AdapterOutcome:
        if (skipped := self._skip_if_processed(run_id, command_id)) is not None:
            return skipped
        return self._dispatch(
            RequestRecoveryPlanCommand(
                run_id=run_id,
                command_id=command_id,
                expected_run_version=expected_version,
                timestamp=timestamp,
                actor_reference=self.actor_id,
                source_metadata={"source": "autonomy-harness"},
            ),
            "request_recovery_plan",
        )

    def complete_run(
        self,
        run_id: str,
        *,
        command_id: str,
        expected_version: int,
        timestamp: datetime,
        detail: str = "Autonomy node completed",
    ) -> AdapterOutcome:
        if (skipped := self._skip_if_processed(run_id, command_id)) is not None:
            return skipped
        return self._dispatch(
            CompleteAgentRunCommand(
                run_id=run_id,
                command_id=command_id,
                expected_run_version=expected_version,
                timestamp=timestamp,
                actor_reference=self.actor_id,
                detail=detail[:500],
                source_metadata={"source": "autonomy-harness"},
            ),
            "complete_run",
        )

    def fail_run(
        self,
        run_id: str,
        *,
        command_id: str,
        expected_version: int,
        timestamp: datetime,
        category: FailureClassification,
        detail: str,
    ) -> AdapterOutcome:
        if (skipped := self._skip_if_processed(run_id, command_id)) is not None:
            return skipped
        return self._dispatch(
            FailAgentRunCommand(
                run_id=run_id,
                command_id=command_id,
                expected_run_version=expected_version,
                timestamp=timestamp,
                actor_reference=self.actor_id,
                failure_category=category,
                failure_detail=detail[:500],
                source_metadata={"source": "autonomy-harness"},
            ),
            "fail_run",
        )

    def request_cancellation(
        self, run_id: str, *, command_id: str, expected_version: int, timestamp: datetime
    ) -> AdapterOutcome:
        if (skipped := self._skip_if_processed(run_id, command_id)) is not None:
            return skipped
        return self._dispatch(
            RequestCancellationCommand(
                run_id=run_id,
                command_id=command_id,
                expected_run_version=expected_version,
                timestamp=timestamp,
                actor_reference=self.actor_id,
                reason_code="operator_cancellation",
                detail="Autonomy objective cancelled by operator gate",
                requester_reference=self.actor_id,
                source_metadata={"source": "autonomy-harness"},
            ),
            "request_cancellation",
        )

    def confirm_cancellation_start(
        self, run_id: str, *, command_id: str, expected_version: int, timestamp: datetime
    ) -> AdapterOutcome:
        if (skipped := self._skip_if_processed(run_id, command_id)) is not None:
            return skipped
        return self._dispatch(
            ConfirmCancellationStartCommand(
                run_id=run_id,
                command_id=command_id,
                expected_run_version=expected_version,
                timestamp=timestamp,
                actor_reference=self.actor_id,
                source_metadata={"source": "autonomy-harness"},
            ),
            "confirm_cancellation_start",
        )

    def confirm_cancellation(
        self, run_id: str, *, command_id: str, expected_version: int, timestamp: datetime
    ) -> AdapterOutcome:
        if (skipped := self._skip_if_processed(run_id, command_id)) is not None:
            return skipped
        return self._dispatch(
            ConfirmCancellationCommand(
                run_id=run_id,
                command_id=command_id,
                expected_run_version=expected_version,
                timestamp=timestamp,
                actor_reference=self.actor_id,
                source_metadata={"source": "autonomy-harness"},
            ),
            "confirm_cancellation",
        )

    @staticmethod
    def classify_category(name: str) -> FailureClassification:
        try:
            return FailureClassification(name)
        except ValueError:
            return FailureClassification.EXECUTION
