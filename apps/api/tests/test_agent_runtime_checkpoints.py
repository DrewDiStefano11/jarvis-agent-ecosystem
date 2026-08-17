from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from app.agent_runtime.errors import (
    CheckpointLineageError,
    CheckpointNotAllowedError,
    CheckpointSequenceConflictError,
    CommandConflictError,
    LedgerReplayError,
)
from app.agent_runtime.ledger import replay_execution_ledger
from app.agent_runtime.recovery import plan_recovery
from app.models.agent_runtime import (
    BeginAttemptCommand,
    ConfirmCancellationCommand,
    ConfirmCancellationStartCommand,
    HeartbeatCommand,
    RecordCheckpointCommand,
    RequestCancellationCommand,
    RequestRecoveryPlanCommand,
    RuntimeEventEnvelope,
    UnblockAgentRunCommand,
)
from tests.agent_runtime_testkit import (
    begin_attempt,
    claim_run,
    create_run,
    fail_attempt,
    make_service,
    make_spec,
    prepare_running_run,
    queue_run,
    start_attempt,
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


def test_duplicate_checkpoint_id_with_same_attempt_and_identical_content_is_a_no_op() -> None:
    service = make_service()
    prepare_running_run(service)
    attempt_id = service.repository.load_attempt_history("run-1")[-1].attempt_id
    first = service.record_checkpoint(
        RecordCheckpointCommand(
            run_id="run-1",
            command_id="cmd-checkpoint-1",
            expected_run_version=6,
            timestamp=ts(5),
            actor_reference="worker-1",
            checkpoint_id="checkpoint-stable",
            attempt_id=attempt_id,
            state_reference="checkpoint://state/1",
            integrity_digest="sha256:aaaaaaaaaaaaaaaa",
            resume_cursor="cursor-1",
            checkpoint_metadata={"step": 1},
            source_metadata={"source": "test"},
        )
    )
    event_count = len(service.repository.list_events("run-1"))
    checkpoint_count = len(service.repository.list_checkpoints("run-1"))
    second = service.record_checkpoint(
        RecordCheckpointCommand(
            run_id="run-1",
            command_id="cmd-checkpoint-2",
            expected_run_version=7,
            timestamp=ts(5),
            actor_reference="worker-1",
            checkpoint_id="checkpoint-stable",
            attempt_id=attempt_id,
            state_reference="checkpoint://state/1",
            integrity_digest="sha256:aaaaaaaaaaaaaaaa",
            resume_cursor="cursor-1",
            checkpoint_metadata={"step": 1},
            source_metadata={"source": "test"},
        )
    )
    assert second.snapshot == first.snapshot
    assert len(service.repository.list_events("run-1")) == event_count
    assert len(service.repository.list_checkpoints("run-1")) == checkpoint_count


def test_identical_historical_checkpoint_resubmission_is_a_deterministic_no_op() -> None:
    service = make_service()
    prepare_running_run(service)
    attempt_id = service.repository.load_attempt_history("run-1")[-1].attempt_id
    checkpoint_a_command = RecordCheckpointCommand(
        run_id="run-1",
        command_id="cmd-checkpoint-a",
        expected_run_version=6,
        timestamp=ts(5),
        actor_reference="worker-1",
        checkpoint_id="checkpoint-a",
        state_reference="checkpoint://state/a",
        integrity_digest="sha256:aaaaaaaaaaaaaaaa",
        resume_cursor="cursor-a",
        checkpoint_metadata={"step": 1, "nested": {"value": True}},
        source_metadata={"source": "test"},
    )
    service.record_checkpoint(checkpoint_a_command)
    service.record_heartbeat(
        HeartbeatCommand(
            run_id="run-1",
            command_id="cmd-heartbeat-1",
            expected_run_version=7,
            timestamp=ts(6),
            actor_reference="worker-1",
            attempt_id=attempt_id,
            source_metadata={"source": "test"},
        )
    )
    service.record_checkpoint(
        RecordCheckpointCommand(
            run_id="run-1",
            command_id="cmd-checkpoint-b",
            expected_run_version=8,
            timestamp=ts(7),
            actor_reference="worker-1",
            checkpoint_id="checkpoint-b",
            attempt_id=attempt_id,
            state_reference="checkpoint://state/b",
            integrity_digest="sha256:bbbbbbbbbbbbbbbb",
            resume_cursor="cursor-b",
            checkpoint_metadata={"step": 2},
            source_metadata={"source": "test"},
        )
    )
    service.record_heartbeat(
        HeartbeatCommand(
            run_id="run-1",
            command_id="cmd-heartbeat-2",
            expected_run_version=9,
            timestamp=ts(8),
            actor_reference="worker-1",
            attempt_id=attempt_id,
            source_metadata={"source": "test"},
        )
    )
    fail_attempt(
        service,
        "run-1",
        expected_run_version=10,
        command_id="cmd-fail-before-resubmit",
        second=9,
    )
    before_snapshot = service.repository.load_run("run-1")
    before_events = service.repository.list_events("run-1")
    before_checkpoints = service.repository.list_checkpoints("run-1")
    original_a = before_checkpoints[0]
    no_op_command = checkpoint_a_command.model_copy(
        update={
            "command_id": "cmd-checkpoint-a-resubmit",
            "expected_run_version": 11,
        }
    )

    result = service.record_checkpoint(no_op_command)

    assert result.snapshot == before_snapshot
    assert result.events == ()
    assert result.idempotent_replay is False
    assert service.repository.load_run("run-1") == before_snapshot
    assert service.repository.list_events("run-1") == before_events
    assert service.repository.list_checkpoints("run-1") == before_checkpoints
    assert service.repository.list_checkpoints("run-1")[0] == original_a
    assert service.repository.list_checkpoints("run-1")[-1].checkpoint_sequence == 2
    assert service.repository.get_processed_command("run-1", no_op_command.command_id) is not None

    replay = service.record_checkpoint(no_op_command)
    assert replay.snapshot == before_snapshot
    assert replay.events == ()
    assert replay.idempotent_replay is True
    with pytest.raises(CommandConflictError):
        service.record_checkpoint(
            no_op_command.model_copy(update={"state_reference": "checkpoint://state/changed"})
        )

    plan = plan_recovery(
        service.repository.load_run("run-1"),
        service.repository.load_attempt_history("run-1"),
        service.repository.list_checkpoints("run-1"),
        service.repository.list_events("run-1"),
    )
    assert plan.selected_checkpoint is not None
    assert plan.selected_checkpoint.checkpoint_id == "checkpoint-b"


def test_concurrent_same_command_historical_checkpoint_no_op_replays_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = make_service()
    prepare_running_run(service)
    checkpoint_a = RecordCheckpointCommand(
        run_id="run-1",
        command_id="cmd-checkpoint-a",
        expected_run_version=6,
        timestamp=ts(5),
        actor_reference="worker-1",
        checkpoint_id="checkpoint-a",
        state_reference="checkpoint://state/a",
        integrity_digest="sha256:aaaaaaaaaaaaaaaa",
        resume_cursor="cursor-a",
        checkpoint_metadata={"step": 1},
        source_metadata={"source": "test"},
    )
    service.record_checkpoint(checkpoint_a)
    service.record_checkpoint(
        checkpoint_a.model_copy(
            update={
                "command_id": "cmd-checkpoint-b",
                "expected_run_version": 7,
                "timestamp": ts(6),
                "checkpoint_id": "checkpoint-b",
                "state_reference": "checkpoint://state/b",
                "integrity_digest": "sha256:bbbbbbbbbbbbbbbb",
            }
        )
    )
    before_snapshot = service.repository.load_run("run-1")
    before_events = service.repository.list_events("run-1")
    before_checkpoints = service.repository.list_checkpoints("run-1")
    command = checkpoint_a.model_copy(
        update={
            "command_id": "cmd-checkpoint-a-concurrent-resubmit",
            "expected_run_version": 8,
        }
    )
    barrier = Barrier(2)
    original_commit = service.repository.commit_command

    def wrapped_commit(*args, **kwargs):
        barrier.wait()
        return original_commit(*args, **kwargs)

    monkeypatch.setattr(service.repository, "commit_command", wrapped_commit)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: service.record_checkpoint(command), range(2)))

    assert sorted(result.idempotent_replay for result in results) == [False, True]
    assert all(result.snapshot == before_snapshot for result in results)
    assert all(result.events == () for result in results)
    assert service.repository.load_run("run-1") == before_snapshot
    assert service.repository.list_events("run-1") == before_events
    assert service.repository.list_checkpoints("run-1") == before_checkpoints


