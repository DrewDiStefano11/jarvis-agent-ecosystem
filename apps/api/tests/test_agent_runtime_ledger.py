from __future__ import annotations

from datetime import timedelta

import pytest

from app.agent_runtime.errors import LedgerReplayError, LedgerSequenceError
from app.agent_runtime.ledger import replay_execution_ledger
from app.models.agent_runtime import AgentRunState, RuntimeEventEnvelope
from tests.agent_runtime_testkit import make_service, prepare_blocked_run, prepare_running_run


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
