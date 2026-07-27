from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.agent_runtime.errors import (
    InvalidRuntimeMetadataError,
    LedgerSequenceError,
    VersionConflictError,
)
from app.agent_runtime.repository import InMemoryAgentRuntimeRepository
from app.models.agent_runtime import AgentRunQuery, QueueAgentRunCommand
from tests.agent_runtime_testkit import create_run, make_service, make_spec, ts


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
