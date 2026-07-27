from __future__ import annotations

from typing import cast

from app.agent_runtime.errors import InvalidTransitionError, LedgerReplayError, LedgerSequenceError
from app.agent_runtime.transitions import TERMINAL_STATES, TRANSITION_RULES
from app.models.agent_runtime import (
    SUPPORTED_EVENT_SCHEMA_VERSION,
    AgentRunAttempt,
    AgentRunCheckpoint,
    AgentRunSnapshot,
    AgentRunSpecification,
    AgentRunState,
    AgentRuntimeEventType,
    AttemptState,
    BlockingReason,
    CancellationRecord,
    FailureClassification,
    FailureRecord,
    PauseReason,
    RecoveryStatus,
    RuntimeContract,
    RuntimeEventEnvelope,
    TerminalOutcome,
)


class RuntimeAggregate(RuntimeContract):
    snapshot: AgentRunSnapshot
    attempts: tuple[AgentRunAttempt, ...]
    checkpoints: tuple[AgentRunCheckpoint, ...]


def replay_execution_ledger(events: list[RuntimeEventEnvelope]) -> RuntimeAggregate | None:
    if not events:
        return None
    current: RuntimeAggregate | None = None
    expected_sequence = 1
    expected_version = 1
    last_timestamp = None
    run_id = None
    for event in events:
        if event.event_schema_version != SUPPORTED_EVENT_SCHEMA_VERSION:
            raise LedgerReplayError(
                "Unsupported event schema version.",
                run_id=event.run_id,
                command_id=event.command_id,
                metadata={
                    "eventType": event.event_type,
                    "schemaVersion": event.event_schema_version,
                },
            )
        if run_id is None:
            run_id = event.run_id
        elif event.run_id != run_id:
            raise LedgerReplayError(
                "Event run IDs do not match within the ledger.",
                run_id=run_id,
                command_id=event.command_id,
                metadata={"eventRunId": event.run_id},
            )
        if event.sequence_number != expected_sequence:
            raise LedgerSequenceError(
                "Ledger sequence numbers must begin at one and increase by one.",
                run_id=event.run_id,
                command_id=event.command_id,
                metadata={
                    "expectedSequence": expected_sequence,
                    "actualSequence": event.sequence_number,
                },
            )
        if event.run_version != expected_version:
            raise LedgerReplayError(
                "Run versions must increase by one for each event.",
                run_id=event.run_id,
                command_id=event.command_id,
                metadata={"expectedVersion": expected_version, "actualVersion": event.run_version},
            )
        if last_timestamp is not None and event.timestamp < last_timestamp:
            raise LedgerReplayError(
                "Event timestamps must not move backward.",
                run_id=event.run_id,
                command_id=event.command_id,
                metadata={"previousTimestamp": last_timestamp.isoformat()},
            )
        if current is not None and current.snapshot.state in TERMINAL_STATES:
            raise LedgerReplayError(
                "Events cannot follow a terminal run state.",
                run_id=event.run_id,
                command_id=event.command_id,
                metadata={"state": current.snapshot.state},
            )
        current = apply_event(current, event)
        expected_sequence += 1
        expected_version += 1
        last_timestamp = event.timestamp
    return current


def replay_snapshot(events: list[RuntimeEventEnvelope]) -> AgentRunSnapshot | None:
    aggregate = replay_execution_ledger(events)
    return None if aggregate is None else aggregate.snapshot


