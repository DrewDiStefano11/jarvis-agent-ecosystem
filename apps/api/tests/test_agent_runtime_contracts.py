from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from app.models.agent_runtime import (
    MAX_EVENT_PAYLOAD_JSON_LENGTH,
    AgentRunAttempt,
    AgentRunCheckpoint,
    AgentRunSnapshot,
    AgentRunSpecification,
    AgentRunState,
    AttemptState,
    BlockingReason,
    FailureClassification,
    FailureRecord,
    PauseReason,
    QueueAgentRunCommand,
    RecordCheckpointCommand,
    RuntimeEventEnvelope,
    build_run_created_payload,
    canonical_json,
)
from tests.agent_runtime_testkit import create_run, make_service, make_spec, ts


def _metadata_specification(
    value: str, *, metadata_key_order: tuple[str, ...] = ("pad",)
) -> dict[str, object]:
    metadata = {key: value for key in metadata_key_order}
    spec = make_spec().model_dump()
    spec["metadata"] = metadata
    return spec


def _find_run_created_metadata_boundary(*, unicode_value: str = "x") -> tuple[int, int]:
    low = 0
    high = MAX_EVENT_PAYLOAD_JSON_LENGTH
    while low < high:
        mid = (low + high + 1) // 2
        try:
            AgentRunSpecification(**_metadata_specification(unicode_value * mid))
        except ValidationError:
            high = mid - 1
        else:
            low = mid
    return low, low + 1


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("run_id", "   "),
        ("task_id", "task\n1"),
        ("agent_id", "\x01agent"),
        ("idempotency_key", "x" * 201),
    ],
)
def test_invalid_identifiers_are_rejected(field_name: str, value: str) -> None:
    kwargs = make_spec().model_dump()
    kwargs[field_name] = value
    with pytest.raises(ValidationError):
        AgentRunSpecification(**kwargs)


@pytest.mark.parametrize(
    "value",
    [datetime(2026, 1, 1), datetime(2026, 1, 1, 0, 0)],
)
def test_naive_datetimes_are_rejected(value: datetime) -> None:
    with pytest.raises(ValidationError):
        AgentRunSpecification(**(make_spec().model_dump() | {"created_at": value}))


def test_secret_bearing_metadata_keys_are_rejected() -> None:
    with pytest.raises(ValidationError):
        AgentRunSpecification(
            **(make_spec().model_dump() | {"metadata": {"api_key": "safe-looking"}})
        )


def test_excessive_metadata_nesting_is_rejected() -> None:
    metadata = {"a": {"b": {"c": {"d": {"e": {"f": 1}}}}}}
    with pytest.raises(ValidationError):
        AgentRunSpecification(**(make_spec().model_dump() | {"metadata": metadata}))


@pytest.mark.parametrize("metadata", [{" a ": 1, "a": 2}, {"a": 2, " a ": 1}])
def test_colliding_normalized_metadata_keys_are_rejected(metadata: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        AgentRunSpecification(**(make_spec().model_dump() | {"metadata": metadata}))


def test_nested_colliding_normalized_metadata_keys_are_rejected() -> None:
    with pytest.raises(ValidationError):
        AgentRunSpecification(
            **(make_spec().model_dump() | {"metadata": {"nested": {" a ": 1, "a": 2}}})
        )


def test_noncolliding_normalized_metadata_keys_remain_valid_and_deterministic() -> None:
    specification = AgentRunSpecification(
        **(make_spec().model_dump() | {"metadata": {" a ": 1, "b": {" c ": 2}, "d": 3}})
    )
    assert specification.metadata == {"a": 1, "b": {"c": 2}, "d": 3}
    other = AgentRunSpecification(
        **(make_spec().model_dump() | {"metadata": {"d": 3, "b": {"c": 2}, "a": 1}})
    )
    assert canonical_json(specification.metadata) == canonical_json(other.metadata)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: QueueAgentRunCommand(
            run_id="run-1",
            command_id="cmd-queue",
            expected_run_version=0,
            timestamp=ts(1),
            actor_reference="actor-1",
            source_metadata={" a ": 1, "a": 2},
        ),
        lambda: RecordCheckpointCommand(
            run_id="run-1",
            command_id="cmd-checkpoint",
            expected_run_version=0,
            timestamp=ts(1),
            actor_reference="actor-1",
            state_reference="checkpoint://state/1",
            integrity_digest="sha256:aaaaaaaaaaaaaaaa",
            checkpoint_metadata={" a ": 1, "a": 2},
            source_metadata={"source": "test"},
        ),
        lambda: RuntimeEventEnvelope(
            event_id="event-1",
            event_type="run_queued",
            run_id="run-1",
            sequence_number=1,
            run_version=1,
            timestamp=ts(1),
            payload={"detail": "Queued for execution"},
            metadata={" a ": 1, "a": 2},
        ),
    ],
)
def test_ambiguous_metadata_is_rejected_across_command_checkpoint_and_event_contracts(
    factory,
) -> None:
    with pytest.raises(ValidationError):
        factory()


