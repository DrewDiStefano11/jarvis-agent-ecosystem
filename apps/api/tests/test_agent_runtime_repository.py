from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from pydantic import ValidationError

from app.agent_runtime.errors import (
    CommandConflictError,
    InvalidRuntimeMetadataError,
    InvalidTransitionError,
    LedgerReplayError,
    LedgerSequenceError,
    RunAlreadyExistsError,
    VersionConflictError,
)
from app.agent_runtime.repository import InMemoryAgentRuntimeRepository
from app.models.agent_runtime import AgentRunQuery, QueueAgentRunCommand
from tests.agent_runtime_testkit import create_run, make_service, make_spec, ts


def _install_create_commit_barrier(
    service, monkeypatch: pytest.MonkeyPatch, *, parties: int
) -> None:
    barrier = Barrier(parties)
    original_commit = service.repository.commit_command

    def wrapped_commit(*args, **kwargs):
        if kwargs.get("create"):
            barrier.wait()
        return original_commit(*args, **kwargs)

    monkeypatch.setattr(service.repository, "commit_command", wrapped_commit)


def _run_concurrent(callables: list[Callable[[], object]]) -> list[object]:
    with ThreadPoolExecutor(max_workers=len(callables)) as executor:
        futures = [executor.submit(func) for func in callables]
    results: list[object] = []
    for future in futures:
        try:
            results.append(future.result())
        except Exception as exc:  # noqa: BLE001
            results.append(exc)
    return results


def test_repository_returns_safe_copies() -> None:
    service = make_service()
    create_run(service)
    snapshot = service.repository.load_run("run-1")
    assert snapshot is not None
    mutated = snapshot.model_copy(update={"status_detail": "mutated"})
    assert mutated.status_detail == "mutated"
    assert service.repository.load_run("run-1").status_detail != "mutated"


def test_repository_query_ordering_and_pagination_are_deterministic() -> None:
    service = make_service()
    create_run(service, specification=make_spec(run_id="run-2", created_at=ts(2)))
    create_run(service, specification=make_spec(run_id="run-1", created_at=ts(1)))
    result = service.repository.query_runs(AgentRunQuery(limit=1))
    assert [item.specification.run_id for item in result.items] == ["run-1"]
    assert result.next_offset == 1
    second = service.repository.query_runs(AgentRunQuery(limit=1, offset=1))
    assert [item.specification.run_id for item in second.items] == ["run-2"]


def test_repository_instances_are_isolated() -> None:
    first = InMemoryAgentRuntimeRepository()
    second = InMemoryAgentRuntimeRepository()
    service_one = make_service()
    create_run(service_one)
    assert first.load_run("run-1") is None
    assert second.load_run("run-1") is None


def test_save_run_accepts_only_snapshots_supported_by_the_ledger() -> None:
    service = make_service()
    create_run(service)
    snapshot = service.repository.load_run("run-1")
    assert snapshot is not None
    service.repository.save_run(snapshot, expected_version=1)
    assert service.repository.load_run("run-1") == snapshot


def test_save_run_rejects_snapshot_only_mutations_without_events() -> None:
    service = make_service()
    create_run(service)
    snapshot = service.repository.load_run("run-1")
    assert snapshot is not None
    before_snapshot = service.repository.load_run("run-1")
    before_events = service.repository.list_events("run-1")
    before_attempts = service.repository.load_attempt_history("run-1")
    before_checkpoints = service.repository.list_checkpoints("run-1")
    before_processed = service.repository.get_processed_command("run-1", "cmd-missing")
    with pytest.raises(VersionConflictError):
        service.repository.save_run(
            snapshot.model_copy(update={"status_detail": "mutated without event"}),
            expected_version=1,
        )
    with pytest.raises(VersionConflictError):
        service.repository.save_run(
            snapshot.model_copy(update={"queued_at": ts(2)}),
            expected_version=1,
        )
    assert service.repository.load_run("run-1") == before_snapshot
    assert service.repository.list_events("run-1") == before_events
    assert service.repository.load_attempt_history("run-1") == before_attempts
    assert service.repository.list_checkpoints("run-1") == before_checkpoints
    assert service.repository.get_processed_command("run-1", "cmd-missing") == before_processed
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
    assert service.repository.load_run("run-1").state.value == "queued"