@pytest.mark.parametrize(
    "changes",
    [
        {"timestamp": ts(6)},
        {"state_reference": "checkpoint://state/changed"},
        {"integrity_digest": "sha256:bbbbbbbbbbbbbbbb"},
        {"resume_cursor": "cursor-changed"},
        {"checkpoint_metadata": {"step": 2}},
        {"attempt_id": "attempt-unrelated"},
    ],
)
def test_historical_checkpoint_resubmission_rejects_immutable_content_changes(
    changes: dict[str, object],
) -> None:
    service = make_service()
    prepare_running_run(service)
    attempt_id = service.repository.load_attempt_history("run-1")[-1].attempt_id
    original = RecordCheckpointCommand(
        run_id="run-1",
        command_id="cmd-checkpoint-original",
        expected_run_version=6,
        timestamp=ts(5),
        actor_reference="worker-1",
        checkpoint_id="checkpoint-stable",
        attempt_id=attempt_id,
        state_reference="checkpoint://state/1",
        integrity_digest="sha256:aaaaaaaaaaaaaaaa",
        resume_cursor="cursor-1",
        checkpoint_metadata={"step": 1},
        source_metadata={"source": "test"},
    )
    service.record_checkpoint(original)
    service.record_checkpoint(
        original.model_copy(
            update={
                "command_id": "cmd-checkpoint-later",
                "expected_run_version": 7,
                "checkpoint_id": "checkpoint-later",
                "timestamp": ts(6),
                "state_reference": "checkpoint://state/2",
                "integrity_digest": "sha256:cccccccccccccccc",
            }
        )
    )
    before_snapshot = service.repository.load_run("run-1")
    before_events = service.repository.list_events("run-1")
    before_checkpoints = service.repository.list_checkpoints("run-1")
    with pytest.raises(CheckpointSequenceConflictError):
        service.record_checkpoint(
            original.model_copy(
                update={
                    "command_id": "cmd-checkpoint-conflict",
                    "expected_run_version": 8,
                    **changes,
                }
            )
        )
    assert service.repository.load_run("run-1") == before_snapshot
    assert service.repository.list_events("run-1") == before_events
    assert service.repository.list_checkpoints("run-1") == before_checkpoints
    assert service.repository.get_processed_command("run-1", "cmd-checkpoint-conflict") is None


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
    before_snapshot = service.repository.load_run("run-1")
    before_events = service.repository.list_events("run-1")
    before_checkpoints = service.repository.list_checkpoints("run-1")
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
    assert service.repository.load_run("run-1") == before_snapshot
    assert service.repository.list_events("run-1") == before_events
    assert service.repository.list_checkpoints("run-1") == before_checkpoints
    assert service.repository.get_processed_command("run-1", "cmd-checkpoint-2") is None
    replayed = replay_execution_ledger(service.repository.list_events("run-1"))
    assert replayed is not None
    assert replayed.snapshot == before_snapshot


