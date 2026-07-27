from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from app.models.agent_runtime import (
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
)
from tests.agent_runtime_testkit import make_spec, ts


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
