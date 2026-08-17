from __future__ import annotations

import warnings
from datetime import timedelta

import pytest
from pydantic import ValidationError

from app.agent_runtime.errors import (
    InvalidTransitionError,
    LedgerReplayError,
    LedgerSequenceError,
)
from app.agent_runtime.ledger import replay_execution_ledger
from app.models.agent_runtime import (
    AbandonAgentRunCommand,
    AbandonAttemptCommand,
    AgentRunState,
    AttemptState,
    BeginAttemptCommand,
    BlockAgentRunCommand,
    ClaimAgentRunCommand,
    CompleteAgentRunCommand,
    CompleteAttemptCommand,
    ConfirmCancellationCommand,
    ConfirmCancellationStartCommand,
    ConfirmPauseCommand,
    FailAgentRunCommand,
    FailAttemptCommand,
    RecordCheckpointCommand,
    RequestCancellationCommand,
    RequestPauseCommand,
    RequestRecoveryPlanCommand,
    ResumeAgentRunCommand,
    RuntimeCommandResult,
    RuntimeEventEnvelope,
    TimeoutAgentRunCommand,
    TimeoutAttemptCommand,
    UnblockAgentRunCommand,
)
from tests.agent_runtime_testkit import (
    begin_attempt,
    claim_run,
    create_run,
    fail_attempt,
    make_service,
    prepare_blocked_run,
    prepare_running_run,
    queue_run,
    start_attempt,
    ts,
)


def _mutate_attempt_created(
    events: list[RuntimeEventEnvelope],
    *,
    payload_updates: dict[str, object] | None = None,
    event_updates: dict[str, object] | None = None,
    occurrence: int = 0,
) -> list[RuntimeEventEnvelope]:
    mutated = [event.model_copy(deep=True) for event in events]
    indices = [
        position
        for position, event in enumerate(mutated)
        if event.event_type.value == "attempt_created"
    ]
    index = indices[occurrence]
    attempt_payload = dict(mutated[index].payload["attempt"])
    if payload_updates:
        attempt_payload.update(payload_updates)
    update = {"payload": {"attempt": attempt_payload}}
    if event_updates:
        update.update(event_updates)
    mutated[index] = mutated[index].model_copy(update=update)
    return mutated


def _mutate_event_payload(
    events: list[RuntimeEventEnvelope],
    *,
    event_type: str,
    payload_updates: dict[str, object] | None = None,
    replace_payload: dict[str, object] | None = None,
    occurrence: int = 0,
) -> list[RuntimeEventEnvelope]:
    mutated = [event.model_copy(deep=True) for event in events]
    indices = [
        position for position, event in enumerate(mutated) if event.event_type.value == event_type
    ]
    index = indices[occurrence]
    payload = dict(mutated[index].payload)
    if replace_payload is not None:
        payload = replace_payload
    elif payload_updates is not None:
        payload.update(payload_updates)
    mutated[index] = mutated[index].model_copy(update={"payload": payload})
    return mutated


def _prepare_recovery_planned_run() -> tuple[object, list[RuntimeEventEnvelope]]:
    service = make_service()
    prepare_running_run(service)
    service.record_checkpoint(
        RecordCheckpointCommand(
            run_id="run-1",
            command_id="cmd-checkpoint-1",
            expected_run_version=6,
            timestamp=ts(5),
            actor_reference="worker-1",
            checkpoint_id="checkpoint-1",
            state_reference="checkpoint://state/1",
            integrity_digest="sha256:aaaaaaaaaaaaaaaa",
            source_metadata={"source": "test"},
        )
    )
    fail_attempt(service, "run-1", expected_run_version=7, command_id="cmd-fail-1", second=6)
    planned = service.request_recovery_plan(
        RequestRecoveryPlanCommand(
            run_id="run-1",
            command_id="cmd-plan-1",
            expected_run_version=8,
            timestamp=ts(7),
            actor_reference="operator-1",
            source_metadata={"source": "test"},
        )
    )
    return planned, service.repository.list_events("run-1")


def _prepare_claimed_after_failed_attempt_events() -> list[RuntimeEventEnvelope]:
    service = make_service()
    prepare_running_run(service)
    service.record_checkpoint(
        RecordCheckpointCommand(
            run_id="run-1",
            command_id="cmd-checkpoint-1",
            expected_run_version=6,
            timestamp=ts(5),
            actor_reference="worker-1",
            checkpoint_id="checkpoint-1",
            state_reference="checkpoint://state/1",
            integrity_digest="sha256:aaaaaaaaaaaaaaaa",
            source_metadata={"source": "test"},
        )
    )
    fail_attempt(service, "run-1", expected_run_version=7, command_id="cmd-fail-1", second=6)
    service.request_recovery_plan(
        RequestRecoveryPlanCommand(
            run_id="run-1",
            command_id="cmd-plan-1",
            expected_run_version=8,
            timestamp=ts(7),
            actor_reference="operator-1",
            source_metadata={"source": "test"},
        )
    )
    service.unblock_run(
        UnblockAgentRunCommand(
            run_id="run-1",
            command_id="cmd-unblock-1",
            expected_run_version=9,
            timestamp=ts(8),
            actor_reference="operator-1",
            source_metadata={"source": "test"},
        )
    )
    return service.repository.list_events("run-1")


def _prepare_run_succeeded_events() -> list[RuntimeEventEnvelope]:
    service = make_service()
    prepare_running_run(service)
    service.complete_attempt(
        CompleteAttemptCommand(
            run_id="run-1",
            command_id="cmd-attempt-succeeded",
            expected_run_version=6,
            timestamp=ts(5),
            actor_reference="worker-1",
            source_metadata={"source": "test"},
        )
    )
    service.complete_run(
        CompleteAgentRunCommand(
            run_id="run-1",
            command_id="cmd-run-succeeded",
            expected_run_version=7,
            timestamp=ts(6),
            actor_reference="worker-1",
            source_metadata={"source": "test"},
        )
    )
    return service.repository.list_events("run-1")


def _prepare_run_terminal_failure_events(event_type: str) -> list[RuntimeEventEnvelope]:
    service = make_service()
    prepare_running_run(service)
    if event_type == "run_failed":
        service.fail_attempt(
            FailAttemptCommand(
                run_id="run-1",
                command_id="cmd-attempt-failed",
                expected_run_version=6,
                timestamp=ts(5),
                actor_reference="worker-1",
                failure_category="dependency",
                failure_detail="Dependency unavailable",
                source_metadata={"source": "test"},
            )
        )
        service.fail_run(
            FailAgentRunCommand(
                run_id="run-1",
                command_id="cmd-run-failed",
                expected_run_version=7,
                timestamp=ts(6),
                actor_reference="worker-1",
                failure_category="dependency",
                failure_detail="Dependency unavailable",
                source_metadata={"source": "test"},
            )
        )
    elif event_type == "run_timed_out":
        service.timeout_attempt(
            TimeoutAttemptCommand(
                run_id="run-1",
                command_id="cmd-attempt-timeout",
                expected_run_version=6,
                timestamp=ts(5),
                actor_reference="worker-1",
                source_metadata={"source": "test"},
            )
        )
        service.timeout_run(
            TimeoutAgentRunCommand(
                run_id="run-1",
                command_id="cmd-run-timeout",
                expected_run_version=7,
                timestamp=ts(6),
                actor_reference="worker-1",
                source_metadata={"source": "test"},
            )
        )
    else:
        service.abandon_attempt(
            AbandonAttemptCommand(
                run_id="run-1",
                command_id="cmd-attempt-abandon",
                expected_run_version=6,
                timestamp=ts(5),
                actor_reference="worker-1",
                source_metadata={"source": "test"},
            )
        )
        service.abandon_run(
            AbandonAgentRunCommand(
                run_id="run-1",
                command_id="cmd-run-abandon",
                expected_run_version=7,
                timestamp=ts(6),
                actor_reference="worker-1",
                source_metadata={"source": "test"},
            )
        )
    return service.repository.list_events("run-1")


