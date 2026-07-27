from __future__ import annotations

from typing import Any, cast

from pydantic import ValidationError

from app.agent_runtime.errors import (
    InvalidTransitionError,
    LedgerReplayError,
    LedgerSequenceError,
    RecoveryNotAllowedError,
)
from app.agent_runtime.recovery import derive_recovery_plan
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
    RecoveryPlan,
    RecoveryStatus,
    RuntimeContract,
    RuntimeEventEnvelope,
    TerminalOutcome,
    validate_identifier,
    validate_safe_text,
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


def _payload_validation_error(
    event: RuntimeEventEnvelope,
    *,
    section: str,
    exc: Exception,
) -> LedgerReplayError:
    metadata: dict[str, Any] = {"eventType": event.event_type, "payloadSection": section}
    if isinstance(exc, ValidationError):
        metadata["errors"] = exc.errors(include_url=False)
    else:
        metadata["errorType"] = type(exc).__name__
    return LedgerReplayError(
        f"The {section} payload is invalid for this event type.",
        run_id=event.run_id,
        attempt_id=event.attempt_id,
        command_id=event.command_id,
        metadata=metadata,
    )


def _payload_mapping(event: RuntimeEventEnvelope, key: str) -> dict[str, object]:
    raw = event.payload.get(key)
    if not isinstance(raw, dict):
        raise _payload_validation_error(
            event,
            section=key,
            exc=TypeError(f"{key} must be an object"),
        )
    return raw


def _payload_detail(event: RuntimeEventEnvelope, key: str = "detail") -> str:
    raw = event.payload.get(key)
    if not isinstance(raw, str):
        raise _payload_validation_error(
            event,
            section=key,
            exc=TypeError(f"{key} must be a string"),
        )
    try:
        return validate_safe_text(raw, field_name=f"event.{key}")
    except (TypeError, ValueError) as exc:
        raise _payload_validation_error(event, section=key, exc=exc) from exc


def _payload_agent_run_state(
    event: RuntimeEventEnvelope, key: str = "target_state"
) -> AgentRunState:
    raw = event.payload.get(key)
    if not isinstance(raw, str):
        raise _payload_validation_error(
            event,
            section=key,
            exc=TypeError(f"{key} must be a string"),
        )
    try:
        return AgentRunState(raw)
    except ValueError as exc:
        raise _payload_validation_error(event, section=key, exc=exc) from exc


def _payload_optional_identifier(
    event: RuntimeEventEnvelope,
    key: str,
    field_name: str,
    *,
    max_length: int = 120,
) -> str | None:
    raw = event.payload.get(key)
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise _payload_validation_error(
            event,
            section=key,
            exc=TypeError(f"{key} must be a string when present"),
        )
    try:
        return validate_identifier(raw, field_name=field_name, max_length=max_length)
    except (TypeError, ValueError) as exc:
        raise _payload_validation_error(event, section=key, exc=exc) from exc


def _payload_model(event: RuntimeEventEnvelope, key: str, model_type: type[Any]) -> Any:
    try:
        return model_type.model_validate(_payload_mapping(event, key))
    except LedgerReplayError:
        raise
    except (ValidationError, TypeError, ValueError) as exc:
        raise _payload_validation_error(event, section=key, exc=exc) from exc