def test_run_specification_boundary_that_fits_wrapped_run_created_payload_is_accepted() -> None:
    accepted, rejected = _find_run_created_metadata_boundary()
    specification = AgentRunSpecification(**_metadata_specification("x" * accepted))
    payload = build_run_created_payload(specification.model_dump(mode="json"))
    assert len(canonical_json(payload)) <= MAX_EVENT_PAYLOAD_JSON_LENGTH
    service = make_service()
    created = create_run(service, specification=specification)
    assert created.snapshot is not None
    assert created.snapshot.specification.metadata["pad"] == "x" * accepted
    with pytest.raises(ValidationError):
        AgentRunSpecification(**_metadata_specification("x" * rejected))


def test_run_creation_rejects_metadata_that_overflows_wrapped_event_boundary_without_mutation() -> (
    None
):
    accepted, rejected = _find_run_created_metadata_boundary()
    assert rejected > accepted
    service = make_service()
    with pytest.raises(ValidationError):
        specification = AgentRunSpecification(**_metadata_specification("x" * rejected))
        create_run(service, specification=specification)
    assert service.repository.load_run("run-1") is None


def test_run_specification_boundary_is_canonical_and_order_independent() -> None:
    accepted, _ = _find_run_created_metadata_boundary()
    value = "x" * max(accepted // 4, 1)
    ordered = AgentRunSpecification(
        **_metadata_specification(value, metadata_key_order=("a", "b", "c", "d"))
    )
    reordered = AgentRunSpecification(
        **_metadata_specification(value, metadata_key_order=("d", "c", "b", "a"))
    )
    assert len(canonical_json(build_run_created_payload(ordered.model_dump(mode="json")))) == len(
        canonical_json(build_run_created_payload(reordered.model_dump(mode="json")))
    )


def test_run_specification_boundary_uses_same_unicode_serialization_rule() -> None:
    accepted, rejected = _find_run_created_metadata_boundary(unicode_value="é")
    specification = AgentRunSpecification(**_metadata_specification("é" * accepted))
    payload = build_run_created_payload(specification.model_dump(mode="json"))
    assert len(canonical_json(payload)) <= MAX_EVENT_PAYLOAD_JSON_LENGTH
    with pytest.raises(ValidationError):
        AgentRunSpecification(**_metadata_specification("é" * rejected))


def test_invalid_attempt_counts_are_rejected() -> None:
    with pytest.raises(ValidationError):
        AgentRunSpecification(**(make_spec().model_dump() | {"maximum_permitted_attempts": 0}))


def test_invalid_checkpoint_digest_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AgentRunCheckpoint(
            checkpoint_id="checkpoint-1",
            run_id="run-1",
            attempt_id="attempt-1",
            checkpoint_sequence=1,
            run_version=1,
            event_sequence=1,
            timestamp=ts(1),
            state_reference="state-ref-1",
            integrity_digest="not-a-valid-digest",
        )


def test_invalid_state_values_are_rejected() -> None:
    with pytest.raises(ValidationError):
        AgentRunSnapshot(
            specification=make_spec(),
            state="not-a-state",
            version=0,
            event_sequence_number=0,
            attempt_count=0,
            created_at=ts(0),
        )


def test_snapshot_requires_internal_timestamp_consistency() -> None:
    with pytest.raises(ValidationError):
        AgentRunSnapshot(
            specification=make_spec(),
            state=AgentRunState.QUEUED,
            version=1,
            event_sequence_number=1,
            attempt_count=0,
            created_at=ts(2),
            queued_at=ts(1),
        )


def test_snapshot_allows_repeated_pause_cycles_using_latest_occurrence_fields() -> None:
    snapshot = AgentRunSnapshot(
        specification=make_spec(),
        state=AgentRunState.PAUSED,
        version=5,
        event_sequence_number=5,
        attempt_count=1,
        active_attempt_id="attempt-1",
        created_at=ts(0),
        queued_at=ts(1),
        claimed_at=ts(2),
        started_at=ts(3),
        last_heartbeat_at=ts(8),
        paused_at=ts(10),
        resumed_at=ts(7),
        pause_reason=PauseReason(
            code="operator_pause",
            detail="Pause requested again",
            timestamp=ts(9),
            requested_by="operator-1",
            resume_state=AgentRunState.RUNNING,
        ),
    )
    assert snapshot.paused_at == ts(10)
    assert snapshot.resumed_at == ts(7)
    assert snapshot.last_heartbeat_at == ts(8)


def test_snapshot_allows_repeated_block_cycles_with_latest_resume_timestamp() -> None:
    snapshot = AgentRunSnapshot(
        specification=make_spec(),
        state=AgentRunState.BLOCKED,
        version=5,
        event_sequence_number=5,
        attempt_count=1,
        active_attempt_id="attempt-1",
        created_at=ts(0),
        queued_at=ts(1),
        claimed_at=ts(2),
        started_at=ts(3),
        last_heartbeat_at=ts(4),
        paused_at=ts(5),
        resumed_at=ts(6),
        blocking_reason=BlockingReason(
            code="dependency_wait",
            detail="Waiting on a dependency again",
            timestamp=ts(7),
            resume_state=AgentRunState.RUNNING,
        ),
    )
    assert snapshot.blocking_reason is not None
    assert snapshot.blocking_reason.timestamp == ts(7)


def test_snapshot_rejects_genuinely_backward_pause_and_heartbeat_timestamps() -> None:
    with pytest.raises(ValidationError):
        AgentRunSnapshot(
            specification=make_spec(),
            state=AgentRunState.PAUSED,
            version=5,
            event_sequence_number=5,
            attempt_count=1,
            active_attempt_id="attempt-1",
            created_at=ts(0),
            queued_at=ts(1),
            claimed_at=ts(2),
            started_at=ts(3),
            last_heartbeat_at=ts(2),
            paused_at=ts(4),
            pause_reason=PauseReason(
                code="operator_pause",
                detail="Invalid pause",
                timestamp=ts(5),
                requested_by="operator-1",
                resume_state=AgentRunState.RUNNING,
            ),
        )


def test_attempt_contract_requires_terminal_outcome_consistency() -> None:
    with pytest.raises(ValidationError):
        AgentRunAttempt(
            attempt_id="attempt-1",
            run_id="run-1",
            attempt_number=1,
            state=AttemptState.SUCCEEDED,
            started_at=ts(1),
            finished_at=ts(2),
        )


def test_failure_record_requires_safe_detail() -> None:
    with pytest.raises(ValidationError):
        FailureRecord(
            category=FailureClassification.INTERNAL,
            detail="Bearer secret-token-value",
            timestamp=ts(1),
        )
