from __future__ import annotations

import pytest

from app.agent_runtime.errors import (
    ActiveAttemptExistsError,
    AttemptLimitExceededError,
    InvalidAttemptStateError,
)
from app.models.agent_runtime import (
    AbandonAttemptCommand,
    AgentRunState,
    CompleteAttemptCommand,
    RequestRecoveryPlanCommand,
    StartAttemptCommand,
    UnblockAgentRunCommand,
)
from tests.agent_runtime_testkit import (
    begin_attempt,
    claim_run,
    complete_attempt,
    create_run,
    make_service,
    make_spec,
    prepare_running_run,
    queue_run,
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
    service.unblock_run(
        UnblockAgentRunCommand(
            run_id="run-1",
            command_id="cmd-unblock",
            expected_run_version=7,
            timestamp=ts(6),
            actor_reference="operator-1",
            source_metadata={"source": "test"},
        )
    )
    with pytest.raises(AttemptLimitExceededError):
        begin_attempt(
            service, "run-1", expected_run_version=8, command_id="cmd-begin-second", second=7
        )


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
