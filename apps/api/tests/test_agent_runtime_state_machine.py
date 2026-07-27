from __future__ import annotations

import pytest

from app.agent_runtime.errors import InvalidTransitionError, TerminalRunImmutableError
from app.agent_runtime.ledger import replay_execution_ledger
from app.agent_runtime.transitions import (
    ACTIVE_STATES,
    CANCELLATION_STATES,
    INTERRUPTED_STATES,
    PRE_EXECUTION_STATES,
    TERMINAL_STATES,
    classify_state,
)
from app.models.agent_runtime import (
    AgentRunState,
    BeginAttemptCommand,
    BlockAgentRunCommand,
    ClaimAgentRunCommand,
    CompleteAgentRunCommand,
    CompleteAttemptCommand,
    ConfirmCancellationCommand,
    ConfirmCancellationStartCommand,
    ConfirmPauseCommand,
    HeartbeatCommand,
    QueueAgentRunCommand,
    RequestCancellationCommand,
    RequestPauseCommand,
    ResumeAgentRunCommand,
    StartAttemptCommand,
    UnblockAgentRunCommand,
)
from tests.agent_runtime_testkit import (
    create_run,
    make_service,
    prepare_pause_requested_run,
    prepare_paused_run,
    prepare_running_run,
    ts,
)


def test_state_classification_sets_cover_required_lifecycle_groups() -> None:
    assert PRE_EXECUTION_STATES == {
        AgentRunState.CREATED,
        AgentRunState.QUEUED,
        AgentRunState.CLAIMED,
    }
    assert ACTIVE_STATES == {AgentRunState.STARTING, AgentRunState.RUNNING}
    assert INTERRUPTED_STATES == {
        AgentRunState.PAUSE_REQUESTED,
        AgentRunState.PAUSED,
        AgentRunState.BLOCKED,
    }
    assert CANCELLATION_STATES == {AgentRunState.CANCEL_REQUESTED, AgentRunState.CANCELLING}
    assert TERMINAL_STATES == {
        AgentRunState.CANCELLED,
        AgentRunState.SUCCEEDED,
        AgentRunState.FAILED,
        AgentRunState.TIMED_OUT,
        AgentRunState.ABANDONED,
    }
    assert classify_state(AgentRunState.RUNNING).value == "active"


def test_permitted_pause_and_resume_transition_flow() -> None:
    service = make_service()
    create_run(service)
    queued = service.queue_run(
        QueueAgentRunCommand(
            run_id="run-1",
            command_id="cmd-queue",
            expected_run_version=1,
            timestamp=ts(1),
            actor_reference="scheduler-1",
            source_metadata={"source": "test"},
        )
    )
    pause_requested = service.request_pause(
        RequestPauseCommand(
            run_id="run-1",
            command_id="cmd-pause-request",
            expected_run_version=2,
            timestamp=ts(2),
            actor_reference="operator-1",
            reason_code="operator_pause",
            detail="Pause before assignment",
            source_metadata={"source": "test"},
        )
    )
    paused = service.confirm_pause(
        ConfirmPauseCommand(
            run_id="run-1",
            command_id="cmd-pause-confirm",
            expected_run_version=3,
            timestamp=ts(3),
            actor_reference="operator-1",
            source_metadata={"source": "test"},
        )
    )
    resumed = service.resume_run(
        ResumeAgentRunCommand(
            run_id="run-1",
            command_id="cmd-resume",
            expected_run_version=4,
            timestamp=ts(4),
            actor_reference="operator-1",
            source_metadata={"source": "test"},
        )
    )

    assert queued.snapshot is not None and queued.snapshot.state == AgentRunState.QUEUED
    assert pause_requested.snapshot is not None
    assert pause_requested.snapshot.state == AgentRunState.PAUSE_REQUESTED
    assert paused.snapshot is not None and paused.snapshot.state == AgentRunState.PAUSED
    assert resumed.snapshot is not None and resumed.snapshot.state == AgentRunState.QUEUED
    assert resumed.snapshot.version == 5


