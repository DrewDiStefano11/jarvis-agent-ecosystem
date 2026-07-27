from __future__ import annotations

from datetime import timedelta

import pytest

from app.agent_runtime.errors import LedgerReplayError, LedgerSequenceError
from app.agent_runtime.ledger import replay_execution_ledger
from app.models.agent_runtime import (
    AgentRunState,
    AttemptState,
    BlockAgentRunCommand,
    RecordCheckpointCommand,
    RequestRecoveryPlanCommand,
    RuntimeEventEnvelope,
    UnblockAgentRunCommand,
)
from tests.agent_runtime_testkit import (
    begin_attempt,
    fail_attempt,
    make_service,
    prepare_blocked_run,
    prepare_running_run,
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
