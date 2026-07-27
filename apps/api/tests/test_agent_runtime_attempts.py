from __future__ import annotations

import pytest

from app.agent_runtime.errors import (
    ActiveAttemptExistsError,
    AttemptNotFoundError,
    InvalidAttemptStateError,
    RecoveryNotAllowedError,
)
from app.agent_runtime.ledger import replay_execution_ledger
from app.models.agent_runtime import (
    AbandonAttemptCommand,
    AgentRunState,
    AttemptState,
    CompleteAgentRunCommand,
    CompleteAttemptCommand,
    FailAttemptCommand,
    RecordCheckpointCommand,
    RequestRecoveryPlanCommand,
    StartAttemptCommand,
    TimeoutAttemptCommand,
    UnblockAgentRunCommand,
)
from tests.agent_runtime_testkit import (
    begin_attempt,
    claim_run,
    complete_attempt,
    confirm_pause,
    create_run,
    make_service,
    make_spec,
    prepare_pause_requested_run,
    prepare_paused_run,
    prepare_running_run,
    queue_run,
    request_pause,
    start_attempt,
    ts,
)


def test_first_attempt_number_starts_at_one() -> None:
    service = make_service()
    prepare_running_run(service)
    attempts = service.repository.load_attempt_history("run-1")
    assert attempts[0].attempt_number == 1
    assert attempts[0].state.value == "running"


def test_multiple_attempts_use_monotonic_numbers() -> None:
    service = make_service()
    prepare_running_run(service)
    service.abandon_attempt(
        AbandonAttemptCommand(
            run_id="run-1",
            command_id="cmd-abandon-attempt-1",
            expected_run_version=6,
            timestamp=ts(5),
            actor_reference="worker-1",
            source_metadata={"source": "test"},
        )
    )
    service.request_recovery_plan(
        RequestRecoveryPlanCommand(
            run_id="run-1",
            command_id="cmd-recovery-plan-1",
            expected_run_version=7,
            timestamp=ts(6),
            actor_reference="operator-1",
            source_metadata={"source": "test"},
        )
    )
    service.unblock_run(
        UnblockAgentRunCommand(
            run_id="run-1",
            command_id="cmd-unblock-1",
            expected_run_version=8,
            timestamp=ts(7),
            actor_reference="operator-1",
            source_metadata={"source": "test"},
        )
    )
    begin_attempt(service, "run-1", expected_run_version=9, command_id="cmd-begin-2", second=8)
    start_attempt(service, "run-1", expected_run_version=11, command_id="cmd-start-2", second=9)
    attempts = service.repository.load_attempt_history("run-1")
    assert [attempt.attempt_number for attempt in attempts] == [1, 2]


def test_starting_second_active_attempt_is_rejected() -> None:
    service = make_service()
    prepare_running_run(service)
    with pytest.raises(ActiveAttemptExistsError):
        begin_attempt(
            service, "run-1", expected_run_version=6, command_id="cmd-begin-again", second=5
        )


def test_attempt_limit_is_enforced() -> None:
    service = make_service()
    create_run(service, specification=make_spec(max_attempts=1))
    queue_run(service, "run-1", expected_run_version=1)
    claim_run(service, "run-1", expected_run_version=2)
    begin_attempt(service, "run-1", expected_run_version=3)
    start_attempt(service, "run-1", expected_run_version=5)
    service.abandon_attempt(
        AbandonAttemptCommand(
            run_id="run-1",
            command_id="cmd-abandon-first",
            expected_run_version=6,
            timestamp=ts(5),
            actor_reference="worker-1",
            source_metadata={"source": "test"},
        )
    )
    with pytest.raises(RecoveryNotAllowedError):
        service.request_recovery_plan(
            RequestRecoveryPlanCommand(
                run_id="run-1",
                command_id="cmd-plan",
                expected_run_version=7,
                timestamp=ts(6),
                actor_reference="operator-1",
                source_metadata={"source": "test"},
            )
        )