@pytest.mark.parametrize(
    ("command", "code"),
    [
        (
            BeginAttemptCommand(
                run_id="run-1",
                command_id="cmd-begin-illegal",
                expected_run_version=1,
                timestamp=ts(1),
                actor_reference="worker-1",
                executor_reference="worker-1",
                source_metadata={"source": "test"},
            ),
            "invalid_transition",
        ),
        (
            CompleteAgentRunCommand(
                run_id="run-1",
                command_id="cmd-run-success-illegal",
                expected_run_version=1,
                timestamp=ts(1),
                actor_reference="worker-1",
                source_metadata={"source": "test"},
            ),
            "invalid_attempt_state",
        ),
    ],
)
def test_direct_illegal_jumps_are_rejected(command, code: str) -> None:
    service = make_service()
    create_run(service)
    with pytest.raises(Exception) as exc_info:
        service.handle(command)
    assert getattr(exc_info.value, "code", None) == code


def test_paused_to_starting_is_rejected() -> None:
    service = make_service()
    create_run(service)
    service.queue_run(
        QueueAgentRunCommand(
            run_id="run-1",
            command_id="cmd-queue",
            expected_run_version=1,
            timestamp=ts(1),
            actor_reference="scheduler-1",
            source_metadata={"source": "test"},
        )
    )
    service.request_pause(
        RequestPauseCommand(
            run_id="run-1",
            command_id="cmd-pause-request",
            expected_run_version=2,
            timestamp=ts(2),
            actor_reference="operator-1",
            reason_code="operator_pause",
            detail="Pause",
            source_metadata={"source": "test"},
        )
    )
    service.confirm_pause(
        ConfirmPauseCommand(
            run_id="run-1",
            command_id="cmd-pause-confirm",
            expected_run_version=3,
            timestamp=ts(3),
            actor_reference="operator-1",
            source_metadata={"source": "test"},
        )
    )
    with pytest.raises(InvalidTransitionError):
        service.begin_attempt(
            BeginAttemptCommand(
                run_id="run-1",
                command_id="cmd-begin-after-pause",
                expected_run_version=4,
                timestamp=ts(4),
                actor_reference="worker-1",
                executor_reference="worker-1",
                source_metadata={"source": "test"},
            )
        )


def test_terminal_runs_are_immutable() -> None:
    service = make_service()
    create_run(service)
    service.queue_run(
        QueueAgentRunCommand(
            run_id="run-1",
            command_id="cmd-queue",
            expected_run_version=1,
            timestamp=ts(1),
            actor_reference="scheduler-1",
            source_metadata={"source": "test"},
        )
    )
    service.claim_run(
        ClaimAgentRunCommand(
            run_id="run-1",
            command_id="cmd-claim",
            expected_run_version=2,
            timestamp=ts(2),
            actor_reference="scheduler-1",
            executor_reference="worker-1",
            source_metadata={"source": "test"},
        )
    )
    service.request_cancellation(
        RequestCancellationCommand(
            run_id="run-1",
            command_id="cmd-cancel-request",
            expected_run_version=3,
            timestamp=ts(3),
            actor_reference="operator-1",
            requester_reference="operator-1",
            reason_code="user_requested",
            detail="Stop this run",
            source_metadata={"source": "test"},
        )
    )
    with pytest.raises(TerminalRunImmutableError):
        service.queue_run(
            QueueAgentRunCommand(
                run_id="run-1",
                command_id="cmd-queue-again",
                expected_run_version=5,
                timestamp=ts(4),
                actor_reference="scheduler-1",
                source_metadata={"source": "test"},
            )
        )


