from __future__ import annotations

import pytest

from app.agent_runtime.errors import CommandConflictError, VersionConflictError
from app.models.agent_runtime import (
    QueueAgentRunCommand,
    RecordCheckpointCommand,
    RequestCancellationCommand,
)
from tests.agent_runtime_testkit import create_run, make_service, prepare_running_run, ts


def test_exact_duplicate_command_returns_original_result() -> None:
    service = make_service()
    create_run(service)
    command = QueueAgentRunCommand(
        run_id="run-1",
        command_id="cmd-queue",
        expected_run_version=1,
        timestamp=ts(1),
        actor_reference="scheduler-1",
        source_metadata={"source": "test"},
    )
    first = service.queue_run(command)
    second = service.queue_run(command)
    assert first.snapshot == second.snapshot
    assert second.idempotent_replay is True


def test_same_command_id_with_different_payload_conflicts() -> None:
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
    with pytest.raises(CommandConflictError):
        service.queue_run(
            QueueAgentRunCommand(
                run_id="run-1",
                command_id="cmd-queue",
                expected_run_version=1,
                timestamp=ts(1),
                actor_reference="scheduler-1",
                detail="Different detail",
                source_metadata={"source": "test"},
            )
        )


def test_failed_command_is_not_stored_as_processed() -> None:
    service = make_service()
    create_run(service)
    bad = QueueAgentRunCommand(
        run_id="run-1",
        command_id="cmd-bad-version",
        expected_run_version=99,
        timestamp=ts(1),
        actor_reference="scheduler-1",
        source_metadata={"source": "test"},
    )
    with pytest.raises(VersionConflictError):
        service.queue_run(bad)
    assert service.repository.get_processed_command("run-1", "cmd-bad-version") is None


def test_duplicate_command_does_not_append_duplicate_events() -> None:
    service = make_service()
    create_run(service)
    command = QueueAgentRunCommand(
        run_id="run-1",
        command_id="cmd-queue",
        expected_run_version=1,
        timestamp=ts(1),
        actor_reference="scheduler-1",
        source_metadata={"source": "test"},
    )
    service.queue_run(command)
    count_after_first = len(service.repository.list_events("run-1"))
    service.queue_run(command)
    assert len(service.repository.list_events("run-1")) == count_after_first


def test_result_reconstruction_returns_same_snapshot() -> None:
    service = make_service()
    create = create_run(service)
    duplicate = create_run(service, command_id="cmd-create", timestamp=ts(0))
    assert duplicate.idempotent_replay is True
    assert duplicate.snapshot == create.snapshot


def test_stale_expected_version_is_rejected_without_partial_mutation() -> None:
    service = make_service()
    create_run(service)
    service.queue_run(
        QueueAgentRunCommand(
            run_id="run-1",
            command_id="cmd-queue-1",
            expected_run_version=1,
            timestamp=ts(1),
            actor_reference="scheduler-1",
            source_metadata={"source": "test"},
        )
    )
    before_snapshot = service.repository.load_run("run-1")
    before_event_count = len(service.repository.list_events("run-1"))
    with pytest.raises(VersionConflictError):
        service.queue_run(
            QueueAgentRunCommand(
                run_id="run-1",
                command_id="cmd-queue-stale",
                expected_run_version=1,
                timestamp=ts(2),
                actor_reference="scheduler-1",
                source_metadata={"source": "test"},
            )
        )
    assert service.repository.load_run("run-1") == before_snapshot
    assert len(service.repository.list_events("run-1")) == before_event_count


@pytest.mark.parametrize(
    "changes",
    [
        {"reason_code": "policy_cancel"},
        {"detail": "Different detail"},
        {"requester_reference": "operator-2"},
        {"timestamp": ts(6)},
        {"expected_run_version": 7},
        {"source_metadata": {"source": "other"}},
    ],
)
def test_cancellation_command_conflicts_leave_runtime_unchanged(changes: dict[str, object]) -> None:
    service = make_service()
    prepare_running_run(service)
    command = RequestCancellationCommand(
        run_id="run-1",
        command_id="cmd-cancel",
        expected_run_version=6,
        timestamp=ts(5),
        actor_reference="operator-1",
        requester_reference="operator-1",
        reason_code="operator_cancel",
        detail="Stop the run",
        source_metadata={"source": "test"},
    )
    first = service.request_cancellation(command)
    before_snapshot = service.repository.load_run("run-1")
    before_events = service.repository.list_events("run-1")
    before_attempts = service.repository.load_attempt_history("run-1")
    before_processed = service.repository.get_processed_command("run-1", "cmd-cancel")
    with pytest.raises(CommandConflictError):
        service.request_cancellation(command.model_copy(update=changes))
    assert service.repository.load_run("run-1") == before_snapshot
    assert service.repository.list_events("run-1") == before_events
    assert service.repository.load_attempt_history("run-1") == before_attempts
    assert service.repository.get_processed_command("run-1", "cmd-cancel") == before_processed
    assert first.snapshot == before_snapshot


