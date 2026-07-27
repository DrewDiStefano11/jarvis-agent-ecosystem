from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.agent_runtime.errors import (
    ActiveAttemptExistsError,
    AttemptLimitExceededError,
    AttemptNotFoundError,
    CheckpointLineageError,
    CheckpointNotAllowedError,
    CheckpointSequenceConflictError,
    CommandConflictError,
    InvalidAttemptStateError,
    InvalidRuntimeMetadataError,
    InvalidTransitionError,
    RunAlreadyExistsError,
    RunNotFoundError,
    TerminalRunImmutableError,
    VersionConflictError,
)
from app.agent_runtime.ledger import RuntimeAggregate, replay_execution_ledger
from app.agent_runtime.recovery import plan_recovery
from app.agent_runtime.repository import AgentRuntimeRepository
from app.agent_runtime.transitions import TERMINAL_STATES
from app.models.agent_runtime import (
    DEFAULT_LINEAGE_DEPTH_LIMIT,
    AbandonAgentRunCommand,
    AbandonAttemptCommand,
    AgentRunAttempt,
    AgentRunCheckpoint,
    AgentRunSnapshot,
    AgentRunSpecification,
    AgentRunState,
    AgentRuntimeEventType,
    AttemptState,
    BeginAttemptCommand,
    BlockAgentRunCommand,
    BlockingReason,
    CancellationRecord,
    ClaimAgentRunCommand,
    CommandId,
    CompleteAgentRunCommand,
    CompleteAttemptCommand,
    ConfirmCancellationCommand,
    ConfirmCancellationStartCommand,
    ConfirmPauseCommand,
    CreateAgentRunCommand,
    FailAgentRunCommand,
    FailAttemptCommand,
    FailureClassification,
    FailureRecord,
    HeartbeatCommand,
    LineageEntry,
    LineageResolution,
    PauseReason,
    ProcessedCommandRecord,
    QueueAgentRunCommand,
    RecordCheckpointCommand,
    RequestCancellationCommand,
    RequestPauseCommand,
    RequestRecoveryPlanCommand,
    ResumeAgentRunCommand,
    RuntimeCommand,
    RuntimeCommandResult,
    RuntimeEventEnvelope,
    StartAttemptCommand,
    TimeoutAgentRunCommand,
    TimeoutAttemptCommand,
    UnblockAgentRunCommand,
    stable_hash,
)

CommandHandler = (
    CreateAgentRunCommand
    | QueueAgentRunCommand
    | ClaimAgentRunCommand
    | BeginAttemptCommand
    | StartAttemptCommand
    | HeartbeatCommand
    | RequestPauseCommand
    | ConfirmPauseCommand
    | ResumeAgentRunCommand
    | BlockAgentRunCommand
    | UnblockAgentRunCommand
    | RequestCancellationCommand
    | ConfirmCancellationStartCommand
    | ConfirmCancellationCommand
    | RecordCheckpointCommand
    | CompleteAttemptCommand
    | FailAttemptCommand
    | TimeoutAttemptCommand
    | AbandonAttemptCommand
    | CompleteAgentRunCommand
    | FailAgentRunCommand
    | TimeoutAgentRunCommand
    | AbandonAgentRunCommand
    | RequestRecoveryPlanCommand
)


def default_utc_clock() -> datetime:
    return datetime.now(UTC)


def prefixed_identifier_factory(prefix: str) -> Callable[[], str]:
    return lambda: f"{prefix}-{uuid4().hex}"