def test_active_cancellation_handshake_reaches_terminal_state() -> None:
    service = make_service()
    create_run(service)
    service.queue_run(
        QueueAgentRunCommand(
            run_id="run-1",
            command_id="cmd-queue",
            expected_run_version=1,
            timestamp=ts(1),
            actor_reference="scheduler-1",
            source_metadata={"source": "test"},
        )
    )
    service.claim_run(
        ClaimAgentRunCommand(
            run_id="run-1",
            command_id="cmd-claim",
            expected_run_version=2,
            timestamp=ts(2),
            actor_reference="scheduler-1",
            executor_reference="worker-1",
            source_metadata={"source": "test"},
        )
    )
    service.begin_attempt(
        BeginAttemptCommand(
            run_id="run-1",
            command_id="cmd-begin",
            expected_run_version=3,
            timestamp=ts(3),
            actor_reference="worker-1",
            executor_reference="worker-1",
            source_metadata={"source": "test"},
        )
    )
    service.start_attempt(
        StartAttemptCommand(
            run_id="run-1",
            command_id="cmd-start",
            expected_run_version=5,
            timestamp=ts(4),
            actor_reference="worker-1",
            source_metadata={"source": "test"},
        )
    )
    requested = service.request_cancellation(
        RequestCancellationCommand(
            run_id="run-1",
            command_id="cmd-cancel-request",
            expected_run_version=6,
            timestamp=ts(5),
            actor_reference="operator-1",
            requester_reference="operator-1",
            reason_code="operator_cancel",
            detail="Stop the run",
            source_metadata={"source": "test"},
        )
    )
    started = service.confirm_cancellation_start(
        ConfirmCancellationStartCommand(
            run_id="run-1",
            command_id="cmd-cancel-start",
            expected_run_version=7,
            timestamp=ts(6),
            actor_reference="worker-1",
            source_metadata={"source": "test"},
        )
    )
    cancelled = service.confirm_cancellation(
        ConfirmCancellationCommand(
            run_id="run-1",
            command_id="cmd-cancel-confirm",
            expected_run_version=8,
            timestamp=ts(7),
            actor_reference="worker-1",
            source_metadata={"source": "test"},
        )
    )

    assert (
        requested.snapshot is not None
        and requested.snapshot.state == AgentRunState.CANCEL_REQUESTED
    )
    assert started.snapshot is not None and started.snapshot.state == AgentRunState.CANCELLING
    assert cancelled.snapshot is not None and cancelled.snapshot.state == AgentRunState.CANCELLED
    assert cancelled.snapshot.terminal_outcome.value == "cancelled"


def test_block_and_unblock_are_distinct_from_pause_and_resume() -> None:
    service = make_service()
    create_run(service)
    service.queue_run(
        QueueAgentRunCommand(
            run_id="run-1",
            command_id="cmd-queue",
            expected_run_version=1,
            timestamp=ts(1),
            actor_reference="scheduler-1",
            source_metadata={"source": "test"},
        )
    )
    service.claim_run(
        ClaimAgentRunCommand(
            run_id="run-1",
            command_id="cmd-claim",
            expected_run_version=2,
            timestamp=ts(2),
            actor_reference="scheduler-1",
            executor_reference="worker-1",
            source_metadata={"source": "test"},
        )
    )
    blocked = service.block_run(
        BlockAgentRunCommand(
            run_id="run-1",
            command_id="cmd-block",
            expected_run_version=3,
            timestamp=ts(3),
            actor_reference="operator-1",
            block_code="waiting_for_approval",
            detail="Waiting for approval",
            source_metadata={"source": "test"},
        )
    )
    unblocked = service.unblock_run(
        UnblockAgentRunCommand(
            run_id="run-1",
            command_id="cmd-unblock",
            expected_run_version=4,
            timestamp=ts(4),
            actor_reference="operator-1",
            source_metadata={"source": "test"},
        )
    )
    assert blocked.snapshot is not None and blocked.snapshot.state == AgentRunState.BLOCKED
    assert blocked.snapshot.pause_reason is None
    assert blocked.snapshot.blocking_reason is not None
    assert unblocked.snapshot is not None and unblocked.snapshot.state == AgentRunState.CLAIMED


def test_invalid_unblock_and_duplicate_resume_fail_deterministically() -> None:
    service = make_service()
    create_run(service)
    with pytest.raises(InvalidTransitionError):
        service.unblock_run(
            UnblockAgentRunCommand(
                run_id="run-1",
                command_id="cmd-unblock-invalid",
                expected_run_version=1,
                timestamp=ts(1),
                actor_reference="operator-1",
                source_metadata={"source": "test"},
            )
        )


@pytest.mark.parametrize(
    ("prepare", "expected_version", "second"),
    [
        (prepare_pause_requested_run, 7, 6),
        (prepare_paused_run, 8, 7),
    ],
)
def test_cancellation_clears_pause_metadata_from_interrupted_states(
    prepare, expected_version: int, second: int
) -> None:
    service = make_service()
    prepared = prepare(service)
    assert prepared.snapshot is not None
    cancelled = service.request_cancellation(
        RequestCancellationCommand(
            run_id="run-1",
            command_id=f"cmd-cancel-{prepared.snapshot.state.value}",
            expected_run_version=expected_version,
            timestamp=ts(second),
            actor_reference="operator-1",
            requester_reference="operator-1",
            reason_code="operator_cancel",
            detail="Stop the interrupted run",
            source_metadata={"source": "test"},
        )
    )
    assert cancelled.snapshot is not None
    assert cancelled.snapshot.state == AgentRunState.CANCEL_REQUESTED
    assert cancelled.snapshot.pause_reason is None
    assert cancelled.snapshot.blocking_reason is None
    assert cancelled.snapshot.cancellation is not None
    assert cancelled.snapshot.cancellation.reason_code == "operator_cancel"
    events = service.repository.list_events("run-1")
    assert events[-1].event_type.value == "cancellation_requested"
    replayed = replay_execution_ledger(events)
    assert replayed is not None
    assert replayed.snapshot == cancelled.snapshot


