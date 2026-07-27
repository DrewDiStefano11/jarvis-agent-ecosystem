from __future__ import annotations

import pytest

from app.agent_runtime.errors import InvalidTransitionError, TerminalRunImmutableError
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
    ConfirmCancellationCommand,
    ConfirmCancellationStartCommand,
    ConfirmPauseCommand,
    QueueAgentRunCommand,
    RequestCancellationCommand,
    RequestPauseCommand,
    ResumeAgentRunCommand,
    StartAttemptCommand,
    UnblockAgentRunCommand,
)
from tests.agent_runtime_testkit import create_run, make_service, ts


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
