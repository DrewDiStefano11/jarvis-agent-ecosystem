from __future__ import annotations

import pytest

from app.agent_runtime.errors import CommandConflictError, VersionConflictError
from app.models.agent_runtime import QueueAgentRunCommand
from tests.agent_runtime_testkit import create_run, make_service, ts


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