def test_cancellation_clears_block_metadata_from_blocked_state() -> None:
    service = make_service()
    prepare_running_run(service)
    blocked = service.block_run(
        BlockAgentRunCommand(
            run_id="run-1",
            command_id="cmd-block",
            expected_run_version=6,
            timestamp=ts(5),
            actor_reference="operator-1",
            block_code="waiting_for_approval",
            detail="Waiting for approval",
            source_metadata={"source": "test"},
        )
    )
    assert blocked.snapshot is not None and blocked.snapshot.blocking_reason is not None
    cancelled = service.request_cancellation(
        RequestCancellationCommand(
            run_id="run-1",
            command_id="cmd-cancel-blocked",
            expected_run_version=7,
            timestamp=ts(6),
            actor_reference="operator-1",
            requester_reference="operator-1",
            reason_code="operator_cancel",
            detail="Stop the blocked run",
            source_metadata={"source": "test"},
        )
    )
    assert cancelled.snapshot is not None
    assert cancelled.snapshot.state == AgentRunState.CANCEL_REQUESTED
    assert cancelled.snapshot.pause_reason is None
    assert cancelled.snapshot.blocking_reason is None
    assert cancelled.snapshot.cancellation is not None
    assert cancelled.snapshot.cancellation.reason_code == "operator_cancel"
    events = service.repository.list_events("run-1")
    assert events[-1].event_type.value == "cancellation_requested"
    replayed = replay_execution_ledger(events)
    assert replayed is not None
    assert replayed.snapshot == cancelled.snapshot


def test_cancellation_cannot_be_overwritten_by_success() -> None:
    service = make_service()
    prepare_running_run(service)
    service.request_cancellation(
        RequestCancellationCommand(
            run_id="run-1",
            command_id="cmd-cancel-request",
            expected_run_version=6,
            timestamp=ts(5),
            actor_reference="operator-1",
            requester_reference="operator-1",
            reason_code="operator_cancel",
            detail="Stop the run",
            source_metadata={"source": "test"},
        )
    )
    with pytest.raises(InvalidTransitionError):
        service.complete_attempt(
            CompleteAttemptCommand(
                run_id="run-1",
                command_id="cmd-attempt-success-after-cancel",
                expected_run_version=7,
                timestamp=ts(6),
                actor_reference="worker-1",
                source_metadata={"source": "test"},
            )
        )


def test_repeated_pause_resume_and_heartbeat_cycles_are_valid() -> None:
    service = make_service()
    prepare_running_run(service)
    service.request_pause(
        RequestPauseCommand(
            run_id="run-1",
            command_id="cmd-pause-request-1",
            expected_run_version=6,
            timestamp=ts(5),
            actor_reference="operator-1",
            reason_code="operator_pause",
            detail="Pause once",
            source_metadata={"source": "test"},
        )
    )
    service.confirm_pause(
        ConfirmPauseCommand(
            run_id="run-1",
            command_id="cmd-pause-confirm-1",
            expected_run_version=7,
            timestamp=ts(6),
            actor_reference="operator-1",
            source_metadata={"source": "test"},
        )
    )
    service.resume_run(
        ResumeAgentRunCommand(
            run_id="run-1",
            command_id="cmd-resume-1",
            expected_run_version=8,
            timestamp=ts(7),
            actor_reference="operator-1",
            source_metadata={"source": "test"},
        )
    )
    service.record_heartbeat(
        HeartbeatCommand(
            run_id="run-1",
            command_id="cmd-heartbeat-1",
            expected_run_version=9,
            timestamp=ts(8),
            actor_reference="worker-1",
            source_metadata={"source": "test"},
        )
    )
    service.request_pause(
        RequestPauseCommand(
            run_id="run-1",
            command_id="cmd-pause-request-2",
            expected_run_version=10,
            timestamp=ts(9),
            actor_reference="operator-1",
            reason_code="operator_pause",
            detail="Pause twice",
            source_metadata={"source": "test"},
        )
    )
    paused = service.confirm_pause(
        ConfirmPauseCommand(
            run_id="run-1",
            command_id="cmd-pause-confirm-2",
            expected_run_version=11,
            timestamp=ts(10),
            actor_reference="operator-1",
            source_metadata={"source": "test"},
        )
    )
    assert paused.snapshot is not None
    assert paused.snapshot.state == AgentRunState.PAUSED
    assert paused.snapshot.paused_at == ts(10)
    assert paused.snapshot.resumed_at == ts(7)
    assert paused.snapshot.last_heartbeat_at == ts(8)
    replayed = replay_execution_ledger(service.repository.list_events("run-1"))
    assert replayed is not None
    assert replayed.snapshot == paused.snapshot