def apply_event(current: RuntimeAggregate | None, event: RuntimeEventEnvelope) -> RuntimeAggregate:
    if current is None:
        return _apply_run_created(event)
    if event.event_type == AgentRuntimeEventType.RUN_CREATED:
        raise LedgerReplayError(
            "run_created may only appear as the first ledger event.",
            run_id=event.run_id,
            command_id=event.command_id,
        )
    attempts = {attempt.attempt_id: attempt for attempt in current.attempts}
    checkpoints = {checkpoint.checkpoint_id: checkpoint for checkpoint in current.checkpoints}
    attempt_order = [attempt.attempt_id for attempt in current.attempts]
    checkpoint_order = [checkpoint.checkpoint_id for checkpoint in current.checkpoints]
    snapshot = current.snapshot
    target_state = _resolve_target_state(snapshot.state, event)
    _validate_rule(
        event,
        source_state=snapshot.state,
        target_state=target_state,
        attempts=attempts,
        active_attempt_id=snapshot.active_attempt_id,
    )

    if event.event_type == AgentRuntimeEventType.RUN_QUEUED:
        snapshot = snapshot.model_copy(
            update={
                "state": AgentRunState.QUEUED,
                "queued_at": event.timestamp,
                "version": event.run_version,
                "event_sequence_number": event.sequence_number,
                "status_detail": cast(str, event.payload["detail"]),
            }
        )
    elif event.event_type == AgentRuntimeEventType.RUN_CLAIMED:
        snapshot = snapshot.model_copy(
            update={
                "state": AgentRunState.CLAIMED,
                "claimed_at": event.timestamp,
                "version": event.run_version,
                "event_sequence_number": event.sequence_number,
                "status_detail": cast(str, event.payload["detail"]),
            }
        )
    elif event.event_type == AgentRuntimeEventType.RUN_START_REQUESTED:
        snapshot = snapshot.model_copy(
            update={
                "state": AgentRunState.STARTING,
                "version": event.run_version,
                "event_sequence_number": event.sequence_number,
                "status_detail": cast(str, event.payload["detail"]),
                "recovery_status": RecoveryStatus.NONE,
                "blocking_reason": None,
            }
        )
    elif event.event_type == AgentRuntimeEventType.ATTEMPT_CREATED:
        attempt = AgentRunAttempt.model_validate(cast(dict[str, object], event.payload["attempt"]))
        if attempt.run_id != event.run_id or attempt.attempt_id != event.attempt_id:
            raise LedgerReplayError(
                "Attempt payload does not match the enclosing event identifiers.",
                run_id=event.run_id,
                attempt_id=event.attempt_id,
                command_id=event.command_id,
            )
        if attempt.attempt_id in attempts:
            raise LedgerReplayError(
                "Attempt IDs must be unique within a run.",
                run_id=event.run_id,
                attempt_id=attempt.attempt_id,
                command_id=event.command_id,
            )
        attempts[attempt.attempt_id] = attempt
        attempt_order.append(attempt.attempt_id)
        snapshot = snapshot.model_copy(
            update={
                "active_attempt_id": attempt.attempt_id,
                "attempt_count": attempt.attempt_number,
                "version": event.run_version,
                "event_sequence_number": event.sequence_number,
                "status_detail": "Attempt created",
            }
        )
    elif event.event_type == AgentRuntimeEventType.ATTEMPT_STARTED:
        active_attempt = _get_active_attempt(event, snapshot, attempts)
        attempts[active_attempt.attempt_id] = active_attempt.model_copy(
            update={
                "state": AttemptState.RUNNING,
                "version": event.run_version,
            }
        )
        snapshot = snapshot.model_copy(
            update={
                "state": AgentRunState.RUNNING,
                "started_at": snapshot.started_at or event.timestamp,
                "version": event.run_version,
                "event_sequence_number": event.sequence_number,
                "status_detail": cast(str, event.payload["detail"]),
            }
        )
    elif event.event_type == AgentRuntimeEventType.HEARTBEAT_RECORDED:
        active_attempt = _get_active_attempt(event, snapshot, attempts)
        attempts[active_attempt.attempt_id] = active_attempt.model_copy(
            update={
                "last_heartbeat_at": event.timestamp,
                "version": event.run_version,
            }
        )
        snapshot = snapshot.model_copy(
            update={
                "last_heartbeat_at": event.timestamp,
                "version": event.run_version,
                "event_sequence_number": event.sequence_number,
                "status_detail": cast(str, event.payload["detail"]),
            }
        )
    elif event.event_type == AgentRuntimeEventType.PAUSE_REQUESTED:
        pause = PauseReason.model_validate(cast(dict[str, object], event.payload["pause"]))
        snapshot = snapshot.model_copy(
            update={
                "state": AgentRunState.PAUSE_REQUESTED,
                "pause_reason": pause,
                "version": event.run_version,
                "event_sequence_number": event.sequence_number,
                "status_detail": pause.detail,
            }
        )
    elif event.event_type == AgentRuntimeEventType.RUN_PAUSED:
        active_attempt = _get_optional_active_attempt(snapshot, attempts)
        if active_attempt is not None:
            attempts[active_attempt.attempt_id] = active_attempt.model_copy(
                update={"state": AttemptState.PAUSED, "version": event.run_version}
            )
        snapshot = snapshot.model_copy(
            update={
                "state": AgentRunState.PAUSED,
                "paused_at": event.timestamp,
                "version": event.run_version,
                "event_sequence_number": event.sequence_number,
                "status_detail": cast(str, event.payload["detail"]),
            }
        )
    elif event.event_type == AgentRuntimeEventType.RUN_RESUMED:
        target = AgentRunState(cast(str, event.payload["target_state"]))
        active_attempt = _get_optional_active_attempt(snapshot, attempts)
        if target == AgentRunState.RUNNING:
            if active_attempt is None:
                raise LedgerReplayError(
                    "Resuming to running requires an active attempt.",
                    run_id=event.run_id,
                    command_id=event.command_id,
                )
            attempts[active_attempt.attempt_id] = active_attempt.model_copy(
                update={"state": AttemptState.RUNNING, "version": event.run_version}
            )
        snapshot = snapshot.model_copy(
            update={
                "state": target,
                "pause_reason": None,
                "resumed_at": event.timestamp,
                "version": event.run_version,
                "event_sequence_number": event.sequence_number,
                "status_detail": cast(str, event.payload["detail"]),
            }
        )
    elif event.event_type == AgentRuntimeEventType.RUN_BLOCKED:
        block = BlockingReason.model_validate(cast(dict[str, object], event.payload["block"]))
        active_attempt = _get_optional_active_attempt(snapshot, attempts)
        if active_attempt is not None:
            attempts[active_attempt.attempt_id] = active_attempt.model_copy(
                update={"state": AttemptState.PAUSED, "version": event.run_version}
            )
        snapshot = snapshot.model_copy(
            update={
                "state": AgentRunState.BLOCKED,
                "blocking_reason": block,
                "pause_reason": None,
                "version": event.run_version,
                "event_sequence_number": event.sequence_number,
                "status_detail": block.detail,
            }
        )
    elif event.event_type == AgentRuntimeEventType.RUN_UNBLOCKED:
        target = AgentRunState(cast(str, event.payload["target_state"]))
        active_attempt = _get_optional_active_attempt(snapshot, attempts)
        if target == AgentRunState.RUNNING:
            if active_attempt is None:
                raise LedgerReplayError(
                    "Unblocking to running requires an active attempt.",
                    run_id=event.run_id,
                    command_id=event.command_id,
                )
            attempts[active_attempt.attempt_id] = active_attempt.model_copy(
                update={"state": AttemptState.RUNNING, "version": event.run_version}
            )
        snapshot = snapshot.model_copy(
            update={
                "state": target,
                "blocking_reason": None,
                "resumed_at": event.timestamp,
                "version": event.run_version,
                "event_sequence_number": event.sequence_number,
                "status_detail": cast(str, event.payload["detail"]),
            }
        )
    elif event.event_type == AgentRuntimeEventType.CANCELLATION_REQUESTED:
        cancellation = CancellationRecord.model_validate(
            cast(dict[str, object], event.payload["cancellation"])
        )
        snapshot = snapshot.model_copy(
            update={
                "state": AgentRunState.CANCEL_REQUESTED,
                "cancellation": cancellation,
                "cancellation_requested_at": cancellation.timestamp,
                "version": event.run_version,
                "event_sequence_number": event.sequence_number,
                "status_detail": cancellation.detail,
            }
        )
    elif event.event_type == AgentRuntimeEventType.CANCELLATION_STARTED:
        snapshot = snapshot.model_copy(
            update={
                "state": AgentRunState.CANCELLING,
                "version": event.run_version,
                "event_sequence_number": event.sequence_number,
                "status_detail": cast(str, event.payload["detail"]),
            }
        )
    elif event.event_type == AgentRuntimeEventType.RUN_CANCELLED:
        active_attempt = _get_optional_active_attempt(snapshot, attempts)
        if active_attempt is not None:
            attempts[active_attempt.attempt_id] = active_attempt.model_copy(
                update={
                    "state": AttemptState.CANCELLED,
                    "finished_at": event.timestamp,
                    "outcome": TerminalOutcome.CANCELLED,
                    "cancellation_acknowledged_at": event.timestamp,
                    "version": event.run_version,
                }
            )
        snapshot = snapshot.model_copy(
            update={
                "state": AgentRunState.CANCELLED,
                "active_attempt_id": None,
                "completed_at": event.timestamp,
                "terminal_outcome": TerminalOutcome.CANCELLED,
                "version": event.run_version,
                "event_sequence_number": event.sequence_number,
                "status_detail": cast(str, event.payload["detail"]),
                "blocking_reason": None,
                "pause_reason": None,
                "recovery_status": RecoveryStatus.NONE,
            }
        )
    elif event.event_type == AgentRuntimeEventType.CHECKPOINT_RECORDED:
        checkpoint = AgentRunCheckpoint.model_validate(
            cast(dict[str, object], event.payload["checkpoint"])
        )
        active_attempt = _get_active_attempt(event, snapshot, attempts)
        if checkpoint.run_id != event.run_id or checkpoint.attempt_id != active_attempt.attempt_id:
            raise LedgerReplayError(
                "Checkpoint lineage does not match the active run attempt.",
                run_id=event.run_id,
                attempt_id=event.attempt_id,
                command_id=event.command_id,
            )
        if (
            checkpoint.run_version != event.run_version
            or checkpoint.event_sequence != event.sequence_number
        ):
            raise LedgerReplayError(
                "Checkpoint position must match the checkpoint event position.",
                run_id=event.run_id,
                attempt_id=active_attempt.attempt_id,
                command_id=event.command_id,
            )
        if checkpoint.checkpoint_id in checkpoints:
            raise LedgerReplayError(
                "Checkpoint IDs must be unique within a run.",
                run_id=event.run_id,
                attempt_id=active_attempt.attempt_id,
                command_id=event.command_id,
            )
        expected_checkpoint_sequence = len(checkpoint_order) + 1
        if checkpoint.checkpoint_sequence != expected_checkpoint_sequence:
            raise LedgerReplayError(
                "Checkpoint sequences must increase by one.",
                run_id=event.run_id,
                attempt_id=active_attempt.attempt_id,
                command_id=event.command_id,
                metadata={
                    "expectedCheckpointSequence": expected_checkpoint_sequence,
                    "actualCheckpointSequence": checkpoint.checkpoint_sequence,
                },
            )
        checkpoints[checkpoint.checkpoint_id] = checkpoint
        checkpoint_order.append(checkpoint.checkpoint_id)
        snapshot = snapshot.model_copy(
            update={
                "latest_checkpoint_id": checkpoint.checkpoint_id,
                "version": event.run_version,
                "event_sequence_number": event.sequence_number,
                "status_detail": "Checkpoint recorded",
            }
        )
    elif event.event_type == AgentRuntimeEventType.ATTEMPT_SUCCEEDED:
        active_attempt = _get_active_attempt(event, snapshot, attempts)
        attempts[active_attempt.attempt_id] = active_attempt.model_copy(
            update={
                "state": AttemptState.SUCCEEDED,
                "finished_at": event.timestamp,
                "outcome": TerminalOutcome.SUCCESS,
                "version": event.run_version,
            }
        )
        snapshot = snapshot.model_copy(
            update={
                "state": AgentRunState.CLAIMED,
                "active_attempt_id": None,
                "version": event.run_version,
                "event_sequence_number": event.sequence_number,
                "status_detail": cast(str, event.payload["detail"]),
                "recovery_status": RecoveryStatus.NONE,
            }
        )
    elif event.event_type in {
        AgentRuntimeEventType.ATTEMPT_FAILED,
        AgentRuntimeEventType.ATTEMPT_TIMED_OUT,
        AgentRuntimeEventType.ATTEMPT_ABANDONED,
    }:
        active_attempt = _get_active_attempt(event, snapshot, attempts)
        failure = FailureRecord.model_validate(cast(dict[str, object], event.payload["failure"]))
        block = BlockingReason.model_validate(
            cast(dict[str, object], event.payload["blocking_reason"])
        )
        terminal_state = {
            AgentRuntimeEventType.ATTEMPT_FAILED: AttemptState.FAILED,
            AgentRuntimeEventType.ATTEMPT_TIMED_OUT: AttemptState.TIMED_OUT,
            AgentRuntimeEventType.ATTEMPT_ABANDONED: AttemptState.ABANDONED,
        }[event.event_type]
        outcome = {
            AgentRuntimeEventType.ATTEMPT_FAILED: TerminalOutcome.FAILURE,
            AgentRuntimeEventType.ATTEMPT_TIMED_OUT: TerminalOutcome.TIMEOUT,
            AgentRuntimeEventType.ATTEMPT_ABANDONED: TerminalOutcome.ABANDONED,
        }[event.event_type]
        attempts[active_attempt.attempt_id] = active_attempt.model_copy(
            update={
                "state": terminal_state,
                "finished_at": event.timestamp,
                "outcome": outcome,
                "failure_category": failure.category,
                "failure_detail": failure.detail,
                "version": event.run_version,
            }
        )
        snapshot = snapshot.model_copy(
            update={
                "state": AgentRunState.BLOCKED,
                "active_attempt_id": None,
                "failure": failure,
                "blocking_reason": block,
                "version": event.run_version,
                "event_sequence_number": event.sequence_number,
                "status_detail": failure.detail,
                "recovery_status": RecoveryStatus.REQUIRED,
            }
        )
    elif event.event_type == AgentRuntimeEventType.RUN_SUCCEEDED:
        snapshot = snapshot.model_copy(
            update={
                "state": AgentRunState.SUCCEEDED,
                "completed_at": event.timestamp,
                "terminal_outcome": TerminalOutcome.SUCCESS,
                "version": event.run_version,
                "event_sequence_number": event.sequence_number,
                "status_detail": cast(str, event.payload["detail"]),
                "blocking_reason": None,
                "pause_reason": None,
                "recovery_status": RecoveryStatus.NONE,
            }
        )
    elif event.event_type in {
        AgentRuntimeEventType.RUN_FAILED,
        AgentRuntimeEventType.RUN_TIMED_OUT,
        AgentRuntimeEventType.RUN_ABANDONED,
    }:
        failure = FailureRecord.model_validate(cast(dict[str, object], event.payload["failure"]))
        state = {
            AgentRuntimeEventType.RUN_FAILED: AgentRunState.FAILED,
            AgentRuntimeEventType.RUN_TIMED_OUT: AgentRunState.TIMED_OUT,
            AgentRuntimeEventType.RUN_ABANDONED: AgentRunState.ABANDONED,
        }[event.event_type]
        outcome = {
            AgentRuntimeEventType.RUN_FAILED: TerminalOutcome.FAILURE,
            AgentRuntimeEventType.RUN_TIMED_OUT: TerminalOutcome.TIMEOUT,
            AgentRuntimeEventType.RUN_ABANDONED: TerminalOutcome.ABANDONED,
        }[event.event_type]
        snapshot = snapshot.model_copy(
            update={
                "state": state,
                "completed_at": event.timestamp,
                "terminal_outcome": outcome,
                "failure": failure,
                "version": event.run_version,
                "event_sequence_number": event.sequence_number,
                "status_detail": failure.detail,
                "active_attempt_id": None,
                "blocking_reason": None,
                "pause_reason": None,
                "recovery_status": RecoveryStatus.NONE,
            }
        )
    elif event.event_type == AgentRuntimeEventType.RECOVERY_PLANNED:
        plan = cast(dict[str, object], event.payload["plan"])
        snapshot = snapshot.model_copy(
            update={
                "version": event.run_version,
                "event_sequence_number": event.sequence_number,
                "status_detail": cast(str, plan["reason"]),
                "recovery_status": RecoveryStatus.PLANNED,
            }
        )
    else:
        raise LedgerReplayError(
            "Unsupported event type.",
            run_id=event.run_id,
            command_id=event.command_id,
            metadata={"eventType": event.event_type},
        )

    return RuntimeAggregate(
        snapshot=snapshot,
        attempts=tuple(sorted(attempts.values(), key=lambda item: item.attempt_number)),
        checkpoints=tuple(sorted(checkpoints.values(), key=lambda item: item.checkpoint_sequence)),
    )


