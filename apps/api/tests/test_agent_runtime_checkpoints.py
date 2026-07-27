from __future__ import annotations

import pytest

from app.agent_runtime.errors import (
    CheckpointLineageError,
    CheckpointNotAllowedError,
    CheckpointSequenceConflictError,
    LedgerReplayError,
)
from app.agent_runtime.ledger import replay_execution_ledger
from app.models.agent_runtime import (
    BeginAttemptCommand,
    ConfirmCancellationCommand,
    ConfirmCancellationStartCommand,
    RecordCheckpointCommand,
    RequestCancellationCommand,
)
from tests.agent_runtime_testkit import (
    claim_run,
    create_run,
    make_service,
    make_spec,
    prepare_running_run,
    queue_run,
    ts,
)


def test_valid_checkpoint_updates_latest_checkpoint_id() -> None:
    service = make_service()
    prepare_running_run(service)
    result = service.record_checkpoint(
        RecordCheckpointCommand(
            run_id="run-1",
            command_id="cmd-checkpoint-1",
            expected_run_version=6,
            timestamp=ts(5),
            actor_reference="worker-1",
            state_reference="checkpoint://state/1",
            integrity_digest="sha256:aaaaaaaaaaaaaaaa",
            resume_cursor="cursor-1",
            checkpoint_metadata={"step": 1},
            source_metadata={"source": "test"},
        )
    )
    assert result.snapshot is not None
    assert result.snapshot.latest_checkpoint_id == "checkpoint-1"
    assert service.repository.list_checkpoints("run-1")[-1].checkpoint_sequence == 1


def test_duplicate_checkpoint_command_is_idempotent() -> None:
    service = make_service()
    prepare_running_run(service)
    command = RecordCheckpointCommand(
        run_id="run-1",
        command_id="cmd-checkpoint-1",
        expected_run_version=6,
        timestamp=ts(5),
        actor_reference="worker-1",
        checkpoint_id="checkpoint-stable",
        state_reference="checkpoint://state/1",
        integrity_digest="sha256:aaaaaaaaaaaaaaaa",
        resume_cursor="cursor-1",
        checkpoint_metadata={"step": 1},
        source_metadata={"source": "test"},
    )
    first = service.record_checkpoint(command)
    second = service.record_checkpoint(command)
    assert first.snapshot == second.snapshot
    assert second.idempotent_replay is True
    assert len(service.repository.list_events("run-1")) == 7


def test_duplicate_checkpoint_id_with_different_contents_conflicts() -> None:
    service = make_service()
    prepare_running_run(service)
    service.record_checkpoint(
        RecordCheckpointCommand(
            run_id="run-1",
            command_id="cmd-checkpoint-1",
            expected_run_version=6,
            timestamp=ts(5),
            actor_reference="worker-1",
            checkpoint_id="checkpoint-stable",
            state_reference="checkpoint://state/1",
            integrity_digest="sha256:aaaaaaaaaaaaaaaa",
            source_metadata={"source": "test"},
        )
    )
    with pytest.raises(CheckpointSequenceConflictError):
        service.record_checkpoint(
            RecordCheckpointCommand(
                run_id="run-1",
                command_id="cmd-checkpoint-2",
                expected_run_version=7,
                timestamp=ts(6),
                actor_reference="worker-1",
                checkpoint_id="checkpoint-stable",
                state_reference="checkpoint://state/changed",
                integrity_digest="sha256:bbbbbbbbbbbbbbbb",
                source_metadata={"source": "test"},
            )
        )