def test_duplicate_checkpoint_id_with_different_timestamp_conflicts() -> None:
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
            resume_cursor="cursor-1",
            checkpoint_metadata={"step": 1},
            source_metadata={"source": "test"},
        )
    )
    before_snapshot = service.repository.load_run("run-1")
    before_events = service.repository.list_events("run-1")
    before_attempts = service.repository.load_attempt_history("run-1")
    before_checkpoints = service.repository.list_checkpoints("run-1")
    original_timestamp = before_checkpoints[-1].timestamp
    with pytest.raises(CheckpointSequenceConflictError):
        service.record_checkpoint(
            RecordCheckpointCommand(
                run_id="run-1",
                command_id="cmd-checkpoint-timestamp-conflict",
                expected_run_version=7,
                timestamp=ts(6),
                actor_reference="worker-1",
                checkpoint_id="checkpoint-stable",
                state_reference="checkpoint://state/1",
                integrity_digest="sha256:aaaaaaaaaaaaaaaa",
                resume_cursor="cursor-1",
                checkpoint_metadata={"step": 1},
                source_metadata={"source": "test"},
            )
        )
    assert service.repository.load_run("run-1") == before_snapshot
    assert service.repository.list_events("run-1") == before_events
    assert service.repository.load_attempt_history("run-1") == before_attempts
    assert service.repository.list_checkpoints("run-1") == before_checkpoints
    assert service.repository.list_checkpoints("run-1")[-1].timestamp == original_timestamp
    assert (
        service.repository.get_processed_command("run-1", "cmd-checkpoint-timestamp-conflict")
        is None
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


def test_checkpoint_id_reuse_across_attempts_conflicts_but_new_ids_are_allowed() -> None:
    service = make_service()
    prepare_running_run(service)
    first_attempt_id = service.repository.load_attempt_history("run-1")[-1].attempt_id
    service.record_checkpoint(
        RecordCheckpointCommand(
            run_id="run-1",
            command_id="cmd-checkpoint-1",
            expected_run_version=6,
            timestamp=ts(5),
            actor_reference="worker-1",
            checkpoint_id="checkpoint-shared",
            attempt_id=first_attempt_id,
            state_reference="checkpoint://state/1",
            integrity_digest="sha256:aaaaaaaaaaaaaaaa",
            source_metadata={"source": "test"},
        )
    )
    fail_attempt(service, "run-1", expected_run_version=7, command_id="cmd-fail-1", second=6)
    service.request_recovery_plan(
        RequestRecoveryPlanCommand(
            run_id="run-1",
            command_id="cmd-recovery-plan-1",
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
        checkpoint_id="checkpoint-shared",
    )
    start_attempt(service, "run-1", expected_run_version=12, command_id="cmd-start-2", second=10)
    second_attempt_id = service.repository.load_attempt_history("run-1")[-1].attempt_id
    before_snapshot = service.repository.load_run("run-1")
    before_events = service.repository.list_events("run-1")
    before_checkpoints = service.repository.list_checkpoints("run-1")
    with pytest.raises(CheckpointSequenceConflictError):
        service.record_checkpoint(
            RecordCheckpointCommand(
                run_id="run-1",
                command_id="cmd-checkpoint-cross-attempt",
                expected_run_version=13,
                timestamp=ts(11),
                actor_reference="worker-1",
                checkpoint_id="checkpoint-shared",
                attempt_id=second_attempt_id,
                state_reference="checkpoint://state/1",
                integrity_digest="sha256:aaaaaaaaaaaaaaaa",
                source_metadata={"source": "test"},
            )
        )
    assert service.repository.load_run("run-1") == before_snapshot
    assert service.repository.list_events("run-1") == before_events
    assert service.repository.list_checkpoints("run-1") == before_checkpoints
    assert service.repository.get_processed_command("run-1", "cmd-checkpoint-cross-attempt") is None
    replayed = replay_execution_ledger(service.repository.list_events("run-1"))
    assert replayed is not None
    assert replayed.snapshot == before_snapshot

    allowed = service.record_checkpoint(
        RecordCheckpointCommand(
            run_id="run-1",
            command_id="cmd-checkpoint-second-attempt",
            expected_run_version=13,
            timestamp=ts(11),
            actor_reference="worker-1",
            checkpoint_id="checkpoint-second-attempt",
            attempt_id=second_attempt_id,
            state_reference="checkpoint://state/2",
            integrity_digest="sha256:bbbbbbbbbbbbbbbb",
            source_metadata={"source": "test"},
        )
    )
    assert allowed.snapshot is not None
    assert allowed.snapshot.latest_checkpoint_id == "checkpoint-second-attempt"


def test_recovery_selects_the_latest_checkpoint_for_the_active_attempt_lineage() -> None:
    service = make_service()
    prepare_running_run(service)
    first_attempt_id = service.repository.load_attempt_history("run-1")[-1].attempt_id
    service.record_checkpoint(
        RecordCheckpointCommand(
            run_id="run-1",
            command_id="cmd-checkpoint-1",
            expected_run_version=6,
            timestamp=ts(5),
            actor_reference="worker-1",
            checkpoint_id="checkpoint-first-attempt",
            attempt_id=first_attempt_id,
            state_reference="checkpoint://state/1",
            integrity_digest="sha256:aaaaaaaaaaaaaaaa",
            source_metadata={"source": "test"},
        )
    )
    fail_attempt(service, "run-1", expected_run_version=7, command_id="cmd-fail-1", second=6)
    service.request_recovery_plan(
        RequestRecoveryPlanCommand(
            run_id="run-1",
            command_id="cmd-recovery-plan-1",
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
        checkpoint_id="checkpoint-first-attempt",
    )
    start_attempt(service, "run-1", expected_run_version=12, command_id="cmd-start-2", second=10)
    second_attempt_id = service.repository.load_attempt_history("run-1")[-1].attempt_id
    service.record_checkpoint(
        RecordCheckpointCommand(
            run_id="run-1",
            command_id="cmd-checkpoint-2",
            expected_run_version=13,
            timestamp=ts(11),
            actor_reference="worker-1",
            checkpoint_id="checkpoint-second-attempt",
            attempt_id=second_attempt_id,
            state_reference="checkpoint://state/2",
            integrity_digest="sha256:bbbbbbbbbbbbbbbb",
            source_metadata={"source": "test"},
        )
    )
    fail_attempt(service, "run-1", expected_run_version=14, command_id="cmd-fail-2", second=12)
    result = service.request_recovery_plan(
        RequestRecoveryPlanCommand(
            run_id="run-1",
            command_id="cmd-recovery-plan-2",
            expected_run_version=15,
            timestamp=ts(13),
            actor_reference="operator-1",
            source_metadata={"source": "test"},
        )
    )
    assert result.recovery_plan is not None
    assert result.recovery_plan.selected_checkpoint is not None
    assert result.recovery_plan.selected_checkpoint.checkpoint_id == "checkpoint-second-attempt"


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


def test_checkpoint_timestamp_must_match_event_timestamp_during_replay() -> None:
    service = make_service()
    prepare_running_run(service)
    service.record_checkpoint(
        RecordCheckpointCommand(
            run_id="run-1",
            command_id="cmd-checkpoint-1",
            expected_run_version=6,
            timestamp=ts(5),
            actor_reference="worker-1",
            checkpoint_id="checkpoint-time",
            state_reference="checkpoint://state/1",
            integrity_digest="sha256:aaaaaaaaaaaaaaaa",
            source_metadata={"source": "test"},
        )
    )
    events = service.repository.list_events("run-1")
    earlier = events[:-1] + [
        events[-1].model_copy(
            update={
                "payload": {
                    "checkpoint": {
                        **events[-1].payload["checkpoint"],
                        "timestamp": ts(4).isoformat(),
                    }
                }
            }
        )
    ]
    with pytest.raises(LedgerReplayError):
        replay_execution_ledger(earlier)
    later = events[:-1] + [
        events[-1].model_copy(
            update={
                "payload": {
                    "checkpoint": {
                        **events[-1].payload["checkpoint"],
                        "timestamp": ts(6).isoformat(),
                    }
                }
            }
        )
    ]
    with pytest.raises(LedgerReplayError):
        replay_execution_ledger(later)


def test_checkpoint_timestamp_before_run_creation_is_rejected() -> None:
    service = make_service()
    prepare_running_run(service)
    service.record_checkpoint(
        RecordCheckpointCommand(
            run_id="run-1",
            command_id="cmd-checkpoint-1",
            expected_run_version=6,
            timestamp=ts(5),
            actor_reference="worker-1",
            checkpoint_id="checkpoint-time",
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
                        "timestamp": ts(0).isoformat(),
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


def test_valid_checkpoint_timestamp_parity_with_event_and_command() -> None:
    service = make_service()
    prepare_running_run(service)
    command = RecordCheckpointCommand(
        run_id="run-1",
        command_id="cmd-checkpoint-parity",
        expected_run_version=6,
        timestamp=ts(5),
        actor_reference="worker-1",
        checkpoint_id="checkpoint-parity",
        state_reference="checkpoint://state/1",
        integrity_digest="sha256:aaaaaaaaaaaaaaaa",
        source_metadata={"source": "test"},
    )
    result = service.record_checkpoint(command)
    assert result.snapshot is not None
    event = service.repository.list_events("run-1")[-1]
    checkpoint = service.repository.list_checkpoints("run-1")[-1]
    assert checkpoint.timestamp == event.timestamp == command.timestamp


def test_repository_append_rejects_checkpoint_timestamp_mismatch_without_mutation() -> None:
    service = make_service()
    prepare_running_run(service)
    before_snapshot = service.repository.load_run("run-1")
    before_events = service.repository.list_events("run-1")
    before_attempts = service.repository.load_attempt_history("run-1")
    before_checkpoints = service.repository.list_checkpoints("run-1")
    append_event = RuntimeEventEnvelope(
        event_id="event-checkpoint-bad-time",
        event_type="checkpoint_recorded",
        run_id="run-1",
        attempt_id="attempt-1",
        sequence_number=7,
        run_version=7,
        timestamp=ts(5),
        actor_reference="worker-1",
        command_id="cmd-append-checkpoint-bad-time",
        correlation_id="corr-1",
        causation_id="cause-1",
        payload={
            "checkpoint": {
                "checkpoint_id": "checkpoint-bad-time",
                "run_id": "run-1",
                "attempt_id": "attempt-1",
                "checkpoint_sequence": 1,
                "run_version": 7,
                "event_sequence": 7,
                "schema_version": "1.0",
                "timestamp": ts(4).isoformat(),
                "state_reference": "checkpoint://state/1",
                "integrity_digest": "sha256:aaaaaaaaaaaaaaaa",
                "resume_cursor": None,
                "metadata": {},
            }
        },
        metadata={"source": "test"},
    )
    with pytest.raises(LedgerReplayError):
        service.repository.append_events(
            "run-1", [append_event], expected_sequence=len(before_events)
        )
    assert service.repository.load_run("run-1") == before_snapshot
    assert service.repository.list_events("run-1") == before_events
    assert service.repository.load_attempt_history("run-1") == before_attempts
    assert service.repository.list_checkpoints("run-1") == before_checkpoints


def test_recovery_selection_ignores_timestamp_mismatched_checkpoints() -> None:
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
    plan = plan_recovery(
        service.repository.load_run("run-1"),
        service.repository.load_attempt_history("run-1"),
        service.repository.list_checkpoints("run-1"),
        service.repository.list_events("run-1"),
    )
    assert plan.selected_checkpoint is not None
    assert plan.selected_checkpoint.checkpoint_id == "checkpoint-1"


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