def _apply_run_created(event: RuntimeEventEnvelope) -> RuntimeAggregate:
    _validate_rule(
        event,
        source_state=None,
        target_state=AgentRunState.CREATED,
        attempts={},
        active_attempt_id=None,
    )
    specification = AgentRunSpecification.model_validate(
        cast(dict[str, object], event.payload["specification"])
    )
    if specification.run_id != event.run_id:
        raise LedgerReplayError(
            "Run specification must match the enclosing event run ID.",
            run_id=event.run_id,
            command_id=event.command_id,
        )
    if specification.created_at > event.timestamp:
        raise LedgerReplayError(
            "Run creation timestamp must not be later than the creation event timestamp.",
            run_id=event.run_id,
            command_id=event.command_id,
        )
    snapshot = AgentRunSnapshot(
        specification=specification,
        state=AgentRunState.CREATED,
        version=event.run_version,
        event_sequence_number=event.sequence_number,
        attempt_count=0,
        active_attempt_id=None,
        latest_checkpoint_id=None,
        created_at=specification.created_at,
        status_detail=cast(str | None, event.payload.get("detail")),
        recovery_status=RecoveryStatus.NONE,
    )
    return RuntimeAggregate(snapshot=snapshot, attempts=(), checkpoints=())


def _validate_rule(
    event: RuntimeEventEnvelope,
    *,
    source_state: AgentRunState | None,
    target_state: AgentRunState,
    attempts: dict[str, AgentRunAttempt],
    active_attempt_id: str | None,
) -> None:
    rule = TRANSITION_RULES[event.event_type]
    missing_keys = [key for key in rule.required_metadata if key not in event.payload]
    if missing_keys:
        raise LedgerReplayError(
            "Event payload is missing required metadata.",
            run_id=event.run_id,
            attempt_id=event.attempt_id,
            command_id=event.command_id,
            metadata={"missingKeys": missing_keys, "eventType": event.event_type},
        )
    if rule.allowed_sources is None:
        if source_state is not None:
            raise InvalidTransitionError(
                "The event is only valid at ledger start.",
                run_id=event.run_id,
                attempt_id=event.attempt_id,
                command_id=event.command_id,
                metadata={"sourceState": source_state, "targetState": target_state},
            )
    elif source_state not in rule.allowed_sources:
        raise InvalidTransitionError(
            "The event is not allowed from the current run state.",
            run_id=event.run_id,
            attempt_id=event.attempt_id,
            command_id=event.command_id,
            metadata={
                "sourceState": source_state,
                "targetState": target_state,
                "eventType": event.event_type,
            },
        )
    if target_state not in rule.allowed_targets:
        raise InvalidTransitionError(
            "The event target state is not allowed.",
            run_id=event.run_id,
            attempt_id=event.attempt_id,
            command_id=event.command_id,
            metadata={
                "sourceState": source_state,
                "targetState": target_state,
                "eventType": event.event_type,
            },
        )
    if rule.requires_attempt and event.attempt_id is None:
        raise LedgerReplayError(
            "The event requires an attempt ID.",
            run_id=event.run_id,
            command_id=event.command_id,
            metadata={"eventType": event.event_type},
        )
    if rule.requires_active_attempt:
        if active_attempt_id is None:
            raise LedgerReplayError(
                "The event requires an active attempt.",
                run_id=event.run_id,
                command_id=event.command_id,
                metadata={"eventType": event.event_type},
            )
        if event.attempt_id != active_attempt_id:
            raise LedgerReplayError(
                "The event attempt ID must match the active attempt ID.",
                run_id=event.run_id,
                attempt_id=event.attempt_id,
                command_id=event.command_id,
                metadata={"activeAttemptId": active_attempt_id},
            )
        if active_attempt_id not in attempts:
            raise LedgerReplayError(
                "The active attempt does not exist in history.",
                run_id=event.run_id,
                attempt_id=active_attempt_id,
                command_id=event.command_id,
            )