def _prepare_pause_requested_events() -> list[RuntimeEventEnvelope]:
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
            detail="Pause the run",
            source_metadata={"source": "test"},
        )
    )
    return service.repository.list_events("run-1")


def _prepare_preexecution_resumed_events() -> list[RuntimeEventEnvelope]:
    service = make_service()
    create_run(service)
    queue_run(service, "run-1", expected_run_version=1)
    service.request_pause(
        RequestPauseCommand(
            run_id="run-1",
            command_id="cmd-pause-request-pre",
            expected_run_version=2,
            timestamp=ts(2),
            actor_reference="operator-1",
            reason_code="operator_pause",
            detail="Pause before claim",
            source_metadata={"source": "test"},
        )
    )
    service.confirm_pause(
        ConfirmPauseCommand(
            run_id="run-1",
            command_id="cmd-pause-confirm-pre",
            expected_run_version=3,
            timestamp=ts(3),
            actor_reference="operator-1",
            source_metadata={"source": "test"},
        )
    )
    service.resume_run(
        ResumeAgentRunCommand(
            run_id="run-1",
            command_id="cmd-resume-pre",
            expected_run_version=4,
            timestamp=ts(4),
            actor_reference="operator-1",
            source_metadata={"source": "test"},
        )
    )
    return service.repository.list_events("run-1")


def _prepare_resumed_run_events() -> list[RuntimeEventEnvelope]:
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
            detail="Pause the run",
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
    return service.repository.list_events("run-1")


def _prepare_blocked_active_run_events() -> list[RuntimeEventEnvelope]:
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
            detail="Blocked for approval",
            source_metadata={"source": "test"},
        )
    )
    return service.repository.list_events("run-1")


def _prepare_unblocked_run_events() -> list[RuntimeEventEnvelope]:
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
            detail="Blocked for approval",
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
    return service.repository.list_events("run-1")


def _prepare_cancellation_request_events() -> list[RuntimeEventEnvelope]:
    service = make_service()
    prepare_running_run(service)
    service.request_cancellation(
        RequestCancellationCommand(
            run_id="run-1",
            command_id="cmd-cancel-request-1",
            expected_run_version=6,
            timestamp=ts(5),
            actor_reference="operator-1",
            requester_reference="operator-1",
            reason_code="operator_cancel",
            detail="Stop the run",
            source_metadata={"source": "test"},
        )
    )
    return service.repository.list_events("run-1")


def _prepare_cancelled_run_events() -> list[RuntimeEventEnvelope]:
    service = make_service()
    prepare_running_run(service)
    service.request_cancellation(
        RequestCancellationCommand(
            run_id="run-1",
            command_id="cmd-cancel-request-1",
            expected_run_version=6,
            timestamp=ts(5),
            actor_reference="operator-1",
            requester_reference="operator-1",
            reason_code="operator_cancel",
            detail="Stop the run",
            source_metadata={"source": "test"},
        )
    )
    service.confirm_cancellation_start(
        ConfirmCancellationStartCommand(
            run_id="run-1",
            command_id="cmd-cancel-start-1",
            expected_run_version=7,
            timestamp=ts(6),
            actor_reference="worker-1",
            source_metadata={"source": "test"},
        )
    )
    service.confirm_cancellation(
        ConfirmCancellationCommand(
            run_id="run-1",
            command_id="cmd-cancel-confirm-1",
            expected_run_version=8,
            timestamp=ts(7),
            actor_reference="worker-1",
            source_metadata={"source": "test"},
        )
    )
    return service.repository.list_events("run-1")


def _prepare_checkpoint_events() -> list[RuntimeEventEnvelope]:
    service = make_service()
    prepare_running_run(service)
    service.record_checkpoint(
        RecordCheckpointCommand(
            run_id="run-1",
            command_id="cmd-checkpoint-1",
            expected_run_version=6,
            timestamp=ts(5),
            actor_reference="worker-1",
            checkpoint_id="checkpoint-1",
            state_reference="checkpoint://state/1",
            integrity_digest="sha256:aaaaaaaaaaaaaaaa",
            source_metadata={"source": "test"},
        )
    )
    return service.repository.list_events("run-1")


def _prepare_blocked_run_events() -> list[RuntimeEventEnvelope]:
    service = make_service()
    prepare_blocked_run(service)
    return service.repository.list_events("run-1")


def _prepare_created_run_events() -> list[RuntimeEventEnvelope]:
    service = make_service()
    create_run(service)
    return service.repository.list_events("run-1")


def _prepare_queued_run_events() -> list[RuntimeEventEnvelope]:
    service = make_service()
    create_run(service)
    queue_run(service, "run-1", expected_run_version=1)
    return service.repository.list_events("run-1")


def _prepare_claimed_run_events() -> list[RuntimeEventEnvelope]:
    service = make_service()
    create_run(service)
    queue_run(service, "run-1", expected_run_version=1)
    claim_run(service, "run-1", expected_run_version=2)
    return service.repository.list_events("run-1")


def _prepare_running_run_events() -> list[RuntimeEventEnvelope]:
    service = make_service()
    prepare_running_run(service)
    return service.repository.list_events("run-1")


def test_ledger_sequence_begins_at_one_and_is_contiguous() -> None:
    service = make_service()
    prepare_running_run(service)
    events = service.repository.list_events("run-1")
    assert [event.sequence_number for event in events] == [1, 2, 3, 4, 5, 6]
    assert [event.run_version for event in events] == [1, 2, 3, 4, 5, 6]


def test_replay_reproduces_same_snapshot_as_command_execution() -> None:
    service = make_service()
    result = prepare_blocked_run(service)
    events = service.repository.list_events("run-1")
    replayed = replay_execution_ledger(events)
    assert replayed is not None
    assert result.snapshot == replayed.snapshot


def test_replay_rejects_sequence_gaps() -> None:
    service = make_service()
    prepare_running_run(service)
    events = service.repository.list_events("run-1")
    broken = [events[0], events[1].model_copy(update={"sequence_number": 3})]
    with pytest.raises(LedgerSequenceError):
        replay_execution_ledger(broken)


def test_replay_rejects_duplicate_sequences() -> None:
    service = make_service()
    prepare_running_run(service)
    events = service.repository.list_events("run-1")
    broken = [events[0], events[1].model_copy(update={"sequence_number": 1})]
    with pytest.raises(LedgerSequenceError):
        replay_execution_ledger(broken)


def test_replay_rejects_backwards_timestamps() -> None:
    service = make_service()
    prepare_running_run(service)
    events = service.repository.list_events("run-1")
    broken = [events[0], events[1].model_copy(update={"timestamp": events[0].timestamp})]
    replayed = replay_execution_ledger(broken)
    assert replayed is not None
    broken = [
        events[0],
        events[1].model_copy(update={"timestamp": events[0].timestamp - timedelta(seconds=1)}),
    ]
    with pytest.raises(LedgerReplayError):
        replay_execution_ledger(broken)


def test_replay_rejects_invalid_transition() -> None:
    service = make_service()
    prepare_running_run(service)
    events = service.repository.list_events("run-1")
    invalid = events[2].model_copy(update={"event_type": "run_succeeded"})
    with pytest.raises(Exception) as exc_info:
        replay_execution_ledger([events[0], events[1], invalid])
    assert getattr(exc_info.value, "code", None) in {"invalid_transition", "ledger_replay_error"}


