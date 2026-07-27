from __future__ import annotations

from app.agent_runtime.errors import CheckpointLineageError, RecoveryNotAllowedError
from app.agent_runtime.transitions import TERMINAL_STATES
from app.models.agent_runtime import (
    AgentRunAttempt,
    AgentRunCheckpoint,
    AgentRunSnapshot,
    AgentRunState,
    AttemptState,
    FailureClassification,
    RecoveryPlan,
    RecoveryStatus,
    RuntimeEventEnvelope,
)


def plan_recovery(
    snapshot: AgentRunSnapshot,
    attempts: list[AgentRunAttempt],
    checkpoints: list[AgentRunCheckpoint],
    events: list[RuntimeEventEnvelope],
) -> RecoveryPlan:
    from app.agent_runtime.ledger import replay_execution_ledger

    aggregate = replay_execution_ledger(events)
    if aggregate is None:
        raise RecoveryNotAllowedError(
            "Recovery requires an existing execution ledger.",
            run_id=snapshot.specification.run_id,
        )
    if aggregate.snapshot != snapshot:
        raise RecoveryNotAllowedError(
            "The snapshot and execution ledger disagree.",
            run_id=snapshot.specification.run_id,
            metadata={"reason": "snapshot_ledger_mismatch"},
        )
    if list(aggregate.attempts) != attempts:
        raise RecoveryNotAllowedError(
            "Attempt history does not match the execution ledger.",
            run_id=snapshot.specification.run_id,
            metadata={"reason": "attempt_history_mismatch"},
        )
    _validate_checkpoint_lineage(snapshot, attempts, checkpoints)
    if list(aggregate.checkpoints) != checkpoints:
        raise RecoveryNotAllowedError(
            "Checkpoint history does not match the execution ledger.",
            run_id=snapshot.specification.run_id,
            metadata={"reason": "checkpoint_history_mismatch"},
        )
    return derive_recovery_plan(snapshot, attempts, checkpoints)


def derive_recovery_plan(
    snapshot: AgentRunSnapshot,
    attempts: list[AgentRunAttempt],
    checkpoints: list[AgentRunCheckpoint],
) -> RecoveryPlan:
    if snapshot.state in TERMINAL_STATES:
        raise RecoveryNotAllowedError(
            "Recovery is not allowed for terminal runs.",
            run_id=snapshot.specification.run_id,
            metadata={"state": snapshot.state},
        )
    if snapshot.recovery_status not in {RecoveryStatus.REQUIRED, RecoveryStatus.PLANNED}:
        raise RecoveryNotAllowedError(
            "Recovery is not active for this run.",
            run_id=snapshot.specification.run_id,
            metadata={
                "state": snapshot.state,
                "recoveryStatus": snapshot.recovery_status,
            },
        )
    if snapshot.recovery_status == RecoveryStatus.REQUIRED and (
        snapshot.state != AgentRunState.BLOCKED or snapshot.blocking_reason is None
    ):
        raise RecoveryNotAllowedError(
            "Recovery-required runs must remain blocked until a plan is derived.",
            run_id=snapshot.specification.run_id,
            metadata={"state": snapshot.state},
        )
    if (
        snapshot.recovery_status == RecoveryStatus.REQUIRED
        and snapshot.blocking_reason.code != "recovery_required"
    ):
        raise RecoveryNotAllowedError(
            "Recovery requires an explicit recovery_required block.",
            run_id=snapshot.specification.run_id,
            metadata={"blockCode": snapshot.blocking_reason.code},
        )
    if snapshot.active_attempt_id is not None:
        raise RecoveryNotAllowedError(
            "Recovery is not allowed while an attempt remains active.",
            run_id=snapshot.specification.run_id,
            attempt_id=snapshot.active_attempt_id,
        )
    if len(attempts) >= snapshot.specification.maximum_permitted_attempts:
        raise RecoveryNotAllowedError(
            "Recovery would exceed the maximum permitted attempts.",
            run_id=snapshot.specification.run_id,
            metadata={
                "attemptCount": len(attempts),
                "maximumAttempts": snapshot.specification.maximum_permitted_attempts,
            },
        )
    if not attempts:
        raise RecoveryNotAllowedError(
            "Recovery requires at least one prior attempt.",
            run_id=snapshot.specification.run_id,
        )
    latest_attempt = attempts[-1]
    if latest_attempt.state not in {
        AttemptState.FAILED,
        AttemptState.TIMED_OUT,
        AttemptState.ABANDONED,
    }:
        raise RecoveryNotAllowedError(
            "Recovery is only allowed after a failed, timed out, or abandoned attempt.",
            run_id=snapshot.specification.run_id,
            attempt_id=latest_attempt.attempt_id,
            metadata={"attemptState": latest_attempt.state},
        )

    warnings: list[str] = []
    selected_checkpoint = _select_checkpoint(snapshot, attempts, checkpoints, warnings)
    reason = (
        "Recovery is allowed from the selected checkpoint."
        if selected_checkpoint is not None
        else "Recovery is allowed from a fresh next attempt without a checkpoint."
    )
    return RecoveryPlan(
        run_id=snapshot.specification.run_id,
        recovery_allowed=True,
        selected_checkpoint=selected_checkpoint,
        next_attempt_number=len(attempts) + 1,
        expected_starting_state=AgentRunState.CLAIMED,
        required_prior_terminal_attempt_id=latest_attempt.attempt_id,
        reason=reason,
        warnings=tuple(warnings),
        expected_version=snapshot.version,
        expected_event_sequence=snapshot.event_sequence_number,
    )