def test_succeeded_attempt_cannot_be_followed_by_another_attempt() -> None:
    service = make_service()
    prepare_running_run(service)
    first_attempt_id = service.repository.load_attempt_history("run-1")[-1].attempt_id
    completed = complete_attempt(
        service, "run-1", expected_run_version=6, attempt_id=first_attempt_id
    )
    assert completed.snapshot is not None
    before_snapshot = service.repository.load_run("run-1")
    before_events = service.repository.list_events("run-1")
    before_attempts = service.repository.load_attempt_history("run-1")
    before_processed = service.repository.get_processed_command("run-1", "cmd-begin-after-success")
    with pytest.raises(InvalidAttemptStateError):
        begin_attempt(
            service,
            "run-1",
            expected_run_version=7,
            command_id="cmd-begin-after-success",
            second=6,
        )
    assert service.repository.load_run("run-1") == before_snapshot
    assert service.repository.list_events("run-1") == before_events
    assert service.repository.load_attempt_history("run-1") == before_attempts
    assert (
        service.repository.get_processed_command("run-1", "cmd-begin-after-success")
        == before_processed
    )
    finalized = service.complete_run(
        CompleteAgentRunCommand(
            run_id="run-1",
            command_id="cmd-complete-run",
            expected_run_version=7,
            timestamp=ts(6),
            actor_reference="worker-1",
            source_metadata={"source": "test"},
        )
    )
    assert finalized.snapshot is not None
    assert finalized.snapshot.state == AgentRunState.SUCCEEDED


def test_terminal_attempts_do_not_become_active_again() -> None:
    service = make_service()
    prepare_running_run(service)
    first_attempt_id = service.repository.load_attempt_history("run-1")[-1].attempt_id
    complete_attempt(service, "run-1", expected_run_version=6, attempt_id=first_attempt_id)
    with pytest.raises(InvalidAttemptStateError):
        service.start_attempt(
            StartAttemptCommand(
                run_id="run-1",
                command_id="cmd-start-completed-attempt",
                expected_run_version=7,
                timestamp=ts(6),
                actor_reference="worker-1",
                attempt_id=first_attempt_id,
                source_metadata={"source": "test"},
            )
        )


def test_run_and_attempt_state_consistency_is_preserved() -> None:
    service = make_service()
    running = prepare_running_run(service)
    assert running.snapshot is not None and running.snapshot.state == AgentRunState.RUNNING
    attempts = service.repository.load_attempt_history("run-1")
    assert attempts[-1].state.value == "running"
    completed = service.complete_attempt(
        CompleteAttemptCommand(
            run_id="run-1",
            command_id="cmd-success-attempt",
            expected_run_version=6,
            timestamp=ts(5),
            actor_reference="worker-1",
            source_metadata={"source": "test"},
        )
    )
    assert completed.snapshot is not None and completed.snapshot.state == AgentRunState.CLAIMED
    attempts = service.repository.load_attempt_history("run-1")
    assert attempts[-1].state.value == "succeeded"


def test_paused_attempt_failure_clears_pause_metadata_and_keeps_recovery_consistent() -> None:
    service = make_service()
    prepare_running_run(service)
    service.record_checkpoint(
        RecordCheckpointCommand(
            run_id="run-1",
            command_id="cmd-checkpoint-before-pause",
            expected_run_version=6,
            timestamp=ts(5),
            actor_reference="worker-1",
            checkpoint_id="checkpoint-before-pause",
            state_reference="checkpoint://before-pause",
            integrity_digest="sha256:aaaaaaaaaaaaaaaa",
            source_metadata={"source": "test"},
        )
    )
    request_pause(service, "run-1", expected_run_version=7, second=6)
    confirm_pause(service, "run-1", expected_run_version=8, second=7)
    failed = service.fail_attempt(
        FailAttemptCommand(
            run_id="run-1",
            command_id="cmd-fail-while-paused",
            expected_run_version=9,
            timestamp=ts(8),
            actor_reference="worker-1",
            failure_category="dependency",
            failure_detail="Dependency unavailable while paused",
            source_metadata={"source": "test"},
        )
    )
    assert failed.snapshot is not None
    assert failed.snapshot.state == AgentRunState.BLOCKED
    assert failed.snapshot.pause_reason is None
    assert failed.snapshot.blocking_reason is not None
    assert failed.snapshot.blocking_reason.code == "recovery_required"
    assert failed.snapshot.active_attempt_id is None
    attempts = service.repository.load_attempt_history("run-1")
    assert attempts[-1].state == AttemptState.FAILED
    assert attempts[-1].outcome.value == "failure"
    recovery = service.request_recovery_plan(
        RequestRecoveryPlanCommand(
            run_id="run-1",
            command_id="cmd-recovery-after-paused-failure",
            expected_run_version=10,
            timestamp=ts(9),
            actor_reference="operator-1",
            source_metadata={"source": "test"},
        )
    )
    assert recovery.recovery_plan is not None
    assert recovery.recovery_plan.selected_checkpoint is not None
    assert recovery.recovery_plan.selected_checkpoint.checkpoint_id == "checkpoint-before-pause"
    replayed = replay_execution_ledger(service.repository.list_events("run-1"))
    assert replayed is not None
    assert replayed.snapshot == recovery.snapshot