def test_terminal_event_enforcement_rejects_follow_on_changes() -> None:
    service = make_service()
    result = prepare_blocked_run(service)
    assert result.snapshot is not None and result.snapshot.state == AgentRunState.BLOCKED
    events = service.repository.list_events("run-1")
    terminal = events + [
        RuntimeEventEnvelope.model_validate(
            events[-1].model_dump(mode="json")
            | {
                "event_id": "event-terminal",
                "event_type": "run_failed",
                "sequence_number": 8,
                "run_version": 8,
                "attempt_id": None,
                "payload": {
                    "failure": {
                        "category": "internal",
                        "detail": "Run failed permanently",
                        "timestamp": events[-1].timestamp.isoformat(),
                        "metadata": {},
                    }
                },
            }
        ),
        RuntimeEventEnvelope.model_validate(
            events[-1].model_dump(mode="json")
            | {
                "event_id": "event-after-terminal",
                "event_type": "run_queued",
                "sequence_number": 9,
                "run_version": 9,
                "payload": {"detail": "illegal"},
            }
        ),
    ]
    with pytest.raises(LedgerReplayError):
        replay_execution_ledger(terminal)


def test_replay_rejects_mismatched_run_ids() -> None:
    service = make_service()
    prepare_running_run(service)
    events = service.repository.list_events("run-1")
    broken = [events[0], events[1].model_copy(update={"run_id": "run-2"})]
    with pytest.raises(LedgerReplayError):
        replay_execution_ledger(broken)


def test_replay_rejects_incompatible_schema_version() -> None:
    service = make_service()
    prepare_running_run(service)
    events = service.repository.list_events("run-1")
    broken = [events[0].model_copy(update={"event_schema_version": "2.0"})]
    with pytest.raises(LedgerReplayError):
        replay_execution_ledger(broken)


@pytest.mark.parametrize(
    ("event_type", "events_builder"),
    [
        ("run_queued", _prepare_queued_run_events),
        ("run_claimed", _prepare_claimed_run_events),
        ("run_start_requested", _prepare_running_run_events),
        ("run_unblocked", _prepare_unblocked_run_events),
    ],
)
@pytest.mark.parametrize(
    "detail",
    [42, "   ", "x" * 2001, "bad\nvalue", "Bearer secret-token-value"],
)
def test_replay_rejects_invalid_detail_payloads(
    event_type: str, events_builder, detail: object
) -> None:
    events = _mutate_event_payload(
        events_builder(),
        event_type=event_type,
        payload_updates={"detail": detail},
    )
    with pytest.raises(LedgerReplayError) as exc_info:
        replay_execution_ledger(events)
    assert exc_info.value.code == "ledger_replay_error"
    assert exc_info.value.metadata["eventType"] == event_type
    assert exc_info.value.metadata["payloadSection"] == "detail"
    assert "secret-token-value" not in str(exc_info.value.metadata)