def _validated_event_payload(event: RuntimeEventEnvelope) -> dict[str, object]:
    detail_events = {
        AgentRuntimeEventType.RUN_QUEUED,
        AgentRuntimeEventType.ATTEMPT_STARTED,
        AgentRuntimeEventType.HEARTBEAT_RECORDED,
        AgentRuntimeEventType.RUN_PAUSED,
        AgentRuntimeEventType.CANCELLATION_STARTED,
        AgentRuntimeEventType.RUN_CANCELLED,
        AgentRuntimeEventType.ATTEMPT_SUCCEEDED,
        AgentRuntimeEventType.RUN_SUCCEEDED,
    }
    if event.event_type == AgentRuntimeEventType.RUN_CREATED:
        specification = _payload_model(event, "specification", AgentRunSpecification)
        return {
            "specification": specification,
            "detail": _payload_detail(event),
        }
    if event.event_type in detail_events:
        return {"detail": _payload_detail(event)}
    if event.event_type == AgentRuntimeEventType.RUN_CLAIMED:
        return {
            "detail": _payload_detail(event),
            "executor_reference": _payload_optional_identifier(
                event,
                "executor_reference",
                "opaque_reference",
                max_length=160,
            ),
        }
    if event.event_type == AgentRuntimeEventType.RUN_START_REQUESTED:
        return {
            "detail": _payload_detail(event),
            "executor_reference": _payload_optional_identifier(
                event,
                "executor_reference",
                "opaque_reference",
                max_length=160,
            ),
            "resume_from_checkpoint_id": _payload_optional_identifier(
                event, "resume_from_checkpoint_id", "checkpoint_id"
            ),
        }
    if event.event_type == AgentRuntimeEventType.ATTEMPT_CREATED:
        return {"attempt": _payload_model(event, "attempt", AgentRunAttempt)}
    if event.event_type == AgentRuntimeEventType.PAUSE_REQUESTED:
        return {"pause": _payload_model(event, "pause", PauseReason)}
    if event.event_type == AgentRuntimeEventType.RUN_RESUMED:
        return {
            "detail": _payload_detail(event),
            "target_state": _payload_agent_run_state(event),
        }
    if event.event_type == AgentRuntimeEventType.RUN_BLOCKED:
        return {"block": _payload_model(event, "block", BlockingReason)}
    if event.event_type == AgentRuntimeEventType.RUN_UNBLOCKED:
        return {
            "detail": _payload_detail(event),
            "target_state": _payload_agent_run_state(event),
        }
    if event.event_type == AgentRuntimeEventType.CANCELLATION_REQUESTED:
        return {"cancellation": _payload_model(event, "cancellation", CancellationRecord)}
    if event.event_type == AgentRuntimeEventType.CHECKPOINT_RECORDED:
        return {"checkpoint": _payload_model(event, "checkpoint", AgentRunCheckpoint)}
    if event.event_type in {
        AgentRuntimeEventType.ATTEMPT_FAILED,
        AgentRuntimeEventType.ATTEMPT_TIMED_OUT,
        AgentRuntimeEventType.ATTEMPT_ABANDONED,
    }:
        return {
            "failure": _payload_model(event, "failure", FailureRecord),
            "blocking_reason": _payload_model(event, "blocking_reason", BlockingReason),
        }
    if event.event_type in {
        AgentRuntimeEventType.RUN_FAILED,
        AgentRuntimeEventType.RUN_TIMED_OUT,
        AgentRuntimeEventType.RUN_ABANDONED,
    }:
        return {"failure": _payload_model(event, "failure", FailureRecord)}
    if event.event_type == AgentRuntimeEventType.RECOVERY_PLANNED:
        return {"plan": _payload_model(event, "plan", RecoveryPlan)}
    return {}


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
    validated_payload = _validated_event_payload(event)
    target_state = _resolve_target_state(snapshot.state, event, validated_payload)
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
                "status_detail": cast(str, validated_payload["detail"]),
            }
        )
    elif event.event_type == AgentRuntimeEventType.RUN_CLAIMED:
        snapshot = snapshot.model_copy(
            update={
                "state": AgentRunState.CLAIMED,
                "claimed_at": event.timestamp,
                "version": event.run_version,
                "event_sequence_number": event.sequence_number,
                "status_detail": cast(str, validated_payload["detail"]),
            }
        )
    elif event.event_type == AgentRuntimeEventType.RUN_START_REQUESTED:
        snapshot = snapshot.model_copy(
            update={
                "state": AgentRunState.STARTING,
                "version": event.run_version,
                "event_sequence_number": event.sequence_number,
                "status_detail": cast(str, validated_payload["detail"]),
                "blocking_reason": None,
            }
        )
    elif event.event_type == AgentRuntimeEventType.ATTEMPT_CREATED:
        attempt = cast(AgentRunAttempt, validated_payload["attempt"])
        _validate_attempt_created(
            snapshot=snapshot,
            attempts=attempts,
            checkpoints=checkpoints,
            event=event,
            attempt=attempt,
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
                "recovery_status": RecoveryStatus.NONE,
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
                "status_detail": cast(str, validated_payload["detail"]),
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
                "status_detail": cast(str, validated_payload["detail"]),
            }
        )
    elif event.event_type == AgentRuntimeEventType.PAUSE_REQUESTED:
        pause = cast(PauseReason, validated_payload["pause"])
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
                "status_detail": cast(str, validated_payload["detail"]),
            }
        )
    elif event.event_type == AgentRuntimeEventType.RUN_RESUMED:
        target = cast(AgentRunState, validated_payload["target_state"])
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
                "status_detail": cast(str, validated_payload["detail"]),
            }
        )
    elif event.event_type == AgentRuntimeEventType.RUN_BLOCKED:
        block = cast(BlockingReason, validated_payload["block"])
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
        target = cast(AgentRunState, validated_payload["target_state"])
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
                "status_detail": cast(str, validated_payload["detail"]),
            }
        )
    elif event.event_type == AgentRuntimeEventType.CANCELLATION_REQUESTED:
        cancellation = cast(CancellationRecord, validated_payload["cancellation"])
        snapshot = snapshot.model_copy(
            update={
                "state": AgentRunState.CANCEL_REQUESTED,
                "cancellation": cancellation,
                "cancellation_requested_at": cancellation.timestamp,
                "version": event.run_version,
                "event_sequence_number": event.sequence_number,
                "status_detail": cancellation.detail,
                "pause_reason": None,
                "blocking_reason": None,
                "recovery_status": RecoveryStatus.NONE,
            }
        )
    elif event.event_type == AgentRuntimeEventType.CANCELLATION_STARTED:
        snapshot = snapshot.model_copy(
            update={
                "state": AgentRunState.CANCELLING,
                "version": event.run_version,
                "event_sequence_number": event.sequence_number,
                "status_detail": cast(str, validated_payload["detail"]),
                "pause_reason": None,
                "blocking_reason": None,
                "recovery_status": RecoveryStatus.NONE,
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
                "status_detail": cast(str, validated_payload["detail"]),
                "blocking_reason": None,
                "pause_reason": None,
                "recovery_status": RecoveryStatus.NONE,
            }
        )
    elif event.event_type == AgentRuntimeEventType.CHECKPOINT_RECORDED:
        checkpoint = cast(AgentRunCheckpoint, validated_payload["checkpoint"])
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
                "status_detail": cast(str, validated_payload["detail"]),
                "recovery_status": RecoveryStatus.NONE,
            }
        )
    elif event.event_type in {
        AgentRuntimeEventType.ATTEMPT_FAILED,
        AgentRuntimeEventType.ATTEMPT_TIMED_OUT,
        AgentRuntimeEventType.ATTEMPT_ABANDONED,
    }:
        active_attempt = _get_active_attempt(event, snapshot, attempts)
        failure = cast(FailureRecord, validated_payload["failure"])
        block = cast(BlockingReason, validated_payload["blocking_reason"])
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
                "pause_reason": None,
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
                "status_detail": cast(str, validated_payload["detail"]),
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
        failure = cast(FailureRecord, validated_payload["failure"])
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
        recorded_plan = _validate_recovery_plan_event(
            snapshot=snapshot,
            attempts=attempts,
            checkpoints=checkpoints,
            event=event,
            recorded_plan=cast(RecoveryPlan, validated_payload["plan"]),
        )
        snapshot = snapshot.model_copy(
            update={
                "version": event.run_version,
                "event_sequence_number": event.sequence_number,
                "status_detail": recorded_plan.reason,
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

    return _build_validated_aggregate(
        snapshot=snapshot,
        attempts=attempts,
        checkpoints=checkpoints,
        event=event,
    )


def _apply_run_created(event: RuntimeEventEnvelope) -> RuntimeAggregate:
    _validate_rule(
        event,
        source_state=None,
        target_state=AgentRunState.CREATED,
        attempts={},
        active_attempt_id=None,
    )
    validated_payload = _validated_event_payload(event)
    specification = cast(AgentRunSpecification, validated_payload["specification"])
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
        status_detail=cast(str | None, validated_payload.get("detail")),
        recovery_status=RecoveryStatus.NONE,
    )
    return _build_validated_aggregate(
        snapshot=snapshot,
        attempts={},
        checkpoints={},
        event=event,
    )


def _build_validated_aggregate(
    *,
    snapshot: AgentRunSnapshot,
    attempts: dict[str, AgentRunAttempt],
    checkpoints: dict[str, AgentRunCheckpoint],
    event: RuntimeEventEnvelope,
) -> RuntimeAggregate:
    try:
        validated_snapshot = AgentRunSnapshot.model_validate(
            snapshot.model_dump(mode="python", round_trip=True, warnings=False)
        )
        validated_attempts = tuple(
            AgentRunAttempt.model_validate(
                attempt.model_dump(mode="python", round_trip=True, warnings=False)
            )
            for attempt in sorted(attempts.values(), key=lambda item: item.attempt_number)
        )
        validated_checkpoints = tuple(
            AgentRunCheckpoint.model_validate(
                checkpoint.model_dump(mode="python", round_trip=True, warnings=False)
            )
            for checkpoint in sorted(
                checkpoints.values(), key=lambda item: item.checkpoint_sequence
            )
        )
        return RuntimeAggregate(
            snapshot=validated_snapshot,
            attempts=validated_attempts,
            checkpoints=validated_checkpoints,
        )
    except ValidationError as exc:
        raise LedgerReplayError(
            "The replayed aggregate contains invalid contract values.",
            run_id=event.run_id,
            attempt_id=event.attempt_id,
            command_id=event.command_id,
            metadata={"errors": exc.errors(include_url=False), "eventType": event.event_type},
        ) from exc


def _validate_attempt_created(
    *,
    snapshot: AgentRunSnapshot,
    attempts: dict[str, AgentRunAttempt],
    checkpoints: dict[str, AgentRunCheckpoint],
    event: RuntimeEventEnvelope,
    attempt: AgentRunAttempt,
) -> None:
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
    if snapshot.active_attempt_id is not None:
        raise LedgerReplayError(
            "A new attempt cannot be created while another attempt is active.",
            run_id=event.run_id,
            attempt_id=snapshot.active_attempt_id,
            command_id=event.command_id,
        )
    expected_number = len(attempts) + 1
    if attempt.attempt_number != expected_number:
        raise LedgerReplayError(
            "Attempt numbers must increase exactly by one.",
            run_id=event.run_id,
            attempt_id=attempt.attempt_id,
            command_id=event.command_id,
            metadata={
                "expectedAttemptNumber": expected_number,
                "actualAttemptNumber": attempt.attempt_number,
            },
        )
    if attempt.attempt_number > snapshot.specification.maximum_permitted_attempts:
        raise LedgerReplayError(
            "Attempt creation exceeds the configured maximum permitted attempts.",
            run_id=event.run_id,
            attempt_id=attempt.attempt_id,
            command_id=event.command_id,
            metadata={
                "attemptNumber": attempt.attempt_number,
                "maximumAttempts": snapshot.specification.maximum_permitted_attempts,
            },
        )
    nonterminal_prior = [
        prior
        for prior in attempts.values()
        if prior.state
        not in {
            AttemptState.CANCELLED,
            AttemptState.SUCCEEDED,
            AttemptState.FAILED,
            AttemptState.TIMED_OUT,
            AttemptState.ABANDONED,
        }
    ]
    if nonterminal_prior:
        raise LedgerReplayError(
            "A prior attempt must be terminal before another attempt can be created.",
            run_id=event.run_id,
            attempt_id=attempt.attempt_id,
            command_id=event.command_id,
            metadata={"priorAttemptId": nonterminal_prior[0].attempt_id},
        )
    if attempt.state != AttemptState.STARTING:
        raise LedgerReplayError(
            "Attempt creation must begin in the starting state.",
            run_id=event.run_id,
            attempt_id=attempt.attempt_id,
            command_id=event.command_id,
            metadata={"attemptState": attempt.state},
        )
    if attempt.version != event.run_version:
        raise LedgerReplayError(
            "Attempt version must match the enclosing event version.",
            run_id=event.run_id,
            attempt_id=attempt.attempt_id,
            command_id=event.command_id,
            metadata={
                "attemptVersion": attempt.version,
                "eventVersion": event.run_version,
            },
        )
    if attempt.started_at != event.timestamp:
        raise LedgerReplayError(
            "Attempt timestamps must match the enclosing event timestamp.",
            run_id=event.run_id,
            attempt_id=attempt.attempt_id,
            command_id=event.command_id,
            metadata={
                "attemptStartedAt": attempt.started_at.isoformat(),
                "eventTimestamp": event.timestamp.isoformat(),
            },
        )

    resumed_checkpoint = None
    if attempt.resumed_from_checkpoint_id is not None:
        resumed_checkpoint = checkpoints.get(attempt.resumed_from_checkpoint_id)
        if resumed_checkpoint is None:
            raise LedgerReplayError(
                "The resumed checkpoint does not exist in the replay ledger.",
                run_id=event.run_id,
                attempt_id=attempt.attempt_id,
                command_id=event.command_id,
                metadata={"checkpointId": attempt.resumed_from_checkpoint_id},
            )
        if resumed_checkpoint.run_id != event.run_id:
            raise LedgerReplayError(
                "The resumed checkpoint belongs to another run.",
                run_id=event.run_id,
                attempt_id=attempt.attempt_id,
                command_id=event.command_id,
                metadata={"checkpointId": resumed_checkpoint.checkpoint_id},
            )
        if resumed_checkpoint.attempt_id == attempt.attempt_id:
            raise LedgerReplayError(
                "Attempts must not resume from their own checkpoint lineage.",
                run_id=event.run_id,
                attempt_id=attempt.attempt_id,
                command_id=event.command_id,
                metadata={"checkpointId": resumed_checkpoint.checkpoint_id},
            )
        if resumed_checkpoint.attempt_id not in attempts:
            raise LedgerReplayError(
                "The resumed checkpoint must belong to a prior attempt.",
                run_id=event.run_id,
                attempt_id=attempt.attempt_id,
                command_id=event.command_id,
                metadata={"checkpointId": resumed_checkpoint.checkpoint_id},
            )

    if snapshot.recovery_status in {RecoveryStatus.REQUIRED, RecoveryStatus.PLANNED}:
        try:
            expected_plan = derive_recovery_plan(
                snapshot,
                list(sorted(attempts.values(), key=lambda item: item.attempt_number)),
                list(sorted(checkpoints.values(), key=lambda item: item.checkpoint_sequence)),
            )
        except RecoveryNotAllowedError as exc:
            raise LedgerReplayError(
                exc.message,
                run_id=event.run_id,
                attempt_id=attempt.attempt_id,
                command_id=event.command_id,
                metadata=exc.metadata,
            ) from exc
        if (
            expected_plan.expected_version != snapshot.version
            or expected_plan.expected_event_sequence != snapshot.event_sequence_number
        ):
            raise LedgerReplayError(
                "The recovery plan is stale for the current replay snapshot.",
                run_id=event.run_id,
                attempt_id=attempt.attempt_id,
                command_id=event.command_id,
                metadata={
                    "planVersion": expected_plan.expected_version,
                    "snapshotVersion": snapshot.version,
                    "planEventSequence": expected_plan.expected_event_sequence,
                    "snapshotEventSequence": snapshot.event_sequence_number,
                },
            )
        if expected_plan.next_attempt_number != attempt.attempt_number:
            raise LedgerReplayError(
                "The replayed attempt number does not match the expected recovery plan.",
                run_id=event.run_id,
                attempt_id=attempt.attempt_id,
                command_id=event.command_id,
                metadata={
                    "planAttemptNumber": expected_plan.next_attempt_number,
                    "attemptNumber": attempt.attempt_number,
                },
            )
        latest_attempt = list(sorted(attempts.values(), key=lambda item: item.attempt_number))[-1]
        if expected_plan.required_prior_terminal_attempt_id != latest_attempt.attempt_id:
            raise LedgerReplayError(
                "The replayed recovery attempt does not match the required prior terminal attempt.",
                run_id=event.run_id,
                attempt_id=attempt.attempt_id,
                command_id=event.command_id,
                metadata={
                    "requiredAttemptId": expected_plan.required_prior_terminal_attempt_id,
                    "latestAttemptId": latest_attempt.attempt_id,
                },
            )
        if expected_plan.selected_checkpoint is None:
            if attempt.resumed_from_checkpoint_id is not None:
                raise LedgerReplayError(
                    "The recovery plan does not allow a resume checkpoint for this attempt.",
                    run_id=event.run_id,
                    attempt_id=attempt.attempt_id,
                    command_id=event.command_id,
                    metadata={"checkpointId": attempt.resumed_from_checkpoint_id},
                )
        else:
            if (
                attempt.resumed_from_checkpoint_id
                != expected_plan.selected_checkpoint.checkpoint_id
            ):
                raise LedgerReplayError(
                    "The replayed recovery attempt does not use the selected checkpoint.",
                    run_id=event.run_id,
                    attempt_id=attempt.attempt_id,
                    command_id=event.command_id,
                    metadata={
                        "checkpointId": attempt.resumed_from_checkpoint_id,
                        "selectedCheckpointId": expected_plan.selected_checkpoint.checkpoint_id,
                    },
                )
            if resumed_checkpoint != expected_plan.selected_checkpoint:
                raise LedgerReplayError(
                    "The replayed recovery checkpoint does not match the selected checkpoint lineage.",
                    run_id=event.run_id,
                    attempt_id=attempt.attempt_id,
                    command_id=event.command_id,
                    metadata={"checkpointId": attempt.resumed_from_checkpoint_id},
                )
    elif attempt.resumed_from_checkpoint_id is not None:
        raise LedgerReplayError(
            "A resume checkpoint may only be used for an active recovery attempt.",
            run_id=event.run_id,
            attempt_id=attempt.attempt_id,
            command_id=event.command_id,
            metadata={"checkpointId": attempt.resumed_from_checkpoint_id},
        )


def _validate_recovery_plan_event(
    *,
    snapshot: AgentRunSnapshot,
    attempts: dict[str, AgentRunAttempt],
    checkpoints: dict[str, AgentRunCheckpoint],
    event: RuntimeEventEnvelope,
    recorded_plan: RecoveryPlan,
) -> RecoveryPlan:
    try:
        expected_plan = derive_recovery_plan(
            snapshot,
            list(sorted(attempts.values(), key=lambda item: item.attempt_number)),
            list(sorted(checkpoints.values(), key=lambda item: item.checkpoint_sequence)),
        )
    except RecoveryNotAllowedError as exc:
        raise LedgerReplayError(
            exc.message,
            run_id=event.run_id,
            command_id=event.command_id,
            metadata=exc.metadata,
        ) from exc
    if recorded_plan != expected_plan:
        raise LedgerReplayError(
            "The recorded recovery plan does not match the deterministic expected recovery plan.",
            run_id=event.run_id,
            command_id=event.command_id,
            metadata={
                "recordedPlan": recorded_plan.model_dump(mode="json"),
                "expectedPlan": expected_plan.model_dump(mode="json"),
            },
        )
    return recorded_plan


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
    current_state: AgentRunState,
    event: RuntimeEventEnvelope,
    validated_payload: dict[str, object],
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
        return cast(AgentRunState, validated_payload["target_state"])
    if event.event_type == AgentRuntimeEventType.RUN_UNBLOCKED:
        return cast(AgentRunState, validated_payload["target_state"])
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