def test_save_run_honors_expected_version_conflicts() -> None:
    service = make_service()
    create_run(service)
    snapshot = service.repository.load_run("run-1")
    assert snapshot is not None
    with pytest.raises(VersionConflictError):
        service.repository.save_run(snapshot, expected_version=0)


def test_append_events_enforces_expected_sequence() -> None:
    service = make_service()
    create_run(service)
    event = service.repository.list_events("run-1")[0]
    with pytest.raises(LedgerSequenceError):
        service.repository.append_events("run-1", [event], expected_sequence=99)


def test_processed_command_lookup_returns_record() -> None:
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
    record = service.repository.get_processed_command("run-1", "cmd-queue")
    assert record is not None
    assert record.result.snapshot is not None
    assert record.result.snapshot.state.value == "queued"


def test_query_filters_by_terminal_and_agent_and_parent() -> None:
    service = make_service()
    create_run(service, specification=make_spec(run_id="run-1", agent_id="agent-1"))
    create_run(
        service, specification=make_spec(run_id="run-2", agent_id="agent-2", parent_run_id="run-1")
    )
    result = service.repository.query_runs(AgentRunQuery(agent_id="agent-2", parent_run_id="run-1"))
    assert [item.specification.run_id for item in result.items] == ["run-2"]


def test_query_returns_safe_empty_result() -> None:
    service = make_service()
    result = service.repository.query_runs(AgentRunQuery(run_id="missing-run"))
    assert result.items == ()
    assert result.total_count == 0


def test_valid_parent_chain_resolves_in_deterministic_order() -> None:
    service = make_service()
    create_run(service, specification=make_spec(run_id="run-1"))
    create_run(
        service,
        specification=make_spec(run_id="run-2", parent_run_id="run-1"),
        command_id="cmd-create-2",
    )
    create_run(
        service,
        specification=make_spec(run_id="run-3", parent_run_id="run-2"),
        command_id="cmd-create-3",
    )
    lineage = service.resolve_lineage("run-3")
    assert [entry.run_id for entry in lineage.entries] == ["run-2", "run-1"]
    assert all(entry.exists for entry in lineage.entries)


def test_self_parent_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_spec(run_id="run-self", parent_run_id="run-self")


def test_two_node_cycle_is_rejected() -> None:
    service = make_service()
    create_run(service, specification=make_spec(run_id="run-1", parent_run_id="run-2"))
    with pytest.raises(InvalidRuntimeMetadataError):
        create_run(
            service,
            specification=make_spec(run_id="run-2", parent_run_id="run-1"),
            command_id="cmd-create-2",
            timestamp=ts(1),
        )


def test_multi_hop_cycle_is_rejected() -> None:
    service = make_service()
    create_run(service, specification=make_spec(run_id="run-1", parent_run_id="run-2"))
    create_run(
        service,
        specification=make_spec(run_id="run-2", parent_run_id="run-3"),
        command_id="cmd-create-2",
        timestamp=ts(1),
    )
    with pytest.raises(InvalidRuntimeMetadataError):
        create_run(
            service,
            specification=make_spec(run_id="run-3", parent_run_id="run-1"),
            command_id="cmd-create-3",
            timestamp=ts(2),
        )


def test_lineage_depth_is_bounded() -> None:
    service = make_service()
    create_run(service, specification=make_spec(run_id="run-33"))
    for index in range(32, 0, -1):
        create_run(
            service,
            specification=make_spec(run_id=f"run-{index}", parent_run_id=f"run-{index + 1}"),
            command_id=f"cmd-create-{index}",
            timestamp=ts(index),
        )
    with pytest.raises(InvalidRuntimeMetadataError):
        create_run(
            service,
            specification=make_spec(run_id="run-0", parent_run_id="run-1"),
            command_id="cmd-create-0",
            timestamp=ts(34),
        )