def test_replay_rejects_malformed_payloads_without_emitting_serialization_warnings() -> None:
    events = _mutate_event_payload(
        _prepare_queued_run_events(),
        event_type="run_queued",
        payload_updates={"detail": 42},
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with pytest.raises(LedgerReplayError):
            replay_execution_ledger(events)
    assert caught == []


@pytest.mark.parametrize("bad_value", [None, "", "   ", "bad\nvalue", "x" * 161])
def test_replay_rejects_invalid_required_executor_reference(bad_value: object) -> None:
    for event_type, events in (
        ("run_claimed", _prepare_claimed_run_events()),
        ("run_start_requested", _prepare_running_run_events()),
    ):
        broken = _mutate_event_payload(
            events,
            event_type=event_type,
            payload_updates={"executor_reference": bad_value},
        )
        with pytest.raises(LedgerReplayError) as exc_info:
            replay_execution_ledger(broken)
        assert exc_info.value.metadata["eventType"] == event_type
        assert exc_info.value.metadata["payloadSection"] == "executor_reference"


@pytest.mark.parametrize("event_type", ["run_claimed", "run_start_requested"])
def test_replay_rejects_missing_required_executor_reference(event_type: str) -> None:
    events = _mutate_event_payload(
        {
            "run_claimed": _prepare_claimed_run_events(),
            "run_start_requested": _prepare_running_run_events(),
        }[event_type],
        event_type=event_type,
        replace_payload={"detail": "valid detail"},
    )
    with pytest.raises(LedgerReplayError) as exc_info:
        replay_execution_ledger(events)
    assert exc_info.value.metadata["payloadSection"] == "executor_reference"


def test_service_and_replay_agree_on_invalid_executor_reference() -> None:
    with pytest.raises(ValidationError):
        ClaimAgentRunCommand(
            run_id="run-1",
            command_id="cmd-claim-invalid",
            expected_run_version=1,
            timestamp=ts(1),
            actor_reference="scheduler-1",
            executor_reference="   ",
            source_metadata={"source": "test"},
        )
    with pytest.raises(ValidationError):
        BeginAttemptCommand(
            run_id="run-1",
            command_id="cmd-begin-invalid",
            expected_run_version=3,
            timestamp=ts(3),
            actor_reference="worker-1",
            executor_reference="   ",
            source_metadata={"source": "test"},
        )
    broken_claim = _mutate_event_payload(
        _prepare_claimed_run_events(),
        event_type="run_claimed",
        payload_updates={"executor_reference": "   "},
    )
    with pytest.raises(LedgerReplayError):
        replay_execution_ledger(broken_claim)
    broken_start = _mutate_event_payload(
        _prepare_running_run_events(),
        event_type="run_start_requested",
        payload_updates={"executor_reference": "   "},
    )
    with pytest.raises(LedgerReplayError):
        replay_execution_ledger(broken_start)


def test_replay_accepts_valid_required_executor_reference() -> None:
    assert replay_execution_ledger(_prepare_claimed_run_events()) is not None
    assert replay_execution_ledger(_prepare_running_run_events()) is not None


def test_repository_append_rejects_attempt_created_after_success_without_mutation() -> None:
    service = make_service()
    prepare_running_run(service)
    service.complete_attempt(
        CompleteAttemptCommand(
            run_id="run-1",
            command_id="cmd-attempt-succeeded-append",
            expected_run_version=6,
            timestamp=ts(5),
            actor_reference="worker-1",
            source_metadata={"source": "test"},
        )
    )
    service.complete_run(
        CompleteAgentRunCommand(
            run_id="run-1",
            command_id="cmd-run-succeeded-append",
            expected_run_version=7,
            timestamp=ts(6),
            actor_reference="worker-1",
            source_metadata={"source": "test"},
        )
    )
    before_snapshot = service.repository.load_run("run-1")
    before_events = service.repository.list_events("run-1")
    invalid_attempt = RuntimeEventEnvelope.model_validate(
        before_events[-1].model_dump(mode="json")
        | {
            "event_id": "event-attempt-after-success-append",
            "event_type": "attempt_created",
            "sequence_number": len(before_events) + 1,
            "run_version": len(before_events) + 1,
            "attempt_id": "attempt-2",
            "timestamp": ts(7).isoformat(),
            "payload": {
                "attempt": {
                    "attempt_id": "attempt-2",
                    "run_id": "run-1",
                    "attempt_number": 2,
                    "state": "starting",
                    "started_at": ts(7).isoformat(),
                    "executor_reference": "worker-1",
                    "version": len(before_events) + 1,
                }
            },
        }
    )
    with pytest.raises(LedgerReplayError):
        service.repository.append_events(
            "run-1", [invalid_attempt], expected_sequence=len(before_events)
        )
    assert service.repository.load_run("run-1") == before_snapshot
    assert service.repository.list_events("run-1") == before_events


def test_replay_rejects_invalid_resume_and_unblock_target_states() -> None:
    with pytest.raises(LedgerReplayError):
        replay_execution_ledger(
            _mutate_event_payload(
                _prepare_resumed_run_events(),
                event_type="run_resumed",
                payload_updates={"target_state": "not-a-state"},
            )
        )
    with pytest.raises(LedgerReplayError):
        replay_execution_ledger(
            _mutate_event_payload(
                _prepare_unblocked_run_events(),
                event_type="run_unblocked",
                payload_updates={"target_state": 42},
            )
        )


def test_replay_rejects_active_cancellation_that_skips_cancelling() -> None:
    events = _prepare_cancellation_request_events()
    broken = events + [
        RuntimeEventEnvelope.model_validate(
            events[-1].model_dump(mode="json")
            | {
                "event_id": "event-cancel-without-cancelling",
                "event_type": "run_cancelled",
                "sequence_number": 8,
                "run_version": 8,
                "attempt_id": events[-1].attempt_id,
                "timestamp": ts(6).isoformat(),
                "payload": {"detail": "Cancelled without cancelling"},
            }
        )
    ]
    with pytest.raises(InvalidTransitionError):
        replay_execution_ledger(broken)


def test_replay_cancels_the_attempt_identified_by_the_event() -> None:
    events = _prepare_cancelled_run_events()
    active_attempt_id = events[-1].attempt_id
    replayed = replay_execution_ledger(events)
    assert replayed is not None
    assert active_attempt_id is not None
    assert replayed.snapshot.active_attempt_id is None
    assert replayed.attempts[-1].attempt_id == active_attempt_id
    assert replayed.attempts[-1].state == AttemptState.CANCELLED


@pytest.mark.parametrize(
    ("attempt_id", "reason"),
    [
        (None, "cancellation_attempt_missing"),
        ("attempt-unrelated", "cancellation_attempt_mismatch"),
        ("bad\nattempt", "cancellation_attempt_invalid"),
    ],
)
def test_replay_rejects_invalid_active_cancellation_attempt_lineage(
    attempt_id: str | None,
    reason: str,
) -> None:
    events = _prepare_cancelled_run_events()
    broken = [event.model_copy(deep=True) for event in events]
    broken[-1] = broken[-1].model_copy(update={"attempt_id": attempt_id})
    with pytest.raises(LedgerReplayError) as exc_info:
        replay_execution_ledger(broken)
    assert exc_info.value.code == "ledger_replay_error"
    assert exc_info.value.metadata["eventType"] == "run_cancelled"
    assert exc_info.value.metadata["reason"] == reason


def test_invalid_cancellation_commit_is_atomic() -> None:
    service = make_service()
    prepare_running_run(service)
    service.request_cancellation(
        RequestCancellationCommand(
            run_id="run-1",
            command_id="cmd-cancel-request-1",
            expected_run_version=6,
            timestamp=ts(5),
            actor_reference="operator-1",
            requester_reference="operator-1",
            reason_code="operator_cancel",
            detail="Stop the run",
            source_metadata={"source": "test"},
        )
    )
    service.confirm_cancellation_start(
        ConfirmCancellationStartCommand(
            run_id="run-1",
            command_id="cmd-cancel-start-1",
            expected_run_version=7,
            timestamp=ts(6),
            actor_reference="worker-1",
            source_metadata={"source": "test"},
        )
    )
    before_snapshot = service.repository.load_run("run-1")
    before_events = service.repository.list_events("run-1")
    before_attempts = service.repository.load_attempt_history("run-1")
    assert before_snapshot is not None
    command = ConfirmCancellationCommand(
        run_id="run-1",
        command_id="cmd-cancel-invalid",
        expected_run_version=before_snapshot.version,
        timestamp=ts(7),
        actor_reference="worker-1",
        source_metadata={"source": "test"},
    )
    event = RuntimeEventEnvelope(
        event_id="event-cancel-invalid",
        event_type="run_cancelled",
        run_id="run-1",
        attempt_id="attempt-unrelated",
        sequence_number=before_snapshot.event_sequence_number + 1,
        run_version=before_snapshot.version + 1,
        timestamp=command.timestamp,
        actor_reference=command.actor_reference,
        command_id=command.command_id,
        correlation_id="run-1",
        causation_id=command.command_id,
        payload={"detail": command.detail},
        metadata=command.source_metadata,
    )
    record = service._processed_record(
        "run-1",
        command.command_id,
        command,
        RuntimeCommandResult(run_id="run-1", snapshot=before_snapshot, events=()),
    )
    with pytest.raises(LedgerReplayError):
        service.repository.commit_command(
            snapshot=before_snapshot,
            events=(event,),
            processed_command=record,
            expected_version=before_snapshot.version,
            expected_sequence=before_snapshot.event_sequence_number,
        )
    assert service.repository.load_run("run-1") == before_snapshot
    assert service.repository.list_events("run-1") == before_events
    assert service.repository.load_attempt_history("run-1") == before_attempts
    assert service.repository.get_processed_command("run-1", command.command_id) is None


def test_prestart_cancellation_attempt_lineage() -> None:
    service = make_service()
    create_run(service)
    queue_run(service, "run-1", expected_run_version=1)
    result = service.request_cancellation(
        RequestCancellationCommand(
            run_id="run-1",
            command_id="cmd-cancel-prestart",
            expected_run_version=2,
            timestamp=ts(2),
            actor_reference="operator-1",
            requester_reference="operator-1",
            reason_code="operator_cancel",
            detail="Stop before execution",
            source_metadata={"source": "test"},
        )
    )
    assert result.events[-1].attempt_id is None
    events = service.repository.list_events("run-1")
    replayed = replay_execution_ledger(events)
    assert replayed is not None and replayed.snapshot.state == AgentRunState.CANCELLED
    broken = [event.model_copy(deep=True) for event in events]
    broken[-1] = broken[-1].model_copy(update={"attempt_id": "attempt-unrelated"})
    with pytest.raises(LedgerReplayError) as exc_info:
        replay_execution_ledger(broken)
    assert exc_info.value.metadata["reason"] == "cancellation_attempt_unexpected"


def test_replay_rejects_resume_target_mismatching_pause_reason() -> None:
    events = _mutate_event_payload(
        _prepare_resumed_run_events(),
        event_type="run_resumed",
        payload_updates={"target_state": AgentRunState.CLAIMED.value},
    )
    with pytest.raises(LedgerReplayError):
        replay_execution_ledger(events)


def test_replay_rejects_preexecution_resume_target_mismatch() -> None:
    events = _mutate_event_payload(
        _prepare_preexecution_resumed_events(),
        event_type="run_resumed",
        payload_updates={"target_state": AgentRunState.RUNNING.value},
    )
    with pytest.raises(LedgerReplayError):
        replay_execution_ledger(events)


def test_replay_rejects_unblock_target_mismatching_blocking_reason() -> None:
    events = _mutate_event_payload(
        _prepare_unblocked_run_events(),
        event_type="run_unblocked",
        payload_updates={"target_state": AgentRunState.CLAIMED.value},
    )
    with pytest.raises(LedgerReplayError):
        replay_execution_ledger(events)


def test_repository_append_rejects_invalid_unblock_target_without_mutation() -> None:
    service = make_service()
    prepare_running_run(service)
    service.block_run(
        BlockAgentRunCommand(
            run_id="run-1",
            command_id="cmd-block-for-append",
            expected_run_version=6,
            timestamp=ts(5),
            actor_reference="operator-1",
            block_code="waiting_for_approval",
            detail="Blocked for approval",
            source_metadata={"source": "test"},
        )
    )
    before_snapshot = service.repository.load_run("run-1")
    before_events = service.repository.list_events("run-1")
    invalid_unblock = RuntimeEventEnvelope.model_validate(
        before_events[-1].model_dump(mode="json")
        | {
            "event_id": "event-invalid-unblock",
            "event_type": "run_unblocked",
            "sequence_number": 8,
            "run_version": 8,
            "payload": {"target_state": AgentRunState.CLAIMED.value, "detail": "Invalid"},
        }
    )
    with pytest.raises(LedgerReplayError):
        service.repository.append_events("run-1", [invalid_unblock], expected_sequence=7)
    assert service.repository.load_run("run-1") == before_snapshot
    assert service.repository.list_events("run-1") == before_events


@pytest.mark.parametrize(
    ("events_builder", "event_type", "replace_payload", "section"),
    [
        (
            _prepare_created_run_events,
            "run_created",
            {"specification": 42, "detail": "Run created"},
            "specification",
        ),
        (
            _prepare_claimed_run_events,
            "run_claimed",
            {"detail": "Claimed for execution", "executor_reference": 42},
            "executor_reference",
        ),
        (_prepare_running_run_events, "attempt_created", {"attempt": 42}, "attempt"),
        (_prepare_resumed_run_events, "pause_requested", {"pause": 42}, "pause"),
        (_prepare_unblocked_run_events, "run_blocked", {"block": 42}, "block"),
        (
            _prepare_cancellation_request_events,
            "cancellation_requested",
            {"cancellation": 42},
            "cancellation",
        ),
        (_prepare_checkpoint_events, "checkpoint_recorded", {"checkpoint": 42}, "checkpoint"),
        (
            _prepare_blocked_run_events,
            "attempt_failed",
            {"failure": 42, "blocking_reason": 42},
            "failure",
        ),
        (
            lambda: _prepare_recovery_planned_run()[1],
            "recovery_planned",
            {"plan": 42},
            "plan",
        ),
    ],
)
def test_replay_rejects_invalid_payload_sections(
    events_builder,
    event_type: str,
    replace_payload: dict[str, object],
    section: str,
) -> None:
    events = _mutate_event_payload(
        events_builder(),
        event_type=event_type,
        replace_payload=replace_payload,
    )
    with pytest.raises(LedgerReplayError) as exc_info:
        replay_execution_ledger(events)
    assert exc_info.value.code == "ledger_replay_error"
    assert exc_info.value.metadata["eventType"] == event_type
    assert exc_info.value.metadata["payloadSection"] == section


def test_replay_accepts_valid_extra_safe_payload_metadata() -> None:
    events = _mutate_event_payload(
        _prepare_pause_requested_events(),
        event_type="pause_requested",
        replace_payload={
            "pause": {
                "code": "operator_pause",
                "detail": "Pause the run",
                "timestamp": ts(5).isoformat(),
                "requested_by": "operator-1",
                "resume_state": "running",
                "metadata": {"note": "safe"},
            }
        },
    )
    replayed = replay_execution_ledger(events)
    assert replayed is not None
    assert replayed.snapshot.pause_reason is not None
    assert replayed.snapshot.pause_reason.metadata["note"] == "safe"


def test_replay_rejects_malformed_failure_and_blocking_reason_payloads() -> None:
    failure_events = _mutate_event_payload(
        _prepare_blocked_run_events(),
        event_type="attempt_failed",
        replace_payload={"failure": 42, "blocking_reason": {}},
    )
    with pytest.raises(LedgerReplayError) as failure_exc:
        replay_execution_ledger(failure_events)
    assert failure_exc.value.metadata["payloadSection"] == "failure"

    blocking_events = _mutate_event_payload(
        _prepare_blocked_run_events(),
        event_type="attempt_failed",
        replace_payload={
            "failure": {
                "category": "dependency",
                "detail": "Dependency unavailable",
                "timestamp": ts(5).isoformat(),
                "attempt_id": "attempt-1",
                "metadata": {},
            },
            "blocking_reason": 42,
        },
    )
    with pytest.raises(LedgerReplayError) as block_exc:
        replay_execution_ledger(blocking_events)
    assert block_exc.value.metadata["payloadSection"] == "blocking_reason"


@pytest.mark.parametrize(
    ("event_type", "replacement"),
    [
        (
            "attempt_failed",
            {
                "failure": {
                    "category": "dependency",
                    "detail": "Dependency unavailable",
                    "timestamp": ts(5).isoformat(),
                    "attempt_id": "attempt-1",
                    "metadata": {},
                },
                "blocking_reason": {
                    "code": "wrong_code",
                    "detail": "Invalid block",
                    "timestamp": ts(5).isoformat(),
                    "resume_state": AgentRunState.CLAIMED.value,
                    "metadata": {},
                },
            },
        ),
        (
            "attempt_timed_out",
            {
                "failure": {
                    "category": "timeout",
                    "detail": "Attempt timed out",
                    "timestamp": ts(5).isoformat(),
                    "attempt_id": "attempt-1",
                    "metadata": {},
                },
                "blocking_reason": {
                    "code": "recovery_required",
                    "detail": "Recovery required",
                    "timestamp": ts(5).isoformat(),
                    "resume_state": AgentRunState.RUNNING.value,
                    "metadata": {},
                },
            },
        ),
        (
            "attempt_abandoned",
            {
                "failure": {
                    "category": "internal",
                    "detail": "Attempt abandoned",
                    "timestamp": ts(5).isoformat(),
                    "attempt_id": "attempt-1",
                    "metadata": {},
                },
                "blocking_reason": {
                    "code": "recovery_required",
                    "detail": "Recovery required",
                    "timestamp": ts(5).isoformat(),
                    "resume_state": AgentRunState.PAUSED.value,
                    "metadata": {},
                },
            },
        ),
    ],
)
def test_replay_rejects_invalid_recovery_block_semantics(
    event_type: str,
    replacement: dict[str, object],
) -> None:
    source_events = {
        "attempt_failed": _prepare_blocked_run_events(),
        "attempt_timed_out": _prepare_run_terminal_failure_events("run_timed_out")[:-1],
        "attempt_abandoned": _prepare_run_terminal_failure_events("run_abandoned")[:-1],
    }[event_type]
    invalid_events = _mutate_event_payload(
        source_events,
        event_type=event_type,
        replace_payload=replacement,
    )
    with pytest.raises(LedgerReplayError) as exc_info:
        replay_execution_ledger(invalid_events)
    assert exc_info.value.metadata["payloadSection"] == "blocking_reason"


@pytest.mark.parametrize(
    ("event_type", "failure_attempt_id"),
    [
        ("attempt_failed", None),
        ("attempt_failed", "attempt-older"),
        ("attempt_timed_out", "attempt-older"),
        ("attempt_abandoned", "attempt-missing"),
    ],
)
def test_replay_rejects_failure_records_not_referencing_active_attempt(
    event_type: str,
    failure_attempt_id: str | None,
) -> None:
    source_events = {
        "attempt_failed": _prepare_blocked_run_events(),
        "attempt_timed_out": _prepare_run_terminal_failure_events("run_timed_out")[:-1],
        "attempt_abandoned": _prepare_run_terminal_failure_events("run_abandoned")[:-1],
    }[event_type]
    invalid_events = _mutate_event_payload(
        source_events,
        event_type=event_type,
        replace_payload={
            "failure": {
                "category": {
                    "attempt_failed": "dependency",
                    "attempt_timed_out": "timeout",
                    "attempt_abandoned": "internal",
                }[event_type],
                "detail": {
                    "attempt_failed": "Dependency unavailable",
                    "attempt_timed_out": "Attempt timed out",
                    "attempt_abandoned": "Attempt abandoned",
                }[event_type],
                "timestamp": ts(5).isoformat(),
                "attempt_id": failure_attempt_id,
                "metadata": {},
            },
            "blocking_reason": {
                "code": "recovery_required",
                "detail": "Recovery required",
                "timestamp": ts(5).isoformat(),
                "resume_state": AgentRunState.CLAIMED.value,
                "metadata": {},
            },
        },
    )
    with pytest.raises(LedgerReplayError) as exc_info:
        replay_execution_ledger(invalid_events)
    assert exc_info.value.metadata["payloadSection"] == "failure"


@pytest.mark.parametrize("event_type", ["attempt_failed", "attempt_timed_out", "attempt_abandoned"])
def test_repository_append_rejects_invalid_recovery_blocks_without_mutation(
    event_type: str,
) -> None:
    service = make_service()
    prepare_running_run(service)
    before_snapshot = service.repository.load_run("run-1")
    before_events = service.repository.list_events("run-1")
    before_attempts = service.repository.load_attempt_history("run-1")
    invalid_event = RuntimeEventEnvelope.model_validate(
        before_events[-1].model_dump(mode="json")
        | {
            "event_id": f"event-{event_type}",
            "event_type": event_type,
            "sequence_number": len(before_events) + 1,
            "run_version": len(before_events) + 1,
            "attempt_id": before_events[-1].attempt_id,
            "timestamp": ts(99).isoformat(),
            "payload": {
                "failure": {
                    "category": "dependency",
                    "detail": "Dependency unavailable",
                    "timestamp": ts(99).isoformat(),
                    "attempt_id": "attempt-1",
                    "metadata": {},
                },
                "blocking_reason": {
                    "code": "wrong_code",
                    "detail": "Invalid block",
                    "timestamp": ts(99).isoformat(),
                    "resume_state": AgentRunState.CLAIMED.value,
                    "metadata": {},
                },
            },
        }
    )
    with pytest.raises(LedgerReplayError):
        service.repository.append_events(
            "run-1", [invalid_event], expected_sequence=len(before_events)
        )
    assert service.repository.load_run("run-1") == before_snapshot
    assert service.repository.list_events("run-1") == before_events
    assert service.repository.load_attempt_history("run-1") == before_attempts


@pytest.mark.parametrize("event_type", ["attempt_failed", "attempt_timed_out", "attempt_abandoned"])
def test_valid_attempt_terminal_histories_replay_and_remain_recoverable(event_type: str) -> None:
    service = make_service()
    prepare_running_run(service)
    service.record_checkpoint(
        RecordCheckpointCommand(
            run_id="run-1",
            command_id="cmd-checkpoint-before-terminal",
            expected_run_version=6,
            timestamp=ts(5),
            actor_reference="worker-1",
            checkpoint_id="checkpoint-terminal",
            state_reference="checkpoint://terminal",
            integrity_digest="sha256:aaaaaaaaaaaaaaaa",
            source_metadata={"source": "test"},
        )
    )
    if event_type == "attempt_failed":
        service.fail_attempt(
            FailAttemptCommand(
                run_id="run-1",
                command_id="cmd-terminal-fail",
                expected_run_version=7,
                timestamp=ts(6),
                actor_reference="worker-1",
                failure_category="dependency",
                failure_detail="Dependency unavailable",
                source_metadata={"source": "test"},
            )
        )
    elif event_type == "attempt_timed_out":
        service.timeout_attempt(
            TimeoutAttemptCommand(
                run_id="run-1",
                command_id="cmd-terminal-timeout",
                expected_run_version=7,
                timestamp=ts(6),
                actor_reference="worker-1",
                source_metadata={"source": "test"},
            )
        )
    else:
        service.abandon_attempt(
            AbandonAttemptCommand(
                run_id="run-1",
                command_id="cmd-terminal-abandon",
                expected_run_version=7,
                timestamp=ts(6),
                actor_reference="worker-1",
                source_metadata={"source": "test"},
            )
        )
    replayed = replay_execution_ledger(service.repository.list_events("run-1"))
    assert replayed is not None
    assert replayed.snapshot.state == AgentRunState.BLOCKED
    assert replayed.snapshot.blocking_reason is not None
    assert replayed.snapshot.blocking_reason.code == "recovery_required"
    assert replayed.snapshot.blocking_reason.resume_state == AgentRunState.CLAIMED


@pytest.mark.parametrize(
    "payload_updates",
    [
        {"attempt_number": 2},
        {"state": AttemptState.FAILED.value},
        {"version": 999},
        {"started_at": ts(999).isoformat()},
    ],
)
def test_replay_validates_core_attempt_created_invariants(
    payload_updates: dict[str, object],
) -> None:
    service = make_service()
    prepare_running_run(service)
    events = service.repository.list_events("run-1")
    broken = _mutate_attempt_created(events, payload_updates=payload_updates)
    with pytest.raises(LedgerReplayError):
        replay_execution_ledger(broken)


def test_replay_rejects_duplicate_or_skipped_attempt_numbers() -> None:
    service = make_service()
    prepare_running_run(service)
    events = service.repository.list_events("run-1")
    duplicate = _mutate_attempt_created(events, payload_updates={"attempt_number": 0})
    with pytest.raises(LedgerReplayError):
        replay_execution_ledger(duplicate)
    skipped = _mutate_attempt_created(events, payload_updates={"attempt_number": 3})
    with pytest.raises(LedgerReplayError):
        replay_execution_ledger(skipped)


def test_replay_rejects_attempt_creation_while_another_attempt_is_active() -> None:
    service = make_service()
    prepare_running_run(service)
    events = service.repository.list_events("run-1")
    duplicate_create = events + [
        events[4].model_copy(
            update={
                "event_id": "event-duplicate-create",
                "sequence_number": 7,
                "run_version": 7,
            }
        )
    ]
    with pytest.raises(LedgerReplayError):
        replay_execution_ledger(duplicate_create)


@pytest.mark.parametrize(
    ("payload_updates", "event_updates"),
    [
        ({"run_id": "run-2"}, None),
        ({"attempt_id": "attempt-other"}, None),
        (None, {"attempt_id": "attempt-other"}),
    ],
)
def test_replay_rejects_attempt_and_event_identifier_mismatches(
    payload_updates: dict[str, object] | None,
    event_updates: dict[str, object] | None,
) -> None:
    service = make_service()
    prepare_running_run(service)
    events = service.repository.list_events("run-1")
    broken = _mutate_attempt_created(
        events, payload_updates=payload_updates, event_updates=event_updates
    )
    with pytest.raises(LedgerReplayError):
        replay_execution_ledger(broken)


def test_replay_rejects_resume_checkpoint_not_found() -> None:
    service = make_service()
    prepare_running_run(service)
    events = service.repository.list_events("run-1")
    broken = _mutate_attempt_created(
        events,
        payload_updates={"resumed_from_checkpoint_id": "checkpoint-missing"},
    )
    with pytest.raises(LedgerReplayError):
        replay_execution_ledger(broken)


def test_replay_rejects_resume_checkpoint_from_another_run() -> None:
    service = make_service()
    prepare_running_run(service)
    events = service.repository.list_events("run-1")
    foreign = events[:4] + [
        events[4].model_copy(
            update={
                "payload": {
                    "attempt": {
                        **events[4].payload["attempt"],
                        "resumed_from_checkpoint_id": "checkpoint-foreign",
                    }
                }
            }
        ),
        events[5],
        RuntimeEventEnvelope.model_validate(
            events[4].model_dump(mode="json")
            | {
                "event_id": "event-foreign-checkpoint",
                "event_type": "checkpoint_recorded",
                "run_id": "run-2",
                "attempt_id": "attempt-foreign",
                "sequence_number": 7,
                "run_version": 7,
                "timestamp": ts(6).isoformat(),
                "payload": {
                    "checkpoint": {
                        "checkpoint_id": "checkpoint-foreign",
                        "run_id": "run-2",
                        "attempt_id": "attempt-foreign",
                        "checkpoint_sequence": 1,
                        "run_version": 7,
                        "event_sequence": 7,
                        "schema_version": "1.0",
                        "timestamp": ts(6).isoformat(),
                        "state_reference": "checkpoint://foreign",
                        "integrity_digest": "sha256:aaaaaaaaaaaaaaaa",
                        "resume_cursor": None,
                        "metadata": {},
                    }
                },
                "metadata": {},
            }
        ),
    ]
    with pytest.raises(LedgerReplayError):
        replay_execution_ledger(foreign)


def test_replay_rejects_resume_checkpoint_other_than_selected_recovery_checkpoint() -> None:
    service = make_service()
    prepare_running_run(service)
    service.record_checkpoint(
        RecordCheckpointCommand(
            run_id="run-1",
            command_id="cmd-checkpoint-1",
            expected_run_version=6,
            timestamp=ts(5),
            actor_reference="worker-1",
            checkpoint_id="checkpoint-1",
            state_reference="checkpoint://state/1",
            integrity_digest="sha256:aaaaaaaaaaaaaaaa",
            source_metadata={"source": "test"},
        )
    )
    service.record_checkpoint(
        RecordCheckpointCommand(
            run_id="run-1",
            command_id="cmd-checkpoint-2",
            expected_run_version=7,
            timestamp=ts(6),
            actor_reference="worker-1",
            checkpoint_id="checkpoint-2",
            state_reference="checkpoint://state/2",
            integrity_digest="sha256:bbbbbbbbbbbbbbbb",
            source_metadata={"source": "test"},
        )
    )
    fail_attempt(service, "run-1", expected_run_version=8, command_id="cmd-fail-1", second=7)
    service.request_recovery_plan(
        RequestRecoveryPlanCommand(
            run_id="run-1",
            command_id="cmd-plan-1",
            expected_run_version=9,
            timestamp=ts(8),
            actor_reference="operator-1",
            source_metadata={"source": "test"},
        )
    )
    service.unblock_run(
        UnblockAgentRunCommand(
            run_id="run-1",
            command_id="cmd-unblock-1",
            expected_run_version=10,
            timestamp=ts(9),
            actor_reference="operator-1",
            source_metadata={"source": "test"},
        )
    )
    begin_attempt(
        service,
        "run-1",
        expected_run_version=11,
        command_id="cmd-begin-2",
        second=10,
        checkpoint_id="checkpoint-2",
    )
    start_attempt(service, "run-1", expected_run_version=13, command_id="cmd-start-2", second=11)
    valid_events = service.repository.list_events("run-1")
    replayed = replay_execution_ledger(valid_events)
    assert replayed is not None
    mutated = _mutate_attempt_created(
        valid_events,
        payload_updates={"resumed_from_checkpoint_id": "checkpoint-1"},
        occurrence=1,
    )
    with pytest.raises(LedgerReplayError):
        replay_execution_ledger(mutated)


@pytest.mark.parametrize("attempt_number", [1, 4])
def test_replay_validates_later_attempt_numbering_and_max_attempts(attempt_number: int) -> None:
    service = make_service()
    prepare_running_run(service)
    fail_attempt(service, "run-1", expected_run_version=6, command_id="cmd-fail-1", second=5)
    service.request_recovery_plan(
        RequestRecoveryPlanCommand(
            run_id="run-1",
            command_id="cmd-plan-1",
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
    valid_events = service.repository.list_events("run-1")
    later_index = next(
        index
        for index, event in enumerate(valid_events)
        if event.event_type.value == "attempt_created" and event.sequence_number == 11
    )
    broken = [event.model_copy(deep=True) for event in valid_events]
    payload = dict(broken[later_index].payload["attempt"])
    payload["attempt_number"] = attempt_number
    broken[later_index] = broken[later_index].model_copy(update={"payload": {"attempt": payload}})
    with pytest.raises(LedgerReplayError):
        replay_execution_ledger(broken)


def test_replay_of_valid_recovery_attempt_is_deterministic() -> None:
    service = make_service()
    prepare_running_run(service)
    service.record_checkpoint(
        RecordCheckpointCommand(
            run_id="run-1",
            command_id="cmd-checkpoint-1",
            expected_run_version=6,
            timestamp=ts(5),
            actor_reference="worker-1",
            checkpoint_id="checkpoint-1",
            state_reference="checkpoint://state/1",
            integrity_digest="sha256:aaaaaaaaaaaaaaaa",
            source_metadata={"source": "test"},
        )
    )
    fail_attempt(service, "run-1", expected_run_version=7, command_id="cmd-fail-1", second=6)
    service.request_recovery_plan(
        RequestRecoveryPlanCommand(
            run_id="run-1",
            command_id="cmd-plan-1",
            expected_run_version=8,
            timestamp=ts(7),
            actor_reference="operator-1",
            source_metadata={"source": "test"},
        )
    )
    service.unblock_run(
        UnblockAgentRunCommand(
            run_id="run-1",
            command_id="cmd-unblock-1",
            expected_run_version=9,
            timestamp=ts(8),
            actor_reference="operator-1",
            source_metadata={"source": "test"},
        )
    )
    begin_attempt(
        service,
        "run-1",
        expected_run_version=10,
        command_id="cmd-begin-2",
        second=9,
        checkpoint_id="checkpoint-1",
    )
    valid_events = service.repository.list_events("run-1")
    first = replay_execution_ledger(valid_events)
    second = replay_execution_ledger(valid_events)
    assert first is not None and second is not None
    assert first.snapshot == second.snapshot
    assert first.attempts == second.attempts


def test_replay_rejects_attempt_created_after_succeeded_attempt() -> None:
    events = _prepare_run_succeeded_events()
    invalid_attempt = RuntimeEventEnvelope.model_validate(
        events[-1].model_dump(mode="json")
        | {
            "event_id": "event-attempt-after-success",
            "event_type": "attempt_created",
            "sequence_number": len(events) + 1,
            "run_version": len(events) + 1,
            "attempt_id": "attempt-2",
            "timestamp": ts(7).isoformat(),
            "payload": {
                "attempt": {
                    "attempt_id": "attempt-2",
                    "run_id": "run-1",
                    "attempt_number": 2,
                    "state": "starting",
                    "started_at": ts(7).isoformat(),
                    "executor_reference": "worker-1",
                    "version": len(events) + 1,
                }
            },
        }
    )
    with pytest.raises(LedgerReplayError):
        replay_execution_ledger(events + [invalid_attempt])


def test_replay_rejects_run_success_without_a_succeeded_latest_attempt() -> None:
    service = make_service()
    create_run(service)
    queue_run(service, "run-1", expected_run_version=1)
    claim_run(service, "run-1", expected_run_version=2)
    events = service.repository.list_events("run-1")
    invalid_success = RuntimeEventEnvelope.model_validate(
        events[-1].model_dump(mode="json")
        | {
            "event_id": "event-invalid-run-success",
            "event_type": "run_succeeded",
            "sequence_number": 4,
            "run_version": 4,
            "attempt_id": None,
            "timestamp": ts(3).isoformat(),
            "payload": {"detail": "Invalid success"},
        }
    )
    with pytest.raises(LedgerReplayError):
        replay_execution_ledger(events + [invalid_success])


def test_replay_rejects_run_success_when_latest_attempt_did_not_succeed() -> None:
    events = _prepare_claimed_after_failed_attempt_events()
    invalid_success = RuntimeEventEnvelope.model_validate(
        events[-1].model_dump(mode="json")
        | {
            "event_id": "event-invalid-run-success-active",
            "event_type": "run_succeeded",
            "sequence_number": len(events) + 1,
            "run_version": len(events) + 1,
            "attempt_id": None,
            "timestamp": ts(99).isoformat(),
            "payload": {"detail": "Invalid success"},
        }
    )
    with pytest.raises(LedgerReplayError):
        replay_execution_ledger(events + [invalid_success])


def test_replay_rejects_run_success_for_the_wrong_attempt_id() -> None:
    events = _prepare_run_succeeded_events()
    invalid_events = [event.model_copy(deep=True) for event in events]
    invalid_events[-1] = invalid_events[-1].model_copy(update={"attempt_id": "attempt-older"})
    with pytest.raises(LedgerReplayError):
        replay_execution_ledger(invalid_events)


def test_replay_accepts_valid_attempt_success_followed_by_run_success() -> None:
    events = _prepare_run_succeeded_events()
    replayed = replay_execution_ledger(events)
    assert replayed is not None
    assert replayed.snapshot.state == AgentRunState.SUCCEEDED


def test_repository_append_rejects_invalid_run_success_without_mutation() -> None:
    service = make_service()
    create_run(service)
    queue_run(service, "run-1", expected_run_version=1)
    claim_run(service, "run-1", expected_run_version=2)
    before_snapshot = service.repository.load_run("run-1")
    before_events = service.repository.list_events("run-1")
    invalid_success = RuntimeEventEnvelope.model_validate(
        before_events[-1].model_dump(mode="json")
        | {
            "event_id": "event-invalid-run-success-append",
            "event_type": "run_succeeded",
            "sequence_number": 4,
            "run_version": 4,
            "timestamp": ts(3).isoformat(),
            "payload": {"detail": "Invalid success"},
        }
    )
    with pytest.raises(LedgerReplayError):
        service.repository.append_events("run-1", [invalid_success], expected_sequence=3)
    assert service.repository.load_run("run-1") == before_snapshot
    assert service.repository.list_events("run-1") == before_events


@pytest.mark.parametrize(
    "event_type",
    ["run_failed", "run_timed_out", "run_abandoned"],
)
def test_replay_rejects_run_terminal_events_while_attempt_remains_active(event_type: str) -> None:
    events = _prepare_blocked_active_run_events()
    invalid_event = RuntimeEventEnvelope.model_validate(
        events[-1].model_dump(mode="json")
        | {
            "event_id": f"event-{event_type}",
            "event_type": event_type,
            "sequence_number": len(events) + 1,
            "run_version": len(events) + 1,
            "attempt_id": None,
            "timestamp": ts(99).isoformat(),
            "payload": {
                "failure": {
                    "category": "internal",
                    "detail": "Terminal failure",
                    "timestamp": ts(99).isoformat(),
                    "metadata": {},
                }
            },
        }
    )
    with pytest.raises(LedgerReplayError):
        replay_execution_ledger(events + [invalid_event])


@pytest.mark.parametrize(
    ("event_type", "events_builder"),
    [
        ("run_failed", _prepare_running_run_events),
        ("run_failed", _prepare_pause_requested_events),
        ("run_timed_out", _prepare_running_run_events),
        ("run_abandoned", _prepare_pause_requested_events),
    ],
)
def test_run_terminal_events_from_disallowed_active_states_are_rejected(
    event_type: str,
    events_builder,
) -> None:
    events = events_builder()
    invalid_event = RuntimeEventEnvelope.model_validate(
        events[-1].model_dump(mode="json")
        | {
            "event_id": f"event-invalid-{event_type}",
            "event_type": event_type,
            "sequence_number": len(events) + 1,
            "run_version": len(events) + 1,
            "timestamp": ts(99).isoformat(),
            "payload": {
                "failure": {
                    "category": "internal",
                    "detail": "Terminal failure",
                    "timestamp": ts(99).isoformat(),
                    "metadata": {},
                }
            },
        }
    )
    with pytest.raises(InvalidTransitionError):
        replay_execution_ledger(events + [invalid_event])


@pytest.mark.parametrize(
    "event_type",
    ["run_failed", "run_timed_out", "run_abandoned"],
)
def test_replay_accepts_valid_run_terminal_events_after_attempt_terminalization(
    event_type: str,
) -> None:
    events = _prepare_run_terminal_failure_events(event_type)
    replayed = replay_execution_ledger(events)
    assert replayed is not None
    expected_state = {
        "run_failed": AgentRunState.FAILED,
        "run_timed_out": AgentRunState.TIMED_OUT,
        "run_abandoned": AgentRunState.ABANDONED,
    }[event_type]
    assert replayed.snapshot.state == expected_state


def test_replay_validates_recorded_recovery_plan_exactly() -> None:
    planned, events = _prepare_recovery_planned_run()
    replayed = replay_execution_ledger(events)
    assert replayed is not None
    assert replayed.snapshot == planned.snapshot


def test_replay_rejects_fabricated_recovery_plan_on_manual_block() -> None:
    service = make_service()
    prepare_running_run(service)
    service.block_run(
        BlockAgentRunCommand(
            run_id="run-1",
            command_id="cmd-block-manual",
            expected_run_version=6,
            timestamp=ts(5),
            actor_reference="operator-1",
            block_code="manual_block",
            detail="Manual operator block",
            source_metadata={"source": "test"},
        )
    )
    events = service.repository.list_events("run-1")
    fabricated = RuntimeEventEnvelope.model_validate(
        events[-1].model_dump(mode="json")
        | {
            "event_id": "event-fabricated-plan",
            "event_type": "recovery_planned",
            "sequence_number": 8,
            "run_version": 8,
            "payload": {
                "plan": {
                    "run_id": "run-1",
                    "recovery_allowed": True,
                    "selected_checkpoint": None,
                    "next_attempt_number": 2,
                    "expected_starting_state": "claimed",
                    "required_prior_terminal_attempt_id": "attempt-1",
                    "reason": "fabricated",
                    "warnings": [],
                    "expected_version": 7,
                    "expected_event_sequence": 7,
                }
            },
        }
    )
    with pytest.raises(LedgerReplayError):
        replay_execution_ledger(events + [fabricated])


@pytest.mark.parametrize(
    "plan_updates",
    [
        {"run_id": "other-run"},
        {"expected_version": 999},
        {"expected_event_sequence": 999},
        {"next_attempt_number": 99},
        {"required_prior_terminal_attempt_id": "attempt-other"},
        {"reason": "Modified reason"},
        {"warnings": ["modified warning"]},
        {
            "selected_checkpoint": {
                "checkpoint_id": "checkpoint-other",
                "run_id": "run-1",
                "attempt_id": "attempt-1",
                "checkpoint_sequence": 1,
                "run_version": 7,
                "event_sequence": 7,
                "schema_version": "1.0",
                "timestamp": ts(5).isoformat(),
                "state_reference": "checkpoint://other",
                "integrity_digest": "sha256:bbbbbbbbbbbbbbbb",
                "resume_cursor": None,
                "metadata": {},
            }
        },
    ],
)
def test_replay_rejects_modified_recovery_plan_fields(plan_updates: dict[str, object]) -> None:
    _, events = _prepare_recovery_planned_run()
    broken = [event.model_copy(deep=True) for event in events]
    payload = dict(broken[-1].payload["plan"])
    payload.update(plan_updates)
    broken[-1] = broken[-1].model_copy(update={"payload": {"plan": payload}})
    with pytest.raises(LedgerReplayError):
        replay_execution_ledger(broken)


def test_replay_rejects_malformed_recovery_plan_payload() -> None:
    _, events = _prepare_recovery_planned_run()
    broken = [event.model_copy(deep=True) for event in events]
    broken[-1] = broken[-1].model_copy(update={"payload": {"plan": {"reason": "only"}}})
    with pytest.raises(LedgerReplayError):
        replay_execution_ledger(broken)


@pytest.mark.parametrize(
    "events_builder",
    [
        _prepare_created_run_events,
        _prepare_queued_run_events,
        _prepare_claimed_run_events,
        _prepare_running_run_events,
        _prepare_resumed_run_events,
        _prepare_unblocked_run_events,
        _prepare_cancellation_request_events,
        _prepare_checkpoint_events,
        _prepare_blocked_run_events,
        lambda: _prepare_recovery_planned_run()[1],
    ],
)
def test_representative_valid_ledgers_replay_deterministically(events_builder) -> None:
    events = events_builder()
    first = replay_execution_ledger(events)
    second = replay_execution_ledger(events)
    assert first is not None and second is not None
    assert first.snapshot == second.snapshot
    assert first.attempts == second.attempts
    assert first.checkpoints == second.checkpoints