class AgentRuntimeService:
    """Central command service for the isolated execution-ledger domain."""

    def __init__(
        self,
        repository: AgentRuntimeRepository,
        *,
        utc_clock: Callable[[], datetime] = default_utc_clock,
        run_id_factory: Callable[[], str] | None = None,
        attempt_id_factory: Callable[[], str] | None = None,
        event_id_factory: Callable[[], str] | None = None,
        checkpoint_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.repository = repository
        self.utc_clock = utc_clock
        self.run_id_factory = run_id_factory or prefixed_identifier_factory("run")
        self.attempt_id_factory = attempt_id_factory or prefixed_identifier_factory("attempt")
        self.event_id_factory = event_id_factory or prefixed_identifier_factory("event")
        self.checkpoint_id_factory = checkpoint_id_factory or prefixed_identifier_factory(
            "checkpoint"
        )

    def generate_run_id(self) -> str:
        return self.run_id_factory()

    def handle(self, command: CommandHandler) -> RuntimeCommandResult:
        if isinstance(command, CreateAgentRunCommand):
            return self.create_run(command)
        if isinstance(command, QueueAgentRunCommand):
            return self.queue_run(command)
        if isinstance(command, ClaimAgentRunCommand):
            return self.claim_run(command)
        if isinstance(command, BeginAttemptCommand):
            return self.begin_attempt(command)
        if isinstance(command, StartAttemptCommand):
            return self.start_attempt(command)
        if isinstance(command, HeartbeatCommand):
            return self.record_heartbeat(command)
        if isinstance(command, RequestPauseCommand):
            return self.request_pause(command)
        if isinstance(command, ConfirmPauseCommand):
            return self.confirm_pause(command)
        if isinstance(command, ResumeAgentRunCommand):
            return self.resume_run(command)
        if isinstance(command, BlockAgentRunCommand):
            return self.block_run(command)
        if isinstance(command, UnblockAgentRunCommand):
            return self.unblock_run(command)
        if isinstance(command, RequestCancellationCommand):
            return self.request_cancellation(command)
        if isinstance(command, ConfirmCancellationStartCommand):
            return self.confirm_cancellation_start(command)
        if isinstance(command, ConfirmCancellationCommand):
            return self.confirm_cancellation(command)
        if isinstance(command, RecordCheckpointCommand):
            return self.record_checkpoint(command)
        if isinstance(command, CompleteAttemptCommand):
            return self.complete_attempt(command)
        if isinstance(command, FailAttemptCommand):
            return self.fail_attempt(command)
        if isinstance(command, TimeoutAttemptCommand):
            return self.timeout_attempt(command)
        if isinstance(command, AbandonAttemptCommand):
            return self.abandon_attempt(command)
        if isinstance(command, CompleteAgentRunCommand):
            return self.complete_run(command)
        if isinstance(command, FailAgentRunCommand):
            return self.fail_run(command)
        if isinstance(command, TimeoutAgentRunCommand):
            return self.timeout_run(command)
        if isinstance(command, AbandonAgentRunCommand):
            return self.abandon_run(command)
        if isinstance(command, RequestRecoveryPlanCommand):
            return self.request_recovery_plan(command)
        raise TypeError(f"Unsupported command type: {type(command)!r}")

    def create_run(self, command: CreateAgentRunCommand) -> RuntimeCommandResult:
        existing = self.repository.load_run(command.specification.run_id)
        if existing_record := self.repository.get_processed_command(
            command.specification.run_id,
            command.command_id,
        ):
            command_hash = stable_hash(command.model_dump(mode="json", exclude_none=False))
            if existing_record.command_hash != command_hash:
                raise CommandConflictError(
                    run_id=command.specification.run_id,
                    command_id=command.command_id,
                )
            return existing_record.result.model_copy(update={"idempotent_replay": True}, deep=True)
        if existing is not None:
            raise RunAlreadyExistsError(
                run_id=command.specification.run_id,
                command_id=command.command_id,
            )
        if command.expected_run_version != 0:
            raise VersionConflictError(
                run_id=command.specification.run_id,
                command_id=command.command_id,
                metadata={"expectedVersion": command.expected_run_version, "storedVersion": None},
            )
        self._validate_parent_lineage(command.specification)
        event = self._build_event(
            command=command,
            run_id=command.specification.run_id,
            event_type=AgentRuntimeEventType.RUN_CREATED,
            sequence_number=1,
            run_version=1,
            payload={
                "specification": command.specification.model_dump(mode="json"),
                "detail": "Run created",
            },
        )
        aggregate = replay_execution_ledger([event])
        assert aggregate is not None
        result = RuntimeCommandResult(
            run_id=command.specification.run_id,
            snapshot=aggregate.snapshot,
            events=(event,),
        )
        record = self._processed_record(
            command.specification.run_id, command.command_id, command, result
        )
        self.repository.commit_command(
            snapshot=aggregate.snapshot,
            events=(event,),
            processed_command=record,
            expected_version=0,
            expected_sequence=0,
            create=True,
        )
        return result

    def queue_run(self, command: QueueAgentRunCommand) -> RuntimeCommandResult:
        aggregate = self._load_current(command.run_id)
        if existing := self._assert_idempotency(command.run_id, command.command_id, command):
            return existing
        snapshot = aggregate.snapshot
        self._ensure_expected_version(snapshot, command.expected_run_version, command)
        self._assert_not_terminal(snapshot, command)
        event = self._build_event(
            command=command,
            run_id=command.run_id,
            event_type=AgentRuntimeEventType.RUN_QUEUED,
            sequence_number=snapshot.event_sequence_number + 1,
            run_version=snapshot.version + 1,
            payload={"detail": command.detail},
        )
        return self._commit(command, aggregate, (event,))

    def claim_run(self, command: ClaimAgentRunCommand) -> RuntimeCommandResult:
        aggregate = self._load_current(command.run_id)
        if existing := self._assert_idempotency(command.run_id, command.command_id, command):
            return existing
        snapshot = aggregate.snapshot
        self._ensure_expected_version(snapshot, command.expected_run_version, command)
        self._assert_not_terminal(snapshot, command)
        event = self._build_event(
            command=command,
            run_id=command.run_id,
            event_type=AgentRuntimeEventType.RUN_CLAIMED,
            sequence_number=snapshot.event_sequence_number + 1,
            run_version=snapshot.version + 1,
            payload={"executor_reference": command.executor_reference, "detail": command.detail},
        )
        return self._commit(command, aggregate, (event,))

    def begin_attempt(self, command: BeginAttemptCommand) -> RuntimeCommandResult:
        aggregate = self._load_current(command.run_id)
        if existing := self._assert_idempotency(command.run_id, command.command_id, command):
            return existing
        snapshot = aggregate.snapshot
        self._ensure_expected_version(snapshot, command.expected_run_version, command)
        self._assert_not_terminal(snapshot, command)
        if snapshot.active_attempt_id is not None:
            raise ActiveAttemptExistsError(
                run_id=command.run_id,
                attempt_id=snapshot.active_attempt_id,
                command_id=command.command_id,
            )
        if aggregate.attempts and aggregate.attempts[-1].state not in {
            AttemptState.CANCELLED,
            AttemptState.SUCCEEDED,
            AttemptState.FAILED,
            AttemptState.TIMED_OUT,
            AttemptState.ABANDONED,
        }:
            raise InvalidAttemptStateError(
                "A prior attempt must be terminal before another attempt can start.",
                run_id=command.run_id,
                attempt_id=aggregate.attempts[-1].attempt_id,
                command_id=command.command_id,
            )
        if len(aggregate.attempts) >= snapshot.specification.maximum_permitted_attempts:
            raise AttemptLimitExceededError(
                run_id=command.run_id,
                command_id=command.command_id,
                metadata={
                    "attemptCount": len(aggregate.attempts),
                    "maximumAttempts": snapshot.specification.maximum_permitted_attempts,
                },
            )
        if command.resume_from_checkpoint_id is not None:
            checkpoint = self._find_checkpoint(
                aggregate, command.resume_from_checkpoint_id, command
            )
            if checkpoint.run_id != command.run_id:
                raise CheckpointLineageError(
                    "The resume checkpoint belongs to another run.",
                    run_id=command.run_id,
                    command_id=command.command_id,
                    metadata={"checkpointId": command.resume_from_checkpoint_id},
                )
        attempt_id = command.attempt_id or self.attempt_id_factory()
        next_number = len(aggregate.attempts) + 1
        attempt = AgentRunAttempt(
            attempt_id=attempt_id,
            run_id=command.run_id,
            attempt_number=next_number,
            state=AttemptState.STARTING,
            started_at=command.timestamp,
            executor_reference=command.executor_reference,
            resumed_from_checkpoint_id=command.resume_from_checkpoint_id,
            version=snapshot.version + 2,
        )
        first_event = self._build_event(
            command=command,
            run_id=command.run_id,
            event_type=AgentRuntimeEventType.RUN_START_REQUESTED,
            sequence_number=snapshot.event_sequence_number + 1,
            run_version=snapshot.version + 1,
            payload={
                "executor_reference": command.executor_reference,
                "resume_from_checkpoint_id": command.resume_from_checkpoint_id,
                "detail": command.detail,
            },
        )
        second_event = self._build_event(
            command=command,
            run_id=command.run_id,
            attempt_id=attempt_id,
            event_type=AgentRuntimeEventType.ATTEMPT_CREATED,
            sequence_number=snapshot.event_sequence_number + 2,
            run_version=snapshot.version + 2,
            payload={"attempt": attempt.model_dump(mode="json")},
        )
        return self._commit(command, aggregate, (first_event, second_event))

    def start_attempt(self, command: StartAttemptCommand) -> RuntimeCommandResult:
        aggregate = self._load_current(command.run_id)
        if existing := self._assert_idempotency(command.run_id, command.command_id, command):
            return existing
        snapshot = aggregate.snapshot
        self._ensure_expected_version(snapshot, command.expected_run_version, command)
        if snapshot.active_attempt_id is None and command.attempt_id is not None:
            historical_attempt = next(
                (item for item in aggregate.attempts if item.attempt_id == command.attempt_id),
                None,
            )
            if historical_attempt is not None:
                raise InvalidAttemptStateError(
                    "Terminal attempts cannot become active again.",
                    run_id=command.run_id,
                    attempt_id=historical_attempt.attempt_id,
                    command_id=command.command_id,
                )
        attempt = self._require_active_attempt(aggregate, command.attempt_id, command)
        if attempt.state != AttemptState.STARTING:
            raise InvalidAttemptStateError(
                "Only a starting attempt may transition to running.",
                run_id=command.run_id,
                attempt_id=attempt.attempt_id,
                command_id=command.command_id,
            )
        event = self._build_event(
            command=command,
            run_id=command.run_id,
            attempt_id=attempt.attempt_id,
            event_type=AgentRuntimeEventType.ATTEMPT_STARTED,
            sequence_number=snapshot.event_sequence_number + 1,
            run_version=snapshot.version + 1,
            payload={"detail": command.detail},
        )
        return self._commit(command, aggregate, (event,))

    def record_heartbeat(self, command: HeartbeatCommand) -> RuntimeCommandResult:
        aggregate = self._load_current(command.run_id)
        if existing := self._assert_idempotency(command.run_id, command.command_id, command):
            return existing
        snapshot = aggregate.snapshot
        self._ensure_expected_version(snapshot, command.expected_run_version, command)
        attempt = self._require_active_attempt(aggregate, command.attempt_id, command)
        if aggregate.snapshot.state not in {AgentRunState.RUNNING, AgentRunState.CANCELLING}:
            raise InvalidTransitionError(
                "Heartbeats are only valid while a run is running or cancelling.",
                run_id=command.run_id,
                attempt_id=attempt.attempt_id,
                command_id=command.command_id,
                metadata={
                    "sourceState": aggregate.snapshot.state,
                    "targetState": aggregate.snapshot.state,
                },
            )
        event = self._build_event(
            command=command,
            run_id=command.run_id,
            attempt_id=attempt.attempt_id,
            event_type=AgentRuntimeEventType.HEARTBEAT_RECORDED,
            sequence_number=snapshot.event_sequence_number + 1,
            run_version=snapshot.version + 1,
            payload={"detail": command.detail},
        )
        return self._commit(command, aggregate, (event,))

    def request_pause(self, command: RequestPauseCommand) -> RuntimeCommandResult:
        aggregate = self._load_current(command.run_id)
        if existing := self._assert_idempotency(command.run_id, command.command_id, command):
            return existing
        snapshot = aggregate.snapshot
        self._ensure_expected_version(snapshot, command.expected_run_version, command)
        self._assert_not_terminal(snapshot, command)
        resume_state = snapshot.state
        pause = PauseReason(
            code=command.reason_code,
            detail=command.detail,
            timestamp=command.timestamp,
            requested_by=command.actor_reference,
            resume_state=resume_state,
            metadata=command.source_metadata,
        )
        event = self._build_event(
            command=command,
            run_id=command.run_id,
            event_type=AgentRuntimeEventType.PAUSE_REQUESTED,
            sequence_number=snapshot.event_sequence_number + 1,
            run_version=snapshot.version + 1,
            payload={"pause": pause.model_dump(mode="json")},
        )
        return self._commit(command, aggregate, (event,))

    def confirm_pause(self, command: ConfirmPauseCommand) -> RuntimeCommandResult:
        aggregate = self._load_current(command.run_id)
        if existing := self._assert_idempotency(command.run_id, command.command_id, command):
            return existing
        snapshot = aggregate.snapshot
        self._ensure_expected_version(snapshot, command.expected_run_version, command)
        event = self._build_event(
            command=command,
            run_id=command.run_id,
            event_type=AgentRuntimeEventType.RUN_PAUSED,
            sequence_number=snapshot.event_sequence_number + 1,
            run_version=snapshot.version + 1,
            payload={"detail": command.detail},
        )
        return self._commit(command, aggregate, (event,))

    def resume_run(self, command: ResumeAgentRunCommand) -> RuntimeCommandResult:
        aggregate = self._load_current(command.run_id)
        if existing := self._assert_idempotency(command.run_id, command.command_id, command):
            return existing
        snapshot = aggregate.snapshot
        self._ensure_expected_version(snapshot, command.expected_run_version, command)
        if snapshot.pause_reason is None:
            raise InvalidTransitionError(
                "Paused runs require a stored pause reason to resume.",
                run_id=command.run_id,
                command_id=command.command_id,
                metadata={"sourceState": snapshot.state, "targetState": None},
            )
        target_state = snapshot.pause_reason.resume_state
        if target_state == AgentRunState.STARTING:
            raise InvalidTransitionError(
                "Runs may not resume directly to starting.",
                run_id=command.run_id,
                command_id=command.command_id,
                metadata={"sourceState": snapshot.state, "targetState": target_state},
            )
        event = self._build_event(
            command=command,
            run_id=command.run_id,
            attempt_id=snapshot.active_attempt_id,
            event_type=AgentRuntimeEventType.RUN_RESUMED,
            sequence_number=snapshot.event_sequence_number + 1,
            run_version=snapshot.version + 1,
            payload={"target_state": target_state, "detail": command.detail},
        )
        return self._commit(command, aggregate, (event,))

    def block_run(self, command: BlockAgentRunCommand) -> RuntimeCommandResult:
        aggregate = self._load_current(command.run_id)
        if existing := self._assert_idempotency(command.run_id, command.command_id, command):
            return existing
        snapshot = aggregate.snapshot
        self._ensure_expected_version(snapshot, command.expected_run_version, command)
        self._assert_not_terminal(snapshot, command)
        block = BlockingReason(
            code=command.block_code,
            detail=command.detail,
            timestamp=command.timestamp,
            related_reference=command.related_reference,
            resume_state=snapshot.state,
            metadata=command.source_metadata,
        )
        event = self._build_event(
            command=command,
            run_id=command.run_id,
            attempt_id=snapshot.active_attempt_id,
            event_type=AgentRuntimeEventType.RUN_BLOCKED,
            sequence_number=snapshot.event_sequence_number + 1,
            run_version=snapshot.version + 1,
            payload={"block": block.model_dump(mode="json")},
        )
        return self._commit(command, aggregate, (event,))

    def unblock_run(self, command: UnblockAgentRunCommand) -> RuntimeCommandResult:
        aggregate = self._load_current(command.run_id)
        if existing := self._assert_idempotency(command.run_id, command.command_id, command):
            return existing
        snapshot = aggregate.snapshot
        self._ensure_expected_version(snapshot, command.expected_run_version, command)
        if snapshot.blocking_reason is None:
            raise InvalidTransitionError(
                "Unblocking requires an active block.",
                run_id=command.run_id,
                command_id=command.command_id,
                metadata={"sourceState": snapshot.state, "targetState": None},
            )
        target_state = snapshot.blocking_reason.resume_state
        event = self._build_event(
            command=command,
            run_id=command.run_id,
            attempt_id=snapshot.active_attempt_id,
            event_type=AgentRuntimeEventType.RUN_UNBLOCKED,
            sequence_number=snapshot.event_sequence_number + 1,
            run_version=snapshot.version + 1,
            payload={"target_state": target_state, "detail": command.detail},
        )
        return self._commit(command, aggregate, (event,))

    def request_cancellation(self, command: RequestCancellationCommand) -> RuntimeCommandResult:
        aggregate = self._load_current(command.run_id)
        if existing := self._assert_idempotency(command.run_id, command.command_id, command):
            return existing
        snapshot = aggregate.snapshot
        self._ensure_expected_version(snapshot, command.expected_run_version, command)
        if snapshot.state in TERMINAL_STATES:
            raise TerminalRunImmutableError(
                "Cancellation requests are rejected for terminal runs.",
                run_id=command.run_id,
                command_id=command.command_id,
                metadata={"state": snapshot.state},
            )
        if snapshot.cancellation is not None and snapshot.state in {
            AgentRunState.CANCEL_REQUESTED,
            AgentRunState.CANCELLING,
        }:
            raise CommandConflictError(
                "Cancellation has already begun and its reason cannot be replaced.",
                run_id=command.run_id,
                command_id=command.command_id,
                metadata={"existingReasonCode": snapshot.cancellation.reason_code},
            )
        cancellation = CancellationRecord(
            reason_code=command.reason_code,
            detail=command.detail,
            requester_reference=command.requester_reference,
            timestamp=command.timestamp,
            metadata=command.source_metadata,
        )
        cancellation_event = self._build_event(
            command=command,
            run_id=command.run_id,
            attempt_id=snapshot.active_attempt_id,
            event_type=AgentRuntimeEventType.CANCELLATION_REQUESTED,
            sequence_number=snapshot.event_sequence_number + 1,
            run_version=snapshot.version + 1,
            payload={"cancellation": cancellation.model_dump(mode="json")},
        )
        if snapshot.active_attempt_id is None:
            cancelled_event = self._build_event(
                command=command,
                run_id=command.run_id,
                event_type=AgentRuntimeEventType.RUN_CANCELLED,
                sequence_number=snapshot.event_sequence_number + 2,
                run_version=snapshot.version + 2,
                payload={"detail": "Run cancelled before execution started"},
            )
            return self._commit(command, aggregate, (cancellation_event, cancelled_event))
        return self._commit(command, aggregate, (cancellation_event,))

    def confirm_cancellation_start(
        self,
        command: ConfirmCancellationStartCommand,
    ) -> RuntimeCommandResult:
        aggregate = self._load_current(command.run_id)
        if existing := self._assert_idempotency(command.run_id, command.command_id, command):
            return existing
        snapshot = aggregate.snapshot
        self._ensure_expected_version(snapshot, command.expected_run_version, command)
        attempt = self._require_active_attempt(aggregate, None, command)
        event = self._build_event(
            command=command,
            run_id=command.run_id,
            attempt_id=attempt.attempt_id,
            event_type=AgentRuntimeEventType.CANCELLATION_STARTED,
            sequence_number=snapshot.event_sequence_number + 1,
            run_version=snapshot.version + 1,
            payload={"detail": command.detail},
        )
        return self._commit(command, aggregate, (event,))

    def confirm_cancellation(self, command: ConfirmCancellationCommand) -> RuntimeCommandResult:
        aggregate = self._load_current(command.run_id)
        if existing := self._assert_idempotency(command.run_id, command.command_id, command):
            return existing
        snapshot = aggregate.snapshot
        self._ensure_expected_version(snapshot, command.expected_run_version, command)
        attempt = self._require_active_attempt(aggregate, None, command)
        event = self._build_event(
            command=command,
            run_id=command.run_id,
            attempt_id=attempt.attempt_id,
            event_type=AgentRuntimeEventType.RUN_CANCELLED,
            sequence_number=snapshot.event_sequence_number + 1,
            run_version=snapshot.version + 1,
            payload={"detail": command.detail},
        )
        return self._commit(command, aggregate, (event,))

    def record_checkpoint(self, command: RecordCheckpointCommand) -> RuntimeCommandResult:
        aggregate = self._load_current(command.run_id)
        if existing := self._assert_idempotency(command.run_id, command.command_id, command):
            return existing
        snapshot = aggregate.snapshot
        self._ensure_expected_version(snapshot, command.expected_run_version, command)
        if snapshot.state in TERMINAL_STATES:
            raise CheckpointNotAllowedError(
                "Terminal runs reject new checkpoints.",
                run_id=command.run_id,
                command_id=command.command_id,
                metadata={"state": snapshot.state},
            )
        if snapshot.state not in {
            AgentRunState.RUNNING,
            AgentRunState.PAUSED,
            AgentRunState.BLOCKED,
            AgentRunState.CANCELLING,
        }:
            raise CheckpointNotAllowedError(
                "Checkpoints are only allowed for active or interrupted attempts.",
                run_id=command.run_id,
                command_id=command.command_id,
                metadata={"state": snapshot.state},
            )
        attempt = self._require_active_attempt(aggregate, command.attempt_id, command)
        checkpoint_id = command.checkpoint_id or self.checkpoint_id_factory()
        existing_checkpoint = next(
            (item for item in aggregate.checkpoints if item.checkpoint_id == checkpoint_id),
            None,
        )
        if existing_checkpoint is not None:
            if self._checkpoint_matches_command(existing_checkpoint, command, attempt.attempt_id):
                result = RuntimeCommandResult(run_id=command.run_id, snapshot=snapshot, events=())
                record = self._processed_record(command.run_id, command.command_id, command, result)
                self.repository.commit_command(
                    snapshot=snapshot,
                    events=(),
                    processed_command=record,
                    expected_version=snapshot.version,
                    expected_sequence=snapshot.event_sequence_number,
                )
                return result
            raise CheckpointSequenceConflictError(
                "The checkpoint ID already exists with different contents or lineage.",
                run_id=command.run_id,
                attempt_id=attempt.attempt_id,
                command_id=command.command_id,
                metadata={
                    "checkpointId": checkpoint_id,
                    "existingAttemptId": existing_checkpoint.attempt_id,
                },
            )
        checkpoint = AgentRunCheckpoint(
            checkpoint_id=checkpoint_id,
            run_id=command.run_id,
            attempt_id=attempt.attempt_id,
            checkpoint_sequence=len(aggregate.checkpoints) + 1,
            run_version=snapshot.version + 1,
            event_sequence=snapshot.event_sequence_number + 1,
            timestamp=command.timestamp,
            state_reference=command.state_reference,
            integrity_digest=command.integrity_digest,
            resume_cursor=command.resume_cursor,
            metadata=command.checkpoint_metadata,
        )
        event = self._build_event(
            command=command,
            run_id=command.run_id,
            attempt_id=attempt.attempt_id,
            event_type=AgentRuntimeEventType.CHECKPOINT_RECORDED,
            sequence_number=snapshot.event_sequence_number + 1,
            run_version=snapshot.version + 1,
            payload={"checkpoint": checkpoint.model_dump(mode="json")},
        )
        return self._commit(command, aggregate, (event,))

    def complete_attempt(self, command: CompleteAttemptCommand) -> RuntimeCommandResult:
        return self._terminalize_attempt(
            command=command,
            event_type=AgentRuntimeEventType.ATTEMPT_SUCCEEDED,
            detail=command.detail,
        )

    def fail_attempt(self, command: FailAttemptCommand) -> RuntimeCommandResult:
        return self._terminalize_attempt(
            command=command,
            event_type=AgentRuntimeEventType.ATTEMPT_FAILED,
            detail=command.failure_detail,
            failure=FailureRecord(
                category=command.failure_category,
                detail=command.failure_detail,
                timestamp=command.timestamp,
                attempt_id=command.attempt_id,
            ),
        )

    def timeout_attempt(self, command: TimeoutAttemptCommand) -> RuntimeCommandResult:
        return self._terminalize_attempt(
            command=command,
            event_type=AgentRuntimeEventType.ATTEMPT_TIMED_OUT,
            detail=command.detail,
            failure=FailureRecord(
                category=FailureClassification.TIMEOUT,
                detail=command.detail,
                timestamp=command.timestamp,
                attempt_id=command.attempt_id,
            ),
        )

    def abandon_attempt(self, command: AbandonAttemptCommand) -> RuntimeCommandResult:
        return self._terminalize_attempt(
            command=command,
            event_type=AgentRuntimeEventType.ATTEMPT_ABANDONED,
            detail=command.detail,
            failure=FailureRecord(
                category=FailureClassification.INTERNAL,
                detail=command.detail,
                timestamp=command.timestamp,
                attempt_id=command.attempt_id,
            ),
        )

    def complete_run(self, command: CompleteAgentRunCommand) -> RuntimeCommandResult:
        aggregate = self._load_current(command.run_id)
        if existing := self._assert_idempotency(command.run_id, command.command_id, command):
            return existing
        snapshot = aggregate.snapshot
        self._ensure_expected_version(snapshot, command.expected_run_version, command)
        self._assert_not_terminal(snapshot, command)
        if not aggregate.attempts or aggregate.attempts[-1].state != AttemptState.SUCCEEDED:
            raise InvalidAttemptStateError(
                "Run completion requires a succeeded latest attempt.",
                run_id=command.run_id,
                command_id=command.command_id,
            )
        event = self._build_event(
            command=command,
            run_id=command.run_id,
            event_type=AgentRuntimeEventType.RUN_SUCCEEDED,
            sequence_number=snapshot.event_sequence_number + 1,
            run_version=snapshot.version + 1,
            payload={"detail": command.detail},
        )
        return self._commit(command, aggregate, (event,))

    def fail_run(self, command: FailAgentRunCommand) -> RuntimeCommandResult:
        failure = FailureRecord(
            category=command.failure_category,
            detail=command.failure_detail,
            timestamp=command.timestamp,
        )
        return self._terminalize_run(command, AgentRuntimeEventType.RUN_FAILED, failure)

    def timeout_run(self, command: TimeoutAgentRunCommand) -> RuntimeCommandResult:
        failure = FailureRecord(
            category=FailureClassification.TIMEOUT,
            detail=command.detail,
            timestamp=command.timestamp,
        )
        return self._terminalize_run(command, AgentRuntimeEventType.RUN_TIMED_OUT, failure)

    def abandon_run(self, command: AbandonAgentRunCommand) -> RuntimeCommandResult:
        failure = FailureRecord(
            category=FailureClassification.INTERNAL,
            detail=command.detail,
            timestamp=command.timestamp,
        )
        return self._terminalize_run(command, AgentRuntimeEventType.RUN_ABANDONED, failure)

    def request_recovery_plan(self, command: RequestRecoveryPlanCommand) -> RuntimeCommandResult:
        aggregate = self._load_current(command.run_id)
        if existing := self._assert_idempotency(command.run_id, command.command_id, command):
            return existing
        snapshot = aggregate.snapshot
        self._ensure_expected_version(snapshot, command.expected_run_version, command)
        recovery_plan = plan_recovery(
            snapshot,
            list(aggregate.attempts),
            list(aggregate.checkpoints),
            self.repository.list_events(command.run_id),
        )
        event = self._build_event(
            command=command,
            run_id=command.run_id,
            event_type=AgentRuntimeEventType.RECOVERY_PLANNED,
            sequence_number=snapshot.event_sequence_number + 1,
            run_version=snapshot.version + 1,
            payload={"plan": recovery_plan.model_dump(mode="json")},
        )
        updated_aggregate = replay_execution_ledger(
            self.repository.list_events(command.run_id) + [event]
        )
        assert updated_aggregate is not None
        result = RuntimeCommandResult(
            run_id=command.run_id,
            snapshot=updated_aggregate.snapshot,
            events=(event,),
            recovery_plan=recovery_plan,
        )
        record = self._processed_record(command.run_id, command.command_id, command, result)
        self.repository.commit_command(
            snapshot=updated_aggregate.snapshot,
            events=(event,),
            processed_command=record,
            expected_version=aggregate.snapshot.version,
            expected_sequence=aggregate.snapshot.event_sequence_number,
        )
        return result

    def resolve_lineage(
        self,
        run_id: str,
        *,
        depth_limit: int = DEFAULT_LINEAGE_DEPTH_LIMIT,
    ) -> LineageResolution:
        snapshot = self.repository.load_run(run_id)
        if snapshot is None:
            raise RunNotFoundError(run_id=run_id)
        entries: list[LineageEntry] = []
        missing_parent_id = None
        current_parent = snapshot.specification.parent_run_id
        visited = {run_id}
        depth = 0
        while current_parent is not None and depth < depth_limit:
            if current_parent in visited:
                raise InvalidRuntimeMetadataError(
                    "Run lineage contains a cycle.",
                    run_id=run_id,
                    metadata={"parentRunId": current_parent},
                )
            visited.add(current_parent)
            parent_snapshot = self.repository.load_run(current_parent)
            if parent_snapshot is None:
                missing_parent_id = current_parent
                entries.append(LineageEntry(run_id=current_parent, exists=False, state=None))
                break
            entries.append(
                LineageEntry(
                    run_id=parent_snapshot.specification.run_id,
                    exists=True,
                    state=parent_snapshot.state,
                )
            )
            current_parent = parent_snapshot.specification.parent_run_id
            depth += 1
        truncated = current_parent is not None and depth >= depth_limit
        return LineageResolution(
            run_id=run_id,
            entries=tuple(entries),
            missing_parent_id=missing_parent_id,
            truncated=truncated,
            depth_limit=depth_limit,
        )

    def _terminalize_attempt(
        self,
        *,
        command: CompleteAttemptCommand
        | FailAttemptCommand
        | TimeoutAttemptCommand
        | AbandonAttemptCommand,
        event_type: AgentRuntimeEventType,
        detail: str,
        failure: FailureRecord | None = None,
    ) -> RuntimeCommandResult:
        aggregate = self._load_current(command.run_id)
        if existing := self._assert_idempotency(command.run_id, command.command_id, command):
            return existing
        snapshot = aggregate.snapshot
        self._ensure_expected_version(snapshot, command.expected_run_version, command)
        attempt = self._require_active_attempt(
            aggregate, getattr(command, "attempt_id", None), command
        )
        if snapshot.state in {AgentRunState.CANCEL_REQUESTED, AgentRunState.CANCELLING}:
            raise InvalidTransitionError(
                "Attempt terminal events cannot override accepted cancellation.",
                run_id=command.run_id,
                attempt_id=attempt.attempt_id,
                command_id=command.command_id,
                metadata={"sourceState": snapshot.state, "targetState": None},
            )
        payload: dict[str, Any] = {"detail": detail}
        if failure is not None:
            block = BlockingReason(
                code="recovery_required",
                detail="Recovery planning is required before another attempt may begin.",
                timestamp=command.timestamp,
                resume_state=AgentRunState.CLAIMED,
            )
            payload = {
                "failure": failure.model_dump(mode="json"),
                "blocking_reason": block.model_dump(mode="json"),
            }
        event = self._build_event(
            command=command,
            run_id=command.run_id,
            attempt_id=attempt.attempt_id,
            event_type=event_type,
            sequence_number=snapshot.event_sequence_number + 1,
            run_version=snapshot.version + 1,
            payload=payload,
        )
        return self._commit(command, aggregate, (event,))

    def _terminalize_run(
        self,
        command: FailAgentRunCommand | TimeoutAgentRunCommand | AbandonAgentRunCommand,
        event_type: AgentRuntimeEventType,
        failure: FailureRecord,
    ) -> RuntimeCommandResult:
        aggregate = self._load_current(command.run_id)
        if existing := self._assert_idempotency(command.run_id, command.command_id, command):
            return existing
        snapshot = aggregate.snapshot
        self._ensure_expected_version(snapshot, command.expected_run_version, command)
        self._assert_not_terminal(snapshot, command)
        if snapshot.active_attempt_id is not None:
            raise InvalidAttemptStateError(
                "Terminal run events require the active attempt to be closed first.",
                run_id=command.run_id,
                attempt_id=snapshot.active_attempt_id,
                command_id=command.command_id,
            )
        event = self._build_event(
            command=command,
            run_id=command.run_id,
            event_type=event_type,
            sequence_number=snapshot.event_sequence_number + 1,
            run_version=snapshot.version + 1,
            payload={"failure": failure.model_dump(mode="json")},
        )
        return self._commit(command, aggregate, (event,))

    def _load_current(self, run_id: str) -> RuntimeAggregate:
        snapshot = self.repository.load_run(run_id)
        if snapshot is None:
            raise RunNotFoundError(run_id=run_id)
        events = self.repository.list_events(run_id)
        aggregate = replay_execution_ledger(events)
        if aggregate is None:
            raise RunNotFoundError(run_id=run_id)
        if aggregate.snapshot != snapshot:
            raise VersionConflictError(
                "Stored snapshot does not match the execution ledger.",
                run_id=run_id,
            )
        return aggregate

    def _commit(
        self,
        command: RuntimeCommand,
        aggregate: RuntimeAggregate,
        new_events: Sequence[RuntimeEventEnvelope],
    ) -> RuntimeCommandResult:
        existing_events = self.repository.list_events(command.run_id)
        updated_aggregate = replay_execution_ledger(existing_events + list(new_events))
        assert updated_aggregate is not None
        result = RuntimeCommandResult(
            run_id=command.run_id,
            snapshot=updated_aggregate.snapshot,
            events=tuple(new_events),
        )
        record = self._processed_record(command.run_id, command.command_id, command, result)
        self.repository.commit_command(
            snapshot=updated_aggregate.snapshot,
            events=new_events,
            processed_command=record,
            expected_version=aggregate.snapshot.version,
            expected_sequence=aggregate.snapshot.event_sequence_number,
        )
        return result

    def _processed_record(
        self,
        run_id: str,
        command_id: CommandId,
        command: Any,
        result: RuntimeCommandResult,
    ) -> ProcessedCommandRecord:
        command_hash = stable_hash(command.model_dump(mode="json", exclude_none=False))
        return ProcessedCommandRecord(
            run_id=run_id,
            command_id=command_id,
            command_hash=command_hash,
            result=result,
            recorded_at=getattr(command, "timestamp", self.utc_clock()),
        )

    def _assert_idempotency(
        self, run_id: str, command_id: CommandId, command: Any
    ) -> RuntimeCommandResult | None:
        existing = self.repository.get_processed_command(run_id, command_id)
        if existing is None:
            return None
        current_hash = stable_hash(command.model_dump(mode="json", exclude_none=False))
        if existing.command_hash != current_hash:
            raise CommandConflictError(run_id=run_id, command_id=command_id)
        return existing.result.model_copy(update={"idempotent_replay": True}, deep=True)

    def _ensure_expected_version(
        self,
        snapshot: AgentRunSnapshot,
        expected_version: int,
        command: Any,
    ) -> None:
        if snapshot.version != expected_version:
            raise VersionConflictError(
                run_id=snapshot.specification.run_id,
                command_id=getattr(command, "command_id", None),
                metadata={"expectedVersion": expected_version, "storedVersion": snapshot.version},
            )

    @staticmethod
    def _assert_not_terminal(snapshot: AgentRunSnapshot, command: Any) -> None:
        if snapshot.state in TERMINAL_STATES:
            raise TerminalRunImmutableError(
                run_id=snapshot.specification.run_id,
                command_id=getattr(command, "command_id", None),
                metadata={"state": snapshot.state},
            )

    @staticmethod
    def _require_active_attempt(
        aggregate: RuntimeAggregate,
        attempt_id: str | None,
        command: Any,
    ) -> AgentRunAttempt:
        active_attempt_id = aggregate.snapshot.active_attempt_id
        if active_attempt_id is None:
            raise AttemptNotFoundError(
                "The run does not have an active attempt.",
                run_id=aggregate.snapshot.specification.run_id,
                command_id=getattr(command, "command_id", None),
            )
        attempt = next(
            (item for item in aggregate.attempts if item.attempt_id == active_attempt_id), None
        )
        if attempt is None:
            raise AttemptNotFoundError(
                "The active attempt does not exist in history.",
                run_id=aggregate.snapshot.specification.run_id,
                attempt_id=active_attempt_id,
                command_id=getattr(command, "command_id", None),
            )
        if attempt_id is not None and attempt.attempt_id != attempt_id:
            raise AttemptNotFoundError(
                "The referenced attempt is not active.",
                run_id=aggregate.snapshot.specification.run_id,
                attempt_id=attempt_id,
                command_id=getattr(command, "command_id", None),
            )
        return attempt

    @staticmethod
    def _find_checkpoint(
        aggregate: RuntimeAggregate,
        checkpoint_id: str,
        command: Any,
    ) -> AgentRunCheckpoint:
        checkpoint = next(
            (item for item in aggregate.checkpoints if item.checkpoint_id == checkpoint_id),
            None,
        )
        if checkpoint is None:
            raise CheckpointLineageError(
                "The requested checkpoint does not exist for this run.",
                run_id=aggregate.snapshot.specification.run_id,
                command_id=getattr(command, "command_id", None),
                metadata={"checkpointId": checkpoint_id},
            )
        return checkpoint

    @staticmethod
    def _checkpoint_matches_command(
        checkpoint: AgentRunCheckpoint,
        command: RecordCheckpointCommand,
        attempt_id: str,
    ) -> bool:
        return (
            checkpoint.run_id == command.run_id
            and checkpoint.attempt_id == attempt_id
            and checkpoint.checkpoint_id == (command.checkpoint_id or checkpoint.checkpoint_id)
            and checkpoint.state_reference == command.state_reference
            and checkpoint.integrity_digest == command.integrity_digest
            and checkpoint.resume_cursor == command.resume_cursor
            and checkpoint.metadata == command.checkpoint_metadata
        )

    def _validate_parent_lineage(self, specification: AgentRunSpecification) -> None:
        parent_run_id = specification.parent_run_id
        if parent_run_id is None:
            return
        visited = {specification.run_id}
        current_parent = parent_run_id
        depth = 0
        while current_parent is not None and depth < DEFAULT_LINEAGE_DEPTH_LIMIT:
            if current_parent in visited:
                raise InvalidRuntimeMetadataError(
                    "Run lineage contains a cycle.",
                    run_id=specification.run_id,
                    metadata={"parentRunId": current_parent},
                )
            visited.add(current_parent)
            parent_snapshot = self.repository.load_run(current_parent)
            if parent_snapshot is None:
                return
            current_parent = parent_snapshot.specification.parent_run_id
            depth += 1
        if current_parent is not None:
            raise InvalidRuntimeMetadataError(
                "Run lineage exceeded the bounded traversal depth.",
                run_id=specification.run_id,
                metadata={"depthLimit": DEFAULT_LINEAGE_DEPTH_LIMIT},
            )

    def _build_event(
        self,
        *,
        command: Any,
        run_id: str,
        event_type: AgentRuntimeEventType,
        sequence_number: int,
        run_version: int,
        payload: dict[str, Any],
        attempt_id: str | None = None,
    ) -> RuntimeEventEnvelope:
        specification: AgentRunSpecification | None = None
        if isinstance(command, CreateAgentRunCommand):
            specification = command.specification
        else:
            snapshot = self.repository.load_run(run_id)
            if snapshot is not None:
                specification = snapshot.specification
        correlation_id = (
            None if specification is None else specification.correlation_id or specification.run_id
        )
        causation_id = (
            None if specification is None else specification.causation_id or command.command_id
        )
        return RuntimeEventEnvelope(
            event_id=self.event_id_factory(),
            event_type=event_type,
            run_id=run_id,
            attempt_id=attempt_id,
            sequence_number=sequence_number,
            run_version=run_version,
            timestamp=command.timestamp,
            actor_reference=getattr(command, "actor_reference", None),
            command_id=command.command_id,
            correlation_id=correlation_id,
            causation_id=causation_id,
            payload=payload,
            metadata=getattr(command, "source_metadata", {}),
        )
