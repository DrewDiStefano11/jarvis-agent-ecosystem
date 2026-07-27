from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock

import pytest

from app.agent_runtime.errors import (
    CommandConflictError,
    LedgerSequenceError,
    VersionConflictError,
)
from app.models.agent_runtime import (
    AbandonAttemptCommand,
    FailAttemptCommand,
    QueueAgentRunCommand,
    RecordCheckpointCommand,
    RequestCancellationCommand,
    RequestRecoveryPlanCommand,
    TimeoutAttemptCommand,
)
from tests.agent_runtime_testkit import create_run, make_service, prepare_running_run, ts


def _install_commit_barrier(service, monkeypatch: pytest.MonkeyPatch, *, parties: int = 2) -> None:
    barrier = Barrier(parties)
    original_commit = service.repository.commit_command

    def wrapped_commit(*args, **kwargs):
        barrier.wait()
        return original_commit(*args, **kwargs)

    monkeypatch.setattr(service.repository, "commit_command", wrapped_commit)


def _run_in_executor(callables: list[Callable[[], object]]) -> list[object]:
    with ThreadPoolExecutor(max_workers=len(callables)) as executor:
        futures = [executor.submit(func) for func in callables]
    results: list[object] = []
    for future in futures:
        try:
            results.append(future.result())
        except Exception as exc:  # noqa: BLE001
            results.append(exc)
    return results


def _install_precommit_race(
    service,
    monkeypatch: pytest.MonkeyPatch,
    *,
    run_id: str,
) -> None:
    stale_aggregate = service._load_current(run_id).model_copy(deep=True)
    monkeypatch.setattr(
        service,
        "_load_current",
        lambda current_run_id: stale_aggregate.model_copy(deep=True),
    )
    original_list_events = service.repository.list_events
    original_commit = service.repository.commit_command
    call_lock = Lock()
    list_event_calls = {"count": 0}
    first_commit_done = Event()

    def wrapped_list_events(current_run_id: str):
        with call_lock:
            list_event_calls["count"] += 1
            count = list_event_calls["count"]
        if count == 2:
            assert first_commit_done.wait(timeout=5)
        return original_list_events(current_run_id)

    def wrapped_commit(*args, **kwargs):
        result = original_commit(*args, **kwargs)
        first_commit_done.set()
        return result

    monkeypatch.setattr(service.repository, "list_events", wrapped_list_events)
    monkeypatch.setattr(service.repository, "commit_command", wrapped_commit)


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


@pytest.mark.parametrize(
    ("command", "conflicting"),
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
            FailAttemptCommand(
                run_id="run-1",
                command_id="cmd-fail",
                expected_run_version=6,
                timestamp=ts(5),
                actor_reference="worker-1",
                failure_category="dependency",
                failure_detail="Different failure detail",
                source_metadata={"source": "test"},
            ),
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
            TimeoutAttemptCommand(
                run_id="run-1",
                command_id="cmd-timeout",
                expected_run_version=6,
                timestamp=ts(6),
                actor_reference="worker-1",
                source_metadata={"source": "test"},
            ),
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
            AbandonAttemptCommand(
                run_id="run-1",
                command_id="cmd-abandon",
                expected_run_version=6,
                timestamp=ts(5),
                actor_reference="worker-1",
                detail="Different abandonment detail",
                source_metadata={"source": "test"},
            ),
        ),
    ],
)
def test_failure_replay_preserves_resolved_attempt_id_and_conflicting_replay_is_rejected(
    command,
    conflicting,
) -> None:
    service = make_service()
    prepare_running_run(service)
    active_attempt_id = service.repository.load_attempt_history("run-1")[-1].attempt_id
    first = service.handle(command)
    assert first.snapshot is not None
    assert first.snapshot.failure is not None
    assert first.snapshot.failure.attempt_id == active_attempt_id
    replay = service.handle(command)
    assert replay.idempotent_replay is True
    assert replay.snapshot is not None
    assert replay.snapshot.failure is not None
    assert replay.snapshot.failure.attempt_id == active_attempt_id
    with pytest.raises(CommandConflictError):
        service.handle(conflicting)