@pytest.mark.parametrize(
    ("command", "expected_version", "expected_state"),
    [
        (
            TimeoutAttemptCommand(
                run_id="run-1",
                command_id="cmd-timeout-while-paused",
                expected_run_version=8,
                timestamp=ts(7),
                actor_reference="worker-1",
                source_metadata={"source": "test"},
            ),
            8,
            AttemptState.TIMED_OUT,
        ),
        (
            AbandonAttemptCommand(
                run_id="run-1",
                command_id="cmd-abandon-while-paused",
                expected_run_version=8,
                timestamp=ts(7),
                actor_reference="worker-1",
                source_metadata={"source": "test"},
            ),
            8,
            AttemptState.ABANDONED,
        ),
    ],
)
def test_paused_attempt_terminalization_clears_pause_metadata(
    command,
    expected_version: int,
    expected_state: AttemptState,
) -> None:
    service = make_service()
    prepare_paused_run(service)
    command = command.model_copy(update={"expected_run_version": expected_version})
    result = service.handle(command)
    assert result.snapshot is not None
    assert result.snapshot.state == AgentRunState.BLOCKED
    assert result.snapshot.pause_reason is None
    assert result.snapshot.blocking_reason is not None
    attempts = service.repository.load_attempt_history("run-1")
    assert attempts[-1].state == expected_state
    replayed = replay_execution_ledger(service.repository.list_events("run-1"))
    assert replayed is not None
    assert replayed.snapshot == result.snapshot


def test_pause_requested_attempt_failure_clears_pause_metadata() -> None:
    service = make_service()
    prepare_pause_requested_run(service)
    failed = service.fail_attempt(
        FailAttemptCommand(
            run_id="run-1",
            command_id="cmd-fail-while-pause-requested",
            expected_run_version=7,
            timestamp=ts(6),
            actor_reference="worker-1",
            failure_category="dependency",
            failure_detail="Dependency unavailable before pause confirmed",
            source_metadata={"source": "test"},
        )
    )
    assert failed.snapshot is not None
    assert failed.snapshot.state == AgentRunState.BLOCKED
    assert failed.snapshot.pause_reason is None
    assert failed.snapshot.blocking_reason is not None
    attempts = service.repository.load_attempt_history("run-1")
    assert attempts[-1].state == AttemptState.FAILED
    replayed = replay_execution_ledger(service.repository.list_events("run-1"))
    assert replayed is not None
    assert replayed.snapshot == failed.snapshot