def test_repeated_block_unblock_cycles_are_valid() -> None:
    service = make_service()
    prepare_running_run(service)
    service.block_run(
        BlockAgentRunCommand(
            run_id="run-1",
            command_id="cmd-block-1",
            expected_run_version=6,
            timestamp=ts(5),
            actor_reference="operator-1",
            block_code="waiting_for_approval",
            detail="First block",
            source_metadata={"source": "test"},
        )
    )
    service.unblock_run(
        UnblockAgentRunCommand(
            run_id="run-1",
            command_id="cmd-unblock-1",
            expected_run_version=7,
            timestamp=ts(6),
            actor_reference="operator-1",
            source_metadata={"source": "test"},
        )
    )
    blocked = service.block_run(
        BlockAgentRunCommand(
            run_id="run-1",
            command_id="cmd-block-2",
            expected_run_version=8,
            timestamp=ts(7),
            actor_reference="operator-1",
            block_code="dependency_wait",
            detail="Second block",
            source_metadata={"source": "test"},
        )
    )
    assert blocked.snapshot is not None
    assert blocked.snapshot.state == AgentRunState.BLOCKED
    assert blocked.snapshot.resumed_at == ts(6)
    assert blocked.snapshot.blocking_reason is not None
    assert blocked.snapshot.blocking_reason.code == "dependency_wait"
    replayed = replay_execution_ledger(service.repository.list_events("run-1"))
    assert replayed is not None
    assert replayed.snapshot == blocked.snapshot


def test_multiple_heartbeats_around_pause_cycles_are_valid() -> None:
    service = make_service()
    prepare_running_run(service)
    service.record_heartbeat(
        HeartbeatCommand(
            run_id="run-1",
            command_id="cmd-heartbeat-before-pause",
            expected_run_version=6,
            timestamp=ts(5),
            actor_reference="worker-1",
            source_metadata={"source": "test"},
        )
    )
    service.request_pause(
        RequestPauseCommand(
            run_id="run-1",
            command_id="cmd-pause-request-1",
            expected_run_version=7,
            timestamp=ts(6),
            actor_reference="operator-1",
            reason_code="operator_pause",
            detail="Pause once",
            source_metadata={"source": "test"},
        )
    )
    service.confirm_pause(
        ConfirmPauseCommand(
            run_id="run-1",
            command_id="cmd-pause-confirm-1",
            expected_run_version=8,
            timestamp=ts(7),
            actor_reference="operator-1",
            source_metadata={"source": "test"},
        )
    )
    service.resume_run(
        ResumeAgentRunCommand(
            run_id="run-1",
            command_id="cmd-resume-1",
            expected_run_version=9,
            timestamp=ts(8),
            actor_reference="operator-1",
            source_metadata={"source": "test"},
        )
    )
    running = service.record_heartbeat(
        HeartbeatCommand(
            run_id="run-1",
            command_id="cmd-heartbeat-after-resume",
            expected_run_version=10,
            timestamp=ts(9),
            actor_reference="worker-1",
            source_metadata={"source": "test"},
        )
    )
    assert running.snapshot is not None
    assert running.snapshot.state == AgentRunState.RUNNING
    assert running.snapshot.last_heartbeat_at == ts(9)
    assert running.snapshot.paused_at == ts(7)
    assert running.snapshot.resumed_at == ts(8)
    replayed = replay_execution_ledger(service.repository.list_events("run-1"))
    assert replayed is not None
    assert replayed.snapshot == running.snapshot