def test_missing_parent_policy_is_explicit() -> None:
    service = make_service()
    create_run(service, specification=make_spec(run_id="run-1", parent_run_id="missing-parent"))
    lineage = service.resolve_lineage("run-1")
    assert lineage.missing_parent_id == "missing-parent"
    assert [entry.run_id for entry in lineage.entries] == ["missing-parent"]
    assert lineage.entries[0].exists is False


@pytest.mark.parametrize("iteration", range(3))
def test_concurrent_mutual_parent_creation_is_atomic(
    monkeypatch: pytest.MonkeyPatch, iteration: int
) -> None:
    service = make_service()
    _install_create_commit_barrier(service, monkeypatch, parties=2)

    def create_a() -> object:
        return create_run(
            service,
            specification=make_spec(
                run_id=f"run-a-{iteration}", parent_run_id=f"run-b-{iteration}"
            ),
            command_id=f"cmd-create-a-{iteration}",
            timestamp=ts(0),
        )

    def create_b() -> object:
        return create_run(
            service,
            specification=make_spec(
                run_id=f"run-b-{iteration}", parent_run_id=f"run-a-{iteration}"
            ),
            command_id=f"cmd-create-b-{iteration}",
            timestamp=ts(0),
        )

    results = _run_concurrent([create_a, create_b])
    successes = [item for item in results if hasattr(item, "snapshot")]
    failures = [item for item in results if isinstance(item, InvalidRuntimeMetadataError)]
    assert len(successes) == 1
    assert len(failures) == 1
    stored_run_ids = [
        run_id
        for run_id in [f"run-a-{iteration}", f"run-b-{iteration}"]
        if service.repository.load_run(run_id) is not None
    ]
    assert len(stored_run_ids) == 1
    successful_run_id = stored_run_ids[0]
    lineage = service.resolve_lineage(successful_run_id)
    assert all(entry.run_id != successful_run_id for entry in lineage.entries)
    failed_run_id = (
        f"run-b-{iteration}" if successful_run_id == f"run-a-{iteration}" else f"run-a-{iteration}"
    )
    failed_command_id = (
        f"cmd-create-b-{iteration}"
        if failed_run_id == f"run-b-{iteration}"
        else f"cmd-create-a-{iteration}"
    )
    assert service.repository.load_run(failed_run_id) is None
    assert service.repository.get_processed_command(failed_run_id, failed_command_id) is None


def test_existing_chain_closure_is_rejected_atomically() -> None:
    service = make_service()
    create_run(service, specification=make_spec(run_id="run-a", parent_run_id="run-b"))
    create_run(
        service,
        specification=make_spec(run_id="run-b", parent_run_id="run-c"),
        command_id="cmd-create-b",
        timestamp=ts(1),
    )
    before_a = service.repository.load_run("run-a")
    before_b = service.repository.load_run("run-b")
    with pytest.raises(InvalidRuntimeMetadataError):
        create_run(
            service,
            specification=make_spec(run_id="run-c", parent_run_id="run-a"),
            command_id="cmd-create-c",
            timestamp=ts(2),
        )
    assert service.repository.load_run("run-a") == before_a
    assert service.repository.load_run("run-b") == before_b
    assert service.repository.load_run("run-c") is None