def _resolve_target_state(
    current_state: AgentRunState, event: RuntimeEventEnvelope
) -> AgentRunState:
    if event.event_type == AgentRuntimeEventType.HEARTBEAT_RECORDED:
        return current_state
    if event.event_type == AgentRuntimeEventType.ATTEMPT_CREATED:
        return current_state
    if event.event_type == AgentRuntimeEventType.CHECKPOINT_RECORDED:
        return current_state
    if event.event_type == AgentRuntimeEventType.RECOVERY_PLANNED:
        return current_state
    if event.event_type == AgentRuntimeEventType.RUN_RESUMED:
        return AgentRunState(cast(str, event.payload["target_state"]))
    if event.event_type == AgentRuntimeEventType.RUN_UNBLOCKED:
        return AgentRunState(cast(str, event.payload["target_state"]))
    target_map = {
        AgentRuntimeEventType.RUN_QUEUED: AgentRunState.QUEUED,
        AgentRuntimeEventType.RUN_CLAIMED: AgentRunState.CLAIMED,
        AgentRuntimeEventType.RUN_START_REQUESTED: AgentRunState.STARTING,
        AgentRuntimeEventType.ATTEMPT_STARTED: AgentRunState.RUNNING,
        AgentRuntimeEventType.PAUSE_REQUESTED: AgentRunState.PAUSE_REQUESTED,
        AgentRuntimeEventType.RUN_PAUSED: AgentRunState.PAUSED,
        AgentRuntimeEventType.RUN_BLOCKED: AgentRunState.BLOCKED,
        AgentRuntimeEventType.CANCELLATION_REQUESTED: AgentRunState.CANCEL_REQUESTED,
        AgentRuntimeEventType.CANCELLATION_STARTED: AgentRunState.CANCELLING,
        AgentRuntimeEventType.RUN_CANCELLED: AgentRunState.CANCELLED,
        AgentRuntimeEventType.ATTEMPT_SUCCEEDED: AgentRunState.CLAIMED,
        AgentRuntimeEventType.ATTEMPT_FAILED: AgentRunState.BLOCKED,
        AgentRuntimeEventType.ATTEMPT_TIMED_OUT: AgentRunState.BLOCKED,
        AgentRuntimeEventType.ATTEMPT_ABANDONED: AgentRunState.BLOCKED,
        AgentRuntimeEventType.RUN_SUCCEEDED: AgentRunState.SUCCEEDED,
        AgentRuntimeEventType.RUN_FAILED: AgentRunState.FAILED,
        AgentRuntimeEventType.RUN_TIMED_OUT: AgentRunState.TIMED_OUT,
        AgentRuntimeEventType.RUN_ABANDONED: AgentRunState.ABANDONED,
    }
    try:
        return target_map[event.event_type]
    except KeyError as exc:
        raise LedgerReplayError(
            "Unable to resolve event target state.",
            run_id=event.run_id,
            command_id=event.command_id,
            metadata={"eventType": event.event_type},
        ) from exc


