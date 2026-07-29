from __future__ import annotations

from collections.abc import Callable, Sequence
from contextvars import ContextVar
from dataclasses import replace
from datetime import UTC, datetime
from time import sleep
from typing import Any
from uuid import uuid4

from app.agent_runtime.authorization import (
    RuntimeActorContext,
    RuntimeAuthorizationContext,
    RuntimeAuthorizer,
)
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
    RecoveryNotAllowedError,
    RunAlreadyExistsError,
    RunNotFoundError,
    RuntimeActorMismatchError,
    RuntimeReplayActorMismatchError,
    TerminalRunImmutableError,
    VersionConflictError,
)
from app.agent_runtime.ledger import RuntimeAggregate, apply_event, replay_execution_ledger
from app.agent_runtime.recovery import derive_recovery_plan
from app.agent_runtime.repository import AgentRuntimeRepository, validate_lineage_invariant
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
    RecoveryPlan,
    RecoveryStatus,
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
    build_run_created_payload,
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
        authorizer: RuntimeAuthorizer | None = None,
    ) -> None:
        self.repository = repository
        self.authorizer = authorizer
        self._authorization_context: ContextVar[RuntimeAuthorizationContext | None] = ContextVar(
            "runtime_authorization_context", default=None
        )
        self.utc_clock = utc_clock
        self.run_id_factory = run_id_factory or prefixed_identifier_factory("run")
        self.attempt_id_factory = attempt_id_factory or prefixed_identifier_factory("attempt")
        self.event_id_factory = event_id_factory or prefixed_identifier_factory("event")
        self.checkpoint_id_factory = checkpoint_id_factory or prefixed_identifier_factory(
            "checkpoint"
        )

    def generate_run_id(self) -> str:
        return self.run_id_factory()

    def authenticate_actor(self, actor_id: str | None) -> RuntimeActorContext:
        if self.authorizer is None:
            return RuntimeActorContext(
                actor_id=actor_id or "runtime-control-plane", stable_key="internal"
            )
        return self.authorizer.authenticate(actor_id)

    def handle_authorized(
        self, command: CommandHandler, actor: RuntimeActorContext
    ) -> RuntimeCommandResult:
        command = self._command_with_verified_actor(command, actor)
        context = self._authorize_command(command, actor)
        token = self._authorization_context.set(context)
        try:
            return self.handle(command)
        finally:
            self._authorization_context.reset(token)

    def read_run_authorized(self, run_id: str, actor: RuntimeActorContext) -> AgentRunSnapshot:
        snapshot = self.repository.load_run(run_id)
        if snapshot is None:
            raise RunNotFoundError(run_id=run_id)
        self._require_authorized(actor, "read", snapshot=snapshot)
        return snapshot

    def list_runs_authorized(self, query: Any, actor: RuntimeActorContext) -> Any:
        # Bounded deterministic filtering. We intentionally do not return the raw
        # unauthorized total count, and we scan at most five pages from the
        # requested offset to avoid unbounded in-memory filtering.
        items: list[AgentRunSnapshot] = []
        scan_offset = query.offset
        pages = 0
        next_offset: int | None = None
        exhausted = False
        while len(items) < query.limit and pages < 5:
            page_query = query.model_copy(update={"offset": scan_offset, "limit": query.limit})
            page = self.repository.query_runs(page_query)
            consumed_in_page = 0
            for snapshot in page.items:
                consumed_in_page += 1
                try:
                    self._require_authorized(actor, "read", snapshot=snapshot)
                except Exception:
                    continue
                items.append(snapshot)
                if len(items) == query.limit:
                    break
            pages += 1
            page_exhausted = page.next_offset is None
            if len(items) == query.limit:
                if consumed_in_page < len(page.items):
                    next_offset = scan_offset + consumed_in_page
                elif page_exhausted:
                    next_offset = None
                    exhausted = True
                else:
                    next_offset = page.next_offset
                break
            if page_exhausted:
                exhausted = True
                next_offset = None
                break
            scan_offset = page.next_offset
            next_offset = scan_offset
        if not exhausted and len(items) < query.limit and pages >= 5:
            next_offset = scan_offset if scan_offset > query.offset else None
        if next_offset is not None and next_offset <= query.offset:
            next_offset = None
        from app.models.agent_runtime import AgentRunQueryResult

        return AgentRunQueryResult(
            items=tuple(items),
            offset=query.offset,
            limit=query.limit,
            next_offset=next_offset,
            total_count=len(items),
        )

    def events_authorized(self, run_id: str, actor: RuntimeActorContext):
        self.read_run_authorized(run_id, actor)
        return self.repository.list_events(run_id)

    def attempts_authorized(self, run_id: str, actor: RuntimeActorContext):
        self.read_run_authorized(run_id, actor)
        return self.repository.load_attempt_history(run_id)

    def checkpoints_authorized(self, run_id: str, actor: RuntimeActorContext):
        self.read_run_authorized(run_id, actor)
        return self.repository.list_checkpoints(run_id)

    def lineage_authorized(self, run_id: str, actor: RuntimeActorContext) -> LineageResolution:
        snapshot = self.read_run_authorized(run_id, actor)
        return self._resolve_lineage_authorized(snapshot, actor)

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

    def _command_with_verified_actor(
        self, command: CommandHandler, actor: RuntimeActorContext
    ) -> CommandHandler:
        actor_reference = getattr(command, "actor_reference", None)
        if actor_reference is not None and actor_reference != actor.actor_id:
            raise RuntimeActorMismatchError(
                run_id=getattr(command, "run_id", None)
                or getattr(getattr(command, "specification", None), "run_id", None),
                command_id=getattr(command, "command_id", None),
            )
        return command.model_copy(update={"actor_reference": actor.actor_id})

    def _authorize_command(
        self, command: CommandHandler, actor: RuntimeActorContext
    ) -> RuntimeAuthorizationContext:
        if self.authorizer is None:
            from app.models.identity import AuthorizationDecision

            return RuntimeAuthorizationContext(
                actor=actor,
                permission_key="runtime.internal",
                resource_type="internal",
                resource_id="internal",
                allowed_by_admin=True,
                decision=AuthorizationDecision(
                    allowed=True,
                    permission_key="runtime.internal",
                    actor_agent_id=actor.actor_id,
                    resource_type="internal",
                    resource_id="internal",
                    matched_grants=["internal"],
                    matched_denials=[],
                    decisive_rule="internal",
                    reason_code="internal",
                ),
            )
        if isinstance(command, CreateAgentRunCommand):
            context = self.authorizer.authorize(
                actor, "create", specification=command.specification
            )
            parent_checked, parent_allowed_by_admin, parent_reason = self._authorize_parent_lineage(
                command.specification, actor
            )
            return replace(
                context,
                extra={
                    **context.extra,
                    "parentCheckRequired": command.specification.parent_run_id is not None,
                    "parentCheckPerformed": parent_checked,
                    "parentCheckAllowedByAdmin": parent_allowed_by_admin,
                    "parentCheckReasonCode": parent_reason,
                },
            )
        aggregate = self._load_current(command.run_id)
        operation = getattr(command, "command_type", "")
        return self.authorizer.authorize(actor, operation, snapshot=aggregate.snapshot)

    def _authorize_parent_lineage(
        self,
        specification: AgentRunSpecification,
        actor: RuntimeActorContext,
    ) -> tuple[bool, bool, str | None]:
        if self.authorizer is None or specification.parent_run_id is None:
            return False, False, None
        current_parent = specification.parent_run_id
        visited = {specification.run_id}
        depth = 0
        first_checked = False
        first_allowed_by_admin = False
        first_reason: str | None = None
        while current_parent is not None and depth < DEFAULT_LINEAGE_DEPTH_LIMIT:
            if current_parent in visited:
                break
            parent_snapshot = self.repository.load_run(current_parent)
            if parent_snapshot is None:
                break
            parent_context = self.authorizer.authorize(actor, "read", snapshot=parent_snapshot)
            if not first_checked:
                first_checked = True
                first_allowed_by_admin = parent_context.allowed_by_admin
                first_reason = parent_context.decision.reason_code
            visited.add(current_parent)
            current_parent = parent_snapshot.specification.parent_run_id
            depth += 1
        return first_checked, first_allowed_by_admin, first_reason

    def _require_authorized(
        self,
        actor: RuntimeActorContext,
        operation: str,
        *,
        snapshot: AgentRunSnapshot,
    ) -> RuntimeAuthorizationContext:
        if self.authorizer is None:
            return self._authorize_command(
                CreateAgentRunCommand(
                    specification=snapshot.specification,
                    command_id="internal-read",
                    expected_run_version=0,
                    timestamp=self.utc_clock(),
                    actor_reference=actor.actor_id,
                ),
                actor,
            )
        return self.authorizer.authorize(actor, operation, snapshot=snapshot)

    def _assert_replay_actor(
        self,
        existing: ProcessedCommandRecord,
        actor_reference: str | None,
    ) -> None:
        if (
            existing.verified_actor_id
            and actor_reference
            and existing.verified_actor_id != actor_reference
        ):
            raise RuntimeReplayActorMismatchError(
                run_id=existing.run_id,
                command_id=existing.command_id,
            )

    def create_run(self, command: CreateAgentRunCommand) -> RuntimeCommandResult:
        existing = self.repository.load_run(command.specification.run_id)
        if existing_record := self.repository.get_processed_command(
            command.specification.run_id,
            command.command_id,
        ):
            self._assert_replay_actor(existing_record, command.actor_reference)
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
            payload=build_run_created_payload(command.specification.model_dump(mode="json")),
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
        existing_record = self.repository.commit_command(
            snapshot=aggregate.snapshot,
            events=(event,),
            processed_command=record,
            expected_version=0,
            expected_sequence=0,
            create=True,
        )
        if existing_record is not None:
            return existing_record.result.model_copy(update={"idempotent_replay": True}, deep=True)
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
        if aggregate.attempts:
            latest_attempt = aggregate.attempts[-1]
            if latest_attempt.state == AttemptState.SUCCEEDED:
                raise InvalidAttemptStateError(
                    "A succeeded attempt must be finalized by completing the run before another attempt can start.",
                    run_id=command.run_id,
                    attempt_id=latest_attempt.attempt_id,
                    command_id=command.command_id,
                )
            if latest_attempt.state not in {
                AttemptState.FAILED,
                AttemptState.TIMED_OUT,
                AttemptState.ABANDONED,
            }:
                raise InvalidAttemptStateError(
                    "A prior attempt must reach an eligible terminal recovery state before another attempt can start.",
                    run_id=command.run_id,
                    attempt_id=latest_attempt.attempt_id,
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
        next_number = len(aggregate.attempts) + 1
        expected_recovery_plan = None
        if snapshot.recovery_status in {RecoveryStatus.REQUIRED, RecoveryStatus.PLANNED}:
            expected_recovery_plan = derive_recovery_plan(
                snapshot,
                list(aggregate.attempts),
                list(aggregate.checkpoints),
            )
            self._validate_recovery_attempt(
                aggregate,
                command,
                expected_recovery_plan=expected_recovery_plan,
                next_attempt_number=next_number,
            )
        elif command.resume_from_checkpoint_id is not None:
            raise CheckpointLineageError(
                "A resume checkpoint may only be used for an active recovery plan.",
                run_id=command.run_id,
                command_id=command.command_id,
                metadata={"checkpointId": command.resume_from_checkpoint_id},
            )
        attempt_id = command.attempt_id or self.attempt_id_factory()
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
        if snapshot.recovery_status == RecoveryStatus.REQUIRED:
            raise RecoveryNotAllowedError(
                "Recovery-required runs must remain blocked until recovery is planned.",
                run_id=command.run_id,
                command_id=command.command_id,
                metadata={
                    "state": snapshot.state,
                    "recoveryStatus": snapshot.recovery_status,
                },
            )
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
        if snapshot.active_attempt_id is None:
            event = self._build_event(
                command=command,
                run_id=command.run_id,
                event_type=AgentRuntimeEventType.RUN_CANCELLED,
                sequence_number=snapshot.event_sequence_number + 1,
                run_version=snapshot.version + 1,
                payload={"detail": command.detail},
            )
            return self._commit(command, aggregate, (event,))
        if snapshot.state != AgentRunState.CANCELLING:
            raise InvalidTransitionError(
                "Active cancellation must be confirmed only after cancellation has started.",
                run_id=command.run_id,
                attempt_id=snapshot.active_attempt_id,
                command_id=command.command_id,
                metadata={"sourceState": snapshot.state, "targetState": AgentRunState.CANCELLED},
            )
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
            if self._checkpoint_matches_command(
                existing_checkpoint,
                command,
                attempt.attempt_id,
                snapshot=snapshot,
                checkpoint_count=len(aggregate.checkpoints),
            ):
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
            failure_category=command.failure_category,
            failure_detail=command.failure_detail,
        )

    def timeout_attempt(self, command: TimeoutAttemptCommand) -> RuntimeCommandResult:
        return self._terminalize_attempt(
            command=command,
            event_type=AgentRuntimeEventType.ATTEMPT_TIMED_OUT,
            detail=command.detail,
            failure_category=FailureClassification.TIMEOUT,
            failure_detail=command.detail,
        )

    def abandon_attempt(self, command: AbandonAttemptCommand) -> RuntimeCommandResult:
        return self._terminalize_attempt(
            command=command,
            event_type=AgentRuntimeEventType.ATTEMPT_ABANDONED,
            detail=command.detail,
            failure_category=FailureClassification.INTERNAL,
            failure_detail=command.detail,
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
        recovery_plan = derive_recovery_plan(
            snapshot,
            list(aggregate.attempts),
            list(aggregate.checkpoints),
        )
        event = self._build_event(
            command=command,
            run_id=command.run_id,
            event_type=AgentRuntimeEventType.RECOVERY_PLANNED,
            sequence_number=snapshot.event_sequence_number + 1,
            run_version=snapshot.version + 1,
            payload={"plan": recovery_plan.model_dump(mode="json")},
        )
        updated_aggregate = apply_event(aggregate, event)
        result = RuntimeCommandResult(
            run_id=command.run_id,
            snapshot=updated_aggregate.snapshot,
            events=(event,),
            recovery_plan=recovery_plan,
        )
        record = self._processed_record(command.run_id, command.command_id, command, result)
        existing_record = self.repository.commit_command(
            snapshot=updated_aggregate.snapshot,
            events=(event,),
            processed_command=record,
            expected_version=aggregate.snapshot.version,
            expected_sequence=aggregate.snapshot.event_sequence_number,
        )
        if existing_record is not None:
            return existing_record.result.model_copy(update={"idempotent_replay": True}, deep=True)
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
        return self._resolve_lineage_from_snapshot(snapshot, depth_limit=depth_limit)

    def _resolve_lineage_authorized(
        self,
        snapshot: AgentRunSnapshot,
        actor: RuntimeActorContext,
        *,
        depth_limit: int = DEFAULT_LINEAGE_DEPTH_LIMIT,
    ) -> LineageResolution:
        return self._resolve_lineage_from_snapshot(
            snapshot,
            depth_limit=depth_limit,
            authorize=lambda parent: self._require_authorized(actor, "read", snapshot=parent),
        )

    def _resolve_lineage_from_snapshot(
        self,
        snapshot: AgentRunSnapshot,
        *,
        depth_limit: int = DEFAULT_LINEAGE_DEPTH_LIMIT,
        authorize: Callable[[AgentRunSnapshot], object] | None = None,
    ) -> LineageResolution:
        run_id = snapshot.specification.run_id
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
            parent_snapshot = self.repository.load_run(current_parent)
            if parent_snapshot is None:
                missing_parent_id = current_parent
                entries.append(LineageEntry(run_id=current_parent, exists=False, state=None))
                break
            if authorize is not None:
                authorize(parent_snapshot)
            visited.add(current_parent)
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
        failure_category: FailureClassification | None = None,
        failure_detail: str | None = None,
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
        if failure_category is not None:
            assert failure_detail is not None
            failure = FailureRecord(
                category=failure_category,
                detail=failure_detail,
                timestamp=command.timestamp,
                attempt_id=attempt.attempt_id,
            )
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
        state = self.repository.load_run_state(run_id)
        if state is None:
            raise RunNotFoundError(run_id=run_id)
        snapshot, events = state
        aggregate = replay_execution_ledger(events)
        if aggregate is None:
            raise RunNotFoundError(run_id=run_id)
        if aggregate.snapshot != snapshot:
            sleep(0.01)
            state = self.repository.load_run_state(run_id)
            if state is None:
                raise RunNotFoundError(run_id=run_id)
            snapshot, events = state
            aggregate = replay_execution_ledger(events)
            if aggregate is None or aggregate.snapshot != snapshot:
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
        updated_aggregate = aggregate
        for event in new_events:
            updated_aggregate = apply_event(updated_aggregate, event)
        result = RuntimeCommandResult(
            run_id=command.run_id,
            snapshot=updated_aggregate.snapshot,
            events=tuple(new_events),
        )
        record = self._processed_record(command.run_id, command.command_id, command, result)
        existing_record = self.repository.commit_command(
            snapshot=updated_aggregate.snapshot,
            events=new_events,
            processed_command=record,
            expected_version=aggregate.snapshot.version,
            expected_sequence=aggregate.snapshot.event_sequence_number,
        )
        if existing_record is not None:
            return existing_record.result.model_copy(update={"idempotent_replay": True}, deep=True)
        return result

    def _processed_record(
        self,
        run_id: str,
        command_id: CommandId,
        command: Any,
        result: RuntimeCommandResult,
    ) -> ProcessedCommandRecord:
        command_hash = stable_hash(command.model_dump(mode="json", exclude_none=False))
        recorded_at = getattr(command, "timestamp", None)
        if recorded_at is None:
            recorded_at = self.utc_clock()
        context = self._authorization_context.get()
        return ProcessedCommandRecord(
            run_id=run_id,
            command_id=command_id,
            command_hash=command_hash,
            result=result,
            recorded_at=recorded_at,
            verified_actor_id=getattr(command, "actor_reference", None),
            command_type=getattr(command, "command_type", "runtime"),
            authorization={} if context is None else context.bounded_metadata(),
        )

    def _assert_idempotency(
        self, run_id: str, command_id: CommandId, command: Any
    ) -> RuntimeCommandResult | None:
        existing = self.repository.get_processed_command(run_id, command_id)
        if existing is None:
            return None
        self._assert_replay_actor(existing, getattr(command, "actor_reference", None))
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
        *,
        snapshot: AgentRunSnapshot,
        checkpoint_count: int,
    ) -> bool:
        return (
            checkpoint.run_id == command.run_id
            and checkpoint.attempt_id == attempt_id
            and checkpoint.checkpoint_id == (command.checkpoint_id or checkpoint.checkpoint_id)
            and checkpoint.checkpoint_sequence == checkpoint_count
            and checkpoint.run_version == snapshot.version
            and checkpoint.event_sequence == snapshot.event_sequence_number
            and checkpoint.timestamp == command.timestamp
            and checkpoint.state_reference == command.state_reference
            and checkpoint.integrity_digest == command.integrity_digest
            and checkpoint.resume_cursor == command.resume_cursor
            and checkpoint.metadata == command.checkpoint_metadata
        )

    def _validate_recovery_attempt(
        self,
        aggregate: RuntimeAggregate,
        command: BeginAttemptCommand,
        *,
        expected_recovery_plan: RecoveryPlan,
        next_attempt_number: int,
    ) -> None:
        snapshot = aggregate.snapshot
        if expected_recovery_plan.run_id != command.run_id:
            raise RecoveryNotAllowedError(
                "The recovery plan does not belong to this run.",
                run_id=command.run_id,
                command_id=command.command_id,
            )
        if expected_recovery_plan.expected_version != snapshot.version:
            raise RecoveryNotAllowedError(
                "The recovery plan version is stale for the current run snapshot.",
                run_id=command.run_id,
                command_id=command.command_id,
                metadata={
                    "planVersion": expected_recovery_plan.expected_version,
                    "snapshotVersion": snapshot.version,
                },
            )
        if expected_recovery_plan.expected_event_sequence != snapshot.event_sequence_number:
            raise RecoveryNotAllowedError(
                "The recovery plan event sequence is stale for the current run snapshot.",
                run_id=command.run_id,
                command_id=command.command_id,
                metadata={
                    "planEventSequence": expected_recovery_plan.expected_event_sequence,
                    "snapshotEventSequence": snapshot.event_sequence_number,
                },
            )
        if expected_recovery_plan.next_attempt_number != next_attempt_number:
            raise RecoveryNotAllowedError(
                "The recovery plan no longer matches the next attempt number.",
                run_id=command.run_id,
                command_id=command.command_id,
                metadata={
                    "planAttemptNumber": expected_recovery_plan.next_attempt_number,
                    "nextAttemptNumber": next_attempt_number,
                },
            )
        latest_attempt = aggregate.attempts[-1] if aggregate.attempts else None
        if (
            latest_attempt is None
            or expected_recovery_plan.required_prior_terminal_attempt_id
            != latest_attempt.attempt_id
        ):
            raise RecoveryNotAllowedError(
                "The recovery plan no longer matches the prior terminal attempt.",
                run_id=command.run_id,
                command_id=command.command_id,
                metadata={
                    "planAttemptId": expected_recovery_plan.required_prior_terminal_attempt_id,
                    "latestAttemptId": None
                    if latest_attempt is None
                    else latest_attempt.attempt_id,
                },
            )
        selected_checkpoint = expected_recovery_plan.selected_checkpoint
        if selected_checkpoint is None:
            if command.resume_from_checkpoint_id is not None:
                raise CheckpointLineageError(
                    "The recovery plan does not allow a resume checkpoint for the next attempt.",
                    run_id=command.run_id,
                    command_id=command.command_id,
                    metadata={"checkpointId": command.resume_from_checkpoint_id},
                )
            return
        if command.resume_from_checkpoint_id is None:
            raise CheckpointLineageError(
                "The recovery plan requires the selected checkpoint for the next attempt.",
                run_id=command.run_id,
                command_id=command.command_id,
                metadata={"checkpointId": selected_checkpoint.checkpoint_id},
            )
        if command.resume_from_checkpoint_id != selected_checkpoint.checkpoint_id:
            raise CheckpointLineageError(
                "The supplied resume checkpoint does not match the selected recovery checkpoint.",
                run_id=command.run_id,
                command_id=command.command_id,
                metadata={
                    "checkpointId": command.resume_from_checkpoint_id,
                    "selectedCheckpointId": selected_checkpoint.checkpoint_id,
                },
            )
        checkpoint = self._find_checkpoint(aggregate, command.resume_from_checkpoint_id, command)
        if (
            checkpoint.run_id != command.run_id
            or checkpoint.attempt_id != selected_checkpoint.attempt_id
        ):
            raise CheckpointLineageError(
                "The supplied resume checkpoint does not match the selected recovery lineage.",
                run_id=command.run_id,
                command_id=command.command_id,
                metadata={
                    "checkpointId": checkpoint.checkpoint_id,
                    "checkpointAttemptId": checkpoint.attempt_id,
                    "selectedAttemptId": selected_checkpoint.attempt_id,
                },
            )
        if checkpoint != selected_checkpoint:
            raise CheckpointLineageError(
                "The supplied resume checkpoint does not match the selected recovery checkpoint state.",
                run_id=command.run_id,
                command_id=command.command_id,
                metadata={"checkpointId": checkpoint.checkpoint_id},
            )

    def _validate_parent_lineage(self, specification: AgentRunSpecification) -> None:
        validate_lineage_invariant(
            specification.run_id,
            specification.parent_run_id,
            lookup=self.repository.load_run,
            depth_limit=DEFAULT_LINEAGE_DEPTH_LIMIT,
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