@pytest.mark.parametrize(
    ("command", "expected_attempt_state"),
    [
        (
            FailAttemptCommand(
                run_id="run-1",
                command_id="cmd-fail",
                expected_run_version=6,
                timestamp=ts(5),
                actor_reference="worker-1",
                failure_category="dependency",
                failure_detail="Dependency unavailable",
                source_metadata={"source": "test"},
            ),
            AttemptState.FAILED,
        ),
        (
            TimeoutAttemptCommand(
                run_id="run-1",
                command_id="cmd-timeout",
                expected_run_version=6,
                timestamp=ts(5),
                actor_reference="worker-1",
                source_metadata={"source": "test"},
            ),
            AttemptState.TIMED_OUT,
        ),
        (
            AbandonAttemptCommand(
                run_id="run-1",
                command_id="cmd-abandon",
                expected_run_version=6,
                timestamp=ts(5),
                actor_reference="worker-1",
                source_metadata={"source": "test"},
            ),
            AttemptState.ABANDONED,
        ),
    ],
)
def test_failure_records_preserve_resolved_attempt_id_when_command_omits_attempt_id(
    command,
    expected_attempt_state: AttemptState,
) -> None:
    service = make_service()
    prepare_running_run(service)
    active_attempt_id = service.repository.load_attempt_history("run-1")[-1].attempt_id
    result = service.handle(command)
    assert result.snapshot is not None
    assert result.snapshot.failure is not None
    assert result.snapshot.failure.attempt_id == active_attempt_id
    assert result.snapshot.active_attempt_id is None
    assert result.events[-1].attempt_id == active_attempt_id
    attempts = service.repository.load_attempt_history("run-1")
    assert attempts[-1].attempt_id == active_attempt_id
    assert attempts[-1].state == expected_attempt_state
    replay = service.handle(command)
    assert replay.idempotent_replay is True
    assert replay.snapshot is not None
    assert replay.snapshot.failure is not None
    assert replay.snapshot.failure.attempt_id == active_attempt_id


@pytest.mark.parametrize(
    ("command", "expected_attempt_state"),
    [
        (
            FailAttemptCommand(
                run_id="run-1",
                command_id="cmd-fail-explicit",
                expected_run_version=6,
                timestamp=ts(5),
                actor_reference="worker-1",
                attempt_id="attempt-1",
                failure_category="dependency",
                failure_detail="Dependency unavailable",
                source_metadata={"source": "test"},
            ),
            AttemptState.FAILED,
        ),
        (
            TimeoutAttemptCommand(
                run_id="run-1",
                command_id="cmd-timeout-explicit",
                expected_run_version=6,
                timestamp=ts(5),
                actor_reference="worker-1",
                attempt_id="attempt-1",
                source_metadata={"source": "test"},
            ),
            AttemptState.TIMED_OUT,
        ),
        (
            AbandonAttemptCommand(
                run_id="run-1",
                command_id="cmd-abandon-explicit",
                expected_run_version=6,
                timestamp=ts(5),
                actor_reference="worker-1",
                attempt_id="attempt-1",
                source_metadata={"source": "test"},
            ),
            AttemptState.ABANDONED,
        ),
    ],
)
def test_failure_records_preserve_explicit_attempt_id_when_valid(
    command,
    expected_attempt_state: AttemptState,
) -> None:
    service = make_service()
    prepare_running_run(service)
    result = service.handle(command)
    assert result.snapshot is not None
    assert result.snapshot.failure is not None
    assert result.snapshot.failure.attempt_id == "attempt-1"
    assert result.events[-1].attempt_id == "attempt-1"
    assert service.repository.load_attempt_history("run-1")[-1].state == expected_attempt_state


def test_invalid_explicit_attempt_id_fails_without_mutation() -> None:
    service = make_service()
    prepare_running_run(service)
    before_snapshot = service.repository.load_run("run-1")
    before_events = service.repository.list_events("run-1")
    before_attempts = service.repository.load_attempt_history("run-1")
    with pytest.raises(AttemptNotFoundError):
        service.fail_attempt(
            FailAttemptCommand(
                run_id="run-1",
                command_id="cmd-fail-invalid-attempt",
                expected_run_version=6,
                timestamp=ts(5),
                actor_reference="worker-1",
                attempt_id="attempt-missing",
                failure_category="dependency",
                failure_detail="Dependency unavailable",
                source_metadata={"source": "test"},
            )
        )
    assert service.repository.load_run("run-1") == before_snapshot
    assert service.repository.list_events("run-1") == before_events
    assert service.repository.load_attempt_history("run-1") == before_attempts