def _get_active_attempt(
    event: RuntimeEventEnvelope,
    snapshot: AgentRunSnapshot,
    attempts: dict[str, AgentRunAttempt],
) -> AgentRunAttempt:
    active_attempt = _get_optional_active_attempt(snapshot, attempts)
    if active_attempt is None:
        raise LedgerReplayError(
            "The event requires an active attempt.",
            run_id=event.run_id,
            attempt_id=event.attempt_id,
            command_id=event.command_id,
        )
    if event.attempt_id is not None and event.attempt_id != active_attempt.attempt_id:
        raise LedgerReplayError(
            "The event attempt ID does not match the active attempt.",
            run_id=event.run_id,
            attempt_id=event.attempt_id,
            command_id=event.command_id,
            metadata={"activeAttemptId": active_attempt.attempt_id},
        )
    return active_attempt


def _get_optional_active_attempt(
    snapshot: AgentRunSnapshot,
    attempts: dict[str, AgentRunAttempt],
) -> AgentRunAttempt | None:
    if snapshot.active_attempt_id is None:
        return None
    return attempts.get(snapshot.active_attempt_id)


def terminal_failure_record(
    *,
    category: FailureClassification,
    detail: str,
    timestamp,
    attempt_id: str | None = None,
) -> FailureRecord:
    return FailureRecord(
        category=category,
        detail=detail,
        timestamp=timestamp,
        attempt_id=attempt_id,
    )