def test_concurrent_duplicate_state_change_is_atomically_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = make_service()
    create_run(service)
    _install_commit_barrier(service, monkeypatch)

    def submit() -> object:
        return service.queue_run(
            QueueAgentRunCommand(
                run_id="run-1",
                command_id="cmd-queue-concurrent",
                expected_run_version=1,
                timestamp=ts(1),
                actor_reference="scheduler-1",
                source_metadata={"source": "test"},
            )
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [future.result() for future in [executor.submit(submit), executor.submit(submit)]]

    assert len(service.repository.list_events("run-1")) == 2
    assert [result.snapshot.state.value for result in results] == ["queued", "queued"]
    assert sorted(result.idempotent_replay for result in results) == [False, True]


def test_concurrent_conflicting_command_ids_raise_command_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = make_service()
    create_run(service)
    _install_commit_barrier(service, monkeypatch)

    def submit(detail: str) -> object:
        try:
            return service.queue_run(
                QueueAgentRunCommand(
                    run_id="run-1",
                    command_id="cmd-queue-conflict",
                    expected_run_version=1,
                    timestamp=ts(1),
                    actor_reference="scheduler-1",
                    detail=detail,
                    source_metadata={"source": "test"},
                )
            )
        except Exception as exc:  # noqa: BLE001
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = [
            future.result()
            for future in [executor.submit(submit, "first"), executor.submit(submit, "second")]
        ]

    outcomes = [first, second]
    assert sum(isinstance(item, CommandConflictError) for item in outcomes) == 1
    assert sum(hasattr(item, "snapshot") for item in outcomes) == 1
    assert not any(
        isinstance(item, VersionConflictError | LedgerSequenceError) for item in outcomes
    )
    assert len(service.repository.list_events("run-1")) == 2
    record = service.repository.get_processed_command("run-1", "cmd-queue-conflict")
    assert record is not None
    assert record.result.snapshot is not None
    assert record.result.snapshot.state.value == "queued"


def test_concurrent_duplicate_run_creation_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = make_service()
    _install_commit_barrier(service, monkeypatch)

    def submit() -> object:
        return create_run(service, command_id="cmd-create-concurrent", timestamp=ts(0))

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = [
            future.result() for future in [executor.submit(submit), executor.submit(submit)]
        ]

    assert first.snapshot == second.snapshot
    assert sorted([first.idempotent_replay, second.idempotent_replay]) == [False, True]
    assert len(service.repository.list_events("run-1")) == 1


def test_concurrent_duplicate_recovery_plan_requests_are_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = make_service()
    prepare_running_run(service)
    service.record_checkpoint(
        RecordCheckpointCommand(
            run_id="run-1",
            command_id="cmd-checkpoint-pre-plan",
            expected_run_version=6,
            timestamp=ts(5),
            actor_reference="worker-1",
            checkpoint_id="checkpoint-1",
            state_reference="checkpoint://state/1",
            integrity_digest="sha256:aaaaaaaaaaaaaaaa",
            source_metadata={"source": "test"},
        )
    )
    service.fail_attempt(
        FailAttemptCommand(
            run_id="run-1",
            command_id="cmd-fail-before-plan",
            expected_run_version=7,
            timestamp=ts(6),
            actor_reference="worker-1",
            failure_category="dependency",
            failure_detail="Dependency unavailable",
            source_metadata={"source": "test"},
        )
    )
    _install_commit_barrier(service, monkeypatch)

    def submit() -> object:
        return service.request_recovery_plan(
            RequestRecoveryPlanCommand(
                run_id="run-1",
                command_id="cmd-plan-concurrent",
                expected_run_version=8,
                timestamp=ts(7),
                actor_reference="operator-1",
                source_metadata={"source": "test"},
            )
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = [
            future.result() for future in [executor.submit(submit), executor.submit(submit)]
        ]

    assert first.snapshot == second.snapshot
    assert first.recovery_plan == second.recovery_plan
    assert sorted([first.idempotent_replay, second.idempotent_replay]) == [False, True]
    assert len(service.repository.list_events("run-1")) == 9


def test_exact_duplicate_state_change_survives_precommit_race_without_ledger_sequence_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = make_service()
    create_run(service)
    _install_precommit_race(service, monkeypatch, run_id="run-1")

    def submit() -> object:
        return service.queue_run(
            QueueAgentRunCommand(
                run_id="run-1",
                command_id="cmd-queue-race",
                expected_run_version=1,
                timestamp=ts(1),
                actor_reference="scheduler-1",
                source_metadata={"source": "test"},
            )
        )

    first, second = _run_in_executor([submit, submit])
    assert not any(
        isinstance(item, VersionConflictError | LedgerSequenceError) for item in [first, second]
    )
    assert all(hasattr(item, "snapshot") for item in [first, second])
    assert sorted([first.idempotent_replay, second.idempotent_replay]) == [False, True]
    assert len(service.repository.list_events("run-1")) == 2


def test_exact_duplicate_recovery_plan_survives_precommit_race_without_ledger_sequence_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = make_service()
    prepare_running_run(service)
    service.record_checkpoint(
        RecordCheckpointCommand(
            run_id="run-1",
            command_id="cmd-checkpoint-before-race",
            expected_run_version=6,
            timestamp=ts(5),
            actor_reference="worker-1",
            checkpoint_id="checkpoint-race",
            state_reference="checkpoint://state/race",
            integrity_digest="sha256:aaaaaaaaaaaaaaaa",
            source_metadata={"source": "test"},
        )
    )
    service.fail_attempt(
        FailAttemptCommand(
            run_id="run-1",
            command_id="cmd-fail-before-race",
            expected_run_version=7,
            timestamp=ts(6),
            actor_reference="worker-1",
            failure_category="dependency",
            failure_detail="Dependency unavailable",
            source_metadata={"source": "test"},
        )
    )
    _install_precommit_race(service, monkeypatch, run_id="run-1")

    def submit() -> object:
        return service.request_recovery_plan(
            RequestRecoveryPlanCommand(
                run_id="run-1",
                command_id="cmd-plan-race",
                expected_run_version=8,
                timestamp=ts(7),
                actor_reference="operator-1",
                source_metadata={"source": "test"},
            )
        )

    first, second = _run_in_executor([submit, submit])
    assert not any(
        isinstance(item, VersionConflictError | LedgerSequenceError) for item in [first, second]
    )
    assert all(hasattr(item, "snapshot") for item in [first, second])
    assert sorted([first.idempotent_replay, second.idempotent_replay]) == [False, True]
    assert len(service.repository.list_events("run-1")) == 9


def test_unexplained_snapshot_ledger_mismatch_still_fails_closed() -> None:
    service = make_service()
    create_run(service)
    with service.repository._lock:
        stored = service.repository._snapshots["run-1"]
        service.repository._snapshots["run-1"] = stored.model_copy(
            update={"status_detail": "corrupted outside ledger"},
            deep=True,
        )
    with pytest.raises(VersionConflictError):
        service.queue_run(
            QueueAgentRunCommand(
                run_id="run-1",
                command_id="cmd-queue-corrupted",
                expected_run_version=1,
                timestamp=ts(1),
                actor_reference="scheduler-1",
                source_metadata={"source": "test"},
            )
        )