@pytest.mark.parametrize("iteration", range(3))
def test_concurrent_three_run_cycle_is_rejected_atomically(
    monkeypatch: pytest.MonkeyPatch, iteration: int
) -> None:
    service = make_service()
    _install_create_commit_barrier(service, monkeypatch, parties=3)

    callables = [
        lambda: create_run(
            service,
            specification=make_spec(
                run_id=f"run-a-{iteration}", parent_run_id=f"run-b-{iteration}"
            ),
            command_id=f"cmd-a-{iteration}",
            timestamp=ts(0),
        ),
        lambda: create_run(
            service,
            specification=make_spec(
                run_id=f"run-b-{iteration}", parent_run_id=f"run-c-{iteration}"
            ),
            command_id=f"cmd-b-{iteration}",
            timestamp=ts(0),
        ),
        lambda: create_run(
            service,
            specification=make_spec(
                run_id=f"run-c-{iteration}", parent_run_id=f"run-a-{iteration}"
            ),
            command_id=f"cmd-c-{iteration}",
            timestamp=ts(0),
        ),
    ]
    results = _run_concurrent(callables)
    assert sum(hasattr(item, "snapshot") for item in results) == 2
    assert sum(isinstance(item, InvalidRuntimeMetadataError) for item in results) == 1
    for run_id in [f"run-a-{iteration}", f"run-b-{iteration}", f"run-c-{iteration}"]:
        snapshot = service.repository.load_run(run_id)
        if snapshot is not None:
            lineage = service.resolve_lineage(run_id)
            assert all(entry.run_id != run_id for entry in lineage.entries)


@pytest.mark.parametrize("iteration", range(3))
def test_valid_concurrent_creation_remains_supported(
    monkeypatch: pytest.MonkeyPatch, iteration: int
) -> None:
    service = make_service()
    _install_create_commit_barrier(service, monkeypatch, parties=2)

    def create_first() -> object:
        return create_run(
            service,
            specification=make_spec(run_id=f"run-root-{iteration}"),
            command_id=f"cmd-root-{iteration}",
            timestamp=ts(0),
        )

    def create_second() -> object:
        return create_run(
            service,
            specification=make_spec(
                run_id=f"run-child-{iteration}", parent_run_id="missing-parent"
            ),
            command_id=f"cmd-child-{iteration}",
            timestamp=ts(0),
        )

    results = _run_concurrent([create_first, create_second])
    assert all(hasattr(item, "snapshot") for item in results)
    assert all(not getattr(item, "idempotent_replay", False) for item in results)


def test_concurrent_conflicting_create_command_reuse_raises_command_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = make_service()
    _install_create_commit_barrier(service, monkeypatch, parties=2)

    def first() -> object:
        return create_run(
            service,
            specification=make_spec(run_id="run-conflict", parent_run_id="missing-a"),
            command_id="cmd-create-conflict",
            timestamp=ts(0),
        )

    def second() -> object:
        try:
            return create_run(
                service,
                specification=make_spec(run_id="run-conflict", parent_run_id="missing-b"),
                command_id="cmd-create-conflict",
                timestamp=ts(0),
            )
        except Exception as exc:  # noqa: BLE001
            return exc

    outcomes = _run_concurrent([first, second])
    assert sum(hasattr(item, "snapshot") for item in outcomes) == 1
    assert sum(isinstance(item, CommandConflictError) for item in outcomes) == 1


def test_direct_repository_create_run_requires_matching_nonempty_ledger() -> None:
    service = make_service()
    created = create_run(service)
    snapshot = created.snapshot
    assert snapshot is not None
    events = service.repository.list_events("run-1")
    repository = InMemoryAgentRuntimeRepository()
    repository.create_run(snapshot, events=events)
    stored = repository.load_run("run-1")
    assert stored == snapshot
    events.append(events[0].model_copy())
    assert len(repository.list_events("run-1")) == 1


def test_direct_repository_create_run_rejects_empty_and_malformed_ledgers_without_mutation() -> (
    None
):
    service = make_service()
    created = create_run(service)
    snapshot = created.snapshot
    assert snapshot is not None
    repository = InMemoryAgentRuntimeRepository()
    with pytest.raises(LedgerSequenceError):
        repository.create_run(snapshot)
    malformed_events = service.repository.list_events("run-1")[1:]
    with pytest.raises((LedgerSequenceError, LedgerReplayError, InvalidTransitionError)):
        repository.create_run(snapshot, events=malformed_events)
    assert repository.load_run("run-1") is None