def test_checkpoint_from_wrong_run_is_rejected_for_attempt_resume() -> None:
    service = make_service()
    prepare_running_run(service, run_id="run-1")
    service.record_checkpoint(
        RecordCheckpointCommand(
            run_id="run-1",
            command_id="cmd-checkpoint-1",
            expected_run_version=6,
            timestamp=ts(5),
            actor_reference="worker-1",
            checkpoint_id="checkpoint-foreign",
            state_reference="checkpoint://state/1",
            integrity_digest="sha256:aaaaaaaaaaaaaaaa",
            source_metadata={"source": "test"},
        )
    )
    create_run(
        service,
        specification=make_spec(run_id="run-2"),
        command_id="cmd-create-run-2",
        timestamp=ts(10),
    )
    queue_run(service, "run-2", expected_run_version=1, command_id="cmd-queue-run-2", second=11)
    claim_run(service, "run-2", expected_run_version=2, command_id="cmd-claim-run-2", second=12)
    with pytest.raises(CheckpointLineageError):
        service.begin_attempt(
            BeginAttemptCommand(
                run_id="run-2",
                command_id="cmd-begin-foreign",
                expected_run_version=3,
                timestamp=ts(13),
                actor_reference="worker-2",
                executor_reference="worker-2",
                resume_from_checkpoint_id="checkpoint-foreign",
                source_metadata={"source": "test"},
            )
        )


def test_checkpoint_after_terminal_state_is_rejected() -> None:
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
            detail="Stop it",
            source_metadata={"source": "test"},
        )
    )
    service.confirm_cancellation_start(
        ConfirmCancellationStartCommand(
            run_id="run-1",
            command_id="cmd-cancel-start",
            expected_run_version=7,
            timestamp=ts(6),
            actor_reference="worker-1",
            source_metadata={"source": "test"},
        )
    )
    service.confirm_cancellation(
        ConfirmCancellationCommand(
            run_id="run-1",
            command_id="cmd-cancel-confirm",
            expected_run_version=8,
            timestamp=ts(7),
            actor_reference="worker-1",
            source_metadata={"source": "test"},
        )
    )
    with pytest.raises(CheckpointNotAllowedError):
        service.record_checkpoint(
            RecordCheckpointCommand(
                run_id="run-1",
                command_id="cmd-checkpoint-terminal",
                expected_run_version=9,
                timestamp=ts(8),
                actor_reference="worker-1",
                state_reference="checkpoint://state/terminal",
                integrity_digest="sha256:aaaaaaaaaaaaaaaa",
                source_metadata={"source": "test"},
            )
        )


def test_invalid_checkpoint_event_position_is_rejected_by_replay() -> None:
    service = make_service()
    prepare_running_run(service)
    service.record_checkpoint(
        RecordCheckpointCommand(
            run_id="run-1",
            command_id="cmd-checkpoint-1",
            expected_run_version=6,
            timestamp=ts(5),
            actor_reference="worker-1",
            state_reference="checkpoint://state/1",
            integrity_digest="sha256:aaaaaaaaaaaaaaaa",
            source_metadata={"source": "test"},
        )
    )
    events = service.repository.list_events("run-1")
    broken = events[:-1] + [
        events[-1].model_copy(
            update={
                "payload": {
                    "checkpoint": {
                        **events[-1].payload["checkpoint"],
                        "event_sequence": 999,
                    }
                }
            }
        )
    ]
    with pytest.raises(LedgerReplayError):
        replay_execution_ledger(broken)


def test_invalid_checkpoint_run_version_is_rejected_by_replay() -> None:
    service = make_service()
    prepare_running_run(service)
    service.record_checkpoint(
        RecordCheckpointCommand(
            run_id="run-1",
            command_id="cmd-checkpoint-1",
            expected_run_version=6,
            timestamp=ts(5),
            actor_reference="worker-1",
            state_reference="checkpoint://state/1",
            integrity_digest="sha256:aaaaaaaaaaaaaaaa",
            source_metadata={"source": "test"},
        )
    )
    events = service.repository.list_events("run-1")
    broken = events[:-1] + [
        events[-1].model_copy(
            update={
                "payload": {
                    "checkpoint": {
                        **events[-1].payload["checkpoint"],
                        "run_version": 999,
                    }
                }
            }
        )
    ]
    with pytest.raises(LedgerReplayError):
        replay_execution_ledger(broken)


def test_latest_checkpoint_selection_is_deterministic() -> None:
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
    result = service.record_checkpoint(
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
    assert result.snapshot is not None
    assert result.snapshot.latest_checkpoint_id == "checkpoint-2"