def _validate_checkpoint_lineage(
    snapshot: AgentRunSnapshot,
    attempts: list[AgentRunAttempt],
    checkpoints: list[AgentRunCheckpoint],
) -> None:
    attempt_ids = {attempt.attempt_id for attempt in attempts}
    expected_sequence = 1
    for checkpoint in checkpoints:
        if checkpoint.run_id != snapshot.specification.run_id:
            raise CheckpointLineageError(
                "Checkpoint run IDs must match the runtime snapshot.",
                run_id=snapshot.specification.run_id,
                attempt_id=checkpoint.attempt_id,
                metadata={"checkpointId": checkpoint.checkpoint_id},
            )
        if checkpoint.attempt_id not in attempt_ids:
            raise CheckpointLineageError(
                "Checkpoint attempts must exist in the attempt history.",
                run_id=snapshot.specification.run_id,
                attempt_id=checkpoint.attempt_id,
                metadata={"checkpointId": checkpoint.checkpoint_id},
            )
        if checkpoint.checkpoint_sequence != expected_sequence:
            raise CheckpointLineageError(
                "Checkpoint sequences must be contiguous.",
                run_id=snapshot.specification.run_id,
                attempt_id=checkpoint.attempt_id,
                metadata={
                    "checkpointId": checkpoint.checkpoint_id,
                    "expectedSequence": expected_sequence,
                    "actualSequence": checkpoint.checkpoint_sequence,
                },
            )
        if (
            checkpoint.run_version > snapshot.version
            or checkpoint.event_sequence > snapshot.event_sequence_number
        ):
            raise CheckpointLineageError(
                "Checkpoint positions cannot be ahead of the runtime snapshot.",
                run_id=snapshot.specification.run_id,
                attempt_id=checkpoint.attempt_id,
                metadata={"checkpointId": checkpoint.checkpoint_id},
            )
        expected_sequence += 1


def _select_checkpoint(
    snapshot: AgentRunSnapshot,
    attempts: list[AgentRunAttempt],
    checkpoints: list[AgentRunCheckpoint],
    warnings: list[str],
) -> AgentRunCheckpoint | None:
    latest_attempt = attempts[-1]
    latest_attempt_checkpoints = [
        checkpoint
        for checkpoint in checkpoints
        if checkpoint.attempt_id == latest_attempt.attempt_id
    ]
    if latest_attempt_checkpoints:
        return sorted(
            latest_attempt_checkpoints,
            key=lambda item: (item.checkpoint_sequence, item.event_sequence, item.checkpoint_id),
        )[-1]
    earlier_checkpoints = [
        checkpoint
        for checkpoint in checkpoints
        if checkpoint.attempt_id != latest_attempt.attempt_id
        and checkpoint.run_id == snapshot.specification.run_id
    ]
    if earlier_checkpoints:
        warnings.append(
            "No checkpoint exists for the latest attempt; using the most recent earlier checkpoint."
        )
        return sorted(
            earlier_checkpoints,
            key=lambda item: (item.checkpoint_sequence, item.event_sequence, item.checkpoint_id),
        )[-1]
    warnings.append(
        "No valid checkpoint is available; recovery must restart from the next attempt boundary."
    )
    return None


def failure_for_timeout(snapshot: AgentRunSnapshot) -> FailureClassification:
    return (
        FailureClassification.TIMEOUT
        if snapshot.recovery_status == RecoveryStatus.REQUIRED
        else FailureClassification.UNKNOWN
    )