def test_direct_repository_create_run_rejects_mismatched_snapshot_or_unrelated_events() -> None:
    service = make_service()
    first = create_run(service)
    first_snapshot = first.snapshot
    assert first_snapshot is not None
    first_events = service.repository.list_events("run-1")
    second_service = make_service()
    create_run(second_service, specification=make_spec(run_id="run-2"), command_id="cmd-create-2")
    second_events = second_service.repository.list_events("run-2")
    repository = InMemoryAgentRuntimeRepository()
    with pytest.raises(VersionConflictError):
        repository.create_run(
            first_snapshot.model_copy(update={"status_detail": "mutated"}), events=first_events
        )
    with pytest.raises(VersionConflictError):
        repository.create_run(first_snapshot, events=second_events)
    assert repository.load_run("run-1") is None


def test_direct_repository_create_run_rejection_is_atomic_and_duplicate_detection_is_preserved() -> (
    None
):
    service = make_service()
    first = create_run(service)
    snapshot = first.snapshot
    assert snapshot is not None
    events = service.repository.list_events("run-1")
    repository = InMemoryAgentRuntimeRepository()
    repository.create_run(snapshot, events=events)
    before_snapshot = repository.load_run("run-1")
    before_events = repository.list_events("run-1")
    with pytest.raises(RunAlreadyExistsError):
        repository.create_run(snapshot, events=events)
    assert repository.load_run("run-1") == before_snapshot
    assert repository.list_events("run-1") == before_events


def test_direct_repository_create_run_rejects_lineage_cycle_closure() -> None:
    service = make_service()
    first = create_run(service, specification=make_spec(run_id="run-a", parent_run_id="run-b"))
    second = create_run(
        service,
        specification=make_spec(run_id="run-b", parent_run_id="run-c"),
        command_id="cmd-create-b",
        timestamp=ts(1),
    )
    cycle_service = make_service()
    cycle = create_run(
        cycle_service,
        specification=make_spec(run_id="run-c", parent_run_id="run-a"),
        command_id="cmd-create-c",
        timestamp=ts(2),
    )
    repository = InMemoryAgentRuntimeRepository()
    assert first.snapshot is not None and second.snapshot is not None and cycle.snapshot is not None
    repository.create_run(first.snapshot, events=service.repository.list_events("run-a"))
    repository.create_run(second.snapshot, events=service.repository.list_events("run-b"))
    before_runs = {"run-a": repository.load_run("run-a"), "run-b": repository.load_run("run-b")}
    with pytest.raises(InvalidRuntimeMetadataError):
        repository.create_run(cycle.snapshot, events=cycle_service.repository.list_events("run-c"))
    assert repository.load_run("run-a") == before_runs["run-a"]
    assert repository.load_run("run-b") == before_runs["run-b"]
    assert repository.load_run("run-c") is None


def test_direct_repository_create_run_rejects_depth_limit_exceeded() -> None:
    service = make_service()
    repository = InMemoryAgentRuntimeRepository()
    current_run_id = "run-33"
    created = create_run(service, specification=make_spec(run_id=current_run_id))
    repository.create_run(created.snapshot, events=service.repository.list_events(current_run_id))
    for index in range(32, 0, -1):
        run_id = f"run-{index}"
        temp_service = make_service()
        created = create_run(
            temp_service,
            specification=make_spec(run_id=run_id, parent_run_id=current_run_id),
            command_id=f"cmd-create-{index}",
            timestamp=ts(index),
        )
        repository.create_run(created.snapshot, events=temp_service.repository.list_events(run_id))
        current_run_id = run_id
    temp_service = make_service()
    too_deep = create_run(
        temp_service,
        specification=make_spec(run_id="run-0", parent_run_id=current_run_id),
        command_id="cmd-create-0",
        timestamp=ts(34),
    )
    with pytest.raises(InvalidRuntimeMetadataError):
        repository.create_run(
            too_deep.snapshot, events=temp_service.repository.list_events("run-0")
        )
    assert repository.load_run("run-0") is None