def test_exact_duplicate_cancellation_replay_returns_original_result_without_new_events() -> None:
    service = make_service()
    prepare_running_run(service)
    command = RequestCancellationCommand(
        run_id="run-1",
        command_id="cmd-cancel",
        expected_run_version=6,
        timestamp=ts(5),
        actor_reference="operator-1",
        requester_reference="operator-1",
        reason_code="operator_cancel",
        detail="Stop the run",
        source_metadata={"source": "test"},
    )
    first = service.request_cancellation(command)
    event_count = len(service.repository.list_events("run-1"))
    second = service.request_cancellation(command)
    assert second.idempotent_replay is True
    assert second.snapshot == first.snapshot
    assert len(service.repository.list_events("run-1")) == event_count


@pytest.mark.parametrize(
    "changes",
    [
        {"checkpoint_id": "checkpoint-other"},
        {"attempt_id": "attempt-other"},
        {"state_reference": "checkpoint://other"},
        {"integrity_digest": "sha256:bbbbbbbbbbbbbbbb"},
        {"resume_cursor": "cursor-2"},
        {"checkpoint_metadata": {"step": 2}},
        {"expected_run_version": 7},
        {"timestamp": ts(6)},
    ],
)
def test_checkpoint_command_conflicts_leave_runtime_unchanged(changes: dict[str, object]) -> None:
    service = make_service()
    prepare_running_run(service)
    attempt_id = service.repository.load_attempt_history("run-1")[-1].attempt_id
    command = RecordCheckpointCommand(
        run_id="run-1",
        command_id="cmd-checkpoint",
        expected_run_version=6,
        timestamp=ts(5),
        actor_reference="worker-1",
        checkpoint_id="checkpoint-1",
        attempt_id=attempt_id,
        state_reference="checkpoint://state/1",
        integrity_digest="sha256:aaaaaaaaaaaaaaaa",
        resume_cursor="cursor-1",
        checkpoint_metadata={"step": 1},
        source_metadata={"source": "test"},
    )
    first = service.record_checkpoint(command)
    before_snapshot = service.repository.load_run("run-1")
    before_events = service.repository.list_events("run-1")
    before_attempts = service.repository.load_attempt_history("run-1")
    before_checkpoints = service.repository.list_checkpoints("run-1")
    before_processed = service.repository.get_processed_command("run-1", "cmd-checkpoint")
    with pytest.raises(CommandConflictError):
        service.record_checkpoint(command.model_copy(update=changes))
    assert service.repository.load_run("run-1") == before_snapshot
    assert service.repository.list_events("run-1") == before_events
    assert service.repository.load_attempt_history("run-1") == before_attempts
    assert service.repository.list_checkpoints("run-1") == before_checkpoints
    assert service.repository.get_processed_command("run-1", "cmd-checkpoint") == before_processed
    assert first.snapshot == before_snapshot


def test_exact_duplicate_checkpoint_replay_returns_original_result_without_new_events() -> None:
    service = make_service()
    prepare_running_run(service)
    attempt_id = service.repository.load_attempt_history("run-1")[-1].attempt_id
    command = RecordCheckpointCommand(
        run_id="run-1",
        command_id="cmd-checkpoint",
        expected_run_version=6,
        timestamp=ts(5),
        actor_reference="worker-1",
        checkpoint_id="checkpoint-1",
        attempt_id=attempt_id,
        state_reference="checkpoint://state/1",
        integrity_digest="sha256:aaaaaaaaaaaaaaaa",
        resume_cursor="cursor-1",
        checkpoint_metadata={"step": 1},
        source_metadata={"source": "test"},
    )
    first = service.record_checkpoint(command)
    event_count = len(service.repository.list_events("run-1"))
    second = service.record_checkpoint(command)
    assert second.idempotent_replay is True
    assert second.snapshot == first.snapshot
    assert len(service.repository.list_events("run-1")) == event_count
