from __future__ import annotations

import json
import math
import re
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.constraints import MAX_CORRELATION_ID_LENGTH as _MAX_CORRELATION_ID_LENGTH

MAX_IDENTIFIER_LENGTH = 120
# Correlation IDs are preserved exactly across every runtime, outbox, audit,
# dispatcher, and websocket layer; the shared maximum is explicit, never a
# silent truncation point.
MAX_CORRELATION_ID_LENGTH = _MAX_CORRELATION_ID_LENGTH
MAX_IDEMPOTENCY_KEY_LENGTH = 200
MAX_TEXT_LENGTH = 2_000
MAX_METADATA_DEPTH = 5
MAX_METADATA_KEYS = 64
MAX_METADATA_ITEMS = 128
MAX_METADATA_JSON_LENGTH = 20_000
MAX_EVENT_PAYLOAD_JSON_LENGTH = MAX_METADATA_JSON_LENGTH
RUN_CREATED_EVENT_DETAIL = "Run created"
SUPPORTED_EVENT_SCHEMA_VERSION = "1.0"
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100
DEFAULT_LINEAGE_DEPTH_LIMIT = 32

_CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x1F\x7F]")
_SECRET_KEY_PATTERN = re.compile(
    r"(?:api[_-]?key|secret|password|credential|authorization|private[_-]?key)",
    re.IGNORECASE,
)
_TOKEN_SECRET_KEY_PATTERN = re.compile(
    r"(?:^|[_-])(?:token|access[_-]?token|refresh[_-]?token|lease[_-]?token|auth[_-]?token)"
    r"(?:$|[_-])",
    re.IGNORECASE,
)
_SECRET_VALUE_PATTERNS = (
    re.compile(r"Bearer\s+[A-Za-z0-9._-]{8,}", re.IGNORECASE),
    re.compile(r"-----BEGIN\s+[A-Z\s]+PRIVATE\s+KEY-----", re.IGNORECASE),
    re.compile(r"(?ix)(?:api[_-]?key|secret|token|password)[\s:=]+['\"]?[A-Za-z0-9._-]{16,}['\"]?"),
)

SafeMetadataValue = Any


class RuntimeContract(BaseModel):
    """Base contract for deterministic agent-runtime domain models."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class StateCategory(StrEnum):
    PRE_EXECUTION = "pre_execution"
    ACTIVE = "active"
    INTERRUPTED = "interrupted"
    CANCELLATION = "cancellation"
    TERMINAL = "terminal"


class AgentRunState(StrEnum):
    CREATED = "created"
    QUEUED = "queued"
    CLAIMED = "claimed"
    STARTING = "starting"
    RUNNING = "running"
    PAUSE_REQUESTED = "pause_requested"
    PAUSED = "paused"
    BLOCKED = "blocked"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    ABANDONED = "abandoned"


class AttemptState(StrEnum):
    CREATED = "created"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    ABANDONED = "abandoned"


class TerminalOutcome(StrEnum):
    SUCCESS = "success"
    CANCELLED = "cancelled"
    FAILURE = "failure"
    TIMEOUT = "timeout"
    ABANDONED = "abandoned"


class FailureClassification(StrEnum):
    VALIDATION = "validation"
    AUTHORIZATION = "authorization"
    DEPENDENCY = "dependency"
    RESOURCE = "resource"
    PROVIDER = "provider"
    TOOL = "tool"
    EXECUTION = "execution"
    CHECKPOINT = "checkpoint"
    TIMEOUT = "timeout"
    CANCELLATION = "cancellation"
    INTERNAL = "internal"
    UNKNOWN = "unknown"


class RecoveryStatus(StrEnum):
    NONE = "none"
    REQUIRED = "required"
    PLANNED = "planned"
    DENIED = "denied"


class AgentRuntimeEventType(StrEnum):
    RUN_CREATED = "run_created"
    RUN_QUEUED = "run_queued"
    RUN_CLAIMED = "run_claimed"
    RUN_START_REQUESTED = "run_start_requested"
    ATTEMPT_CREATED = "attempt_created"
    ATTEMPT_STARTED = "attempt_started"
    HEARTBEAT_RECORDED = "heartbeat_recorded"
    PAUSE_REQUESTED = "pause_requested"
    RUN_PAUSED = "run_paused"
    RUN_RESUMED = "run_resumed"
    RUN_BLOCKED = "run_blocked"
    RUN_UNBLOCKED = "run_unblocked"
    CANCELLATION_REQUESTED = "cancellation_requested"
    CANCELLATION_STARTED = "cancellation_started"
    RUN_CANCELLED = "run_cancelled"
    CHECKPOINT_RECORDED = "checkpoint_recorded"
    ATTEMPT_SUCCEEDED = "attempt_succeeded"
    ATTEMPT_FAILED = "attempt_failed"
    ATTEMPT_TIMED_OUT = "attempt_timed_out"
    ATTEMPT_ABANDONED = "attempt_abandoned"
    RUN_SUCCEEDED = "run_succeeded"
    RUN_FAILED = "run_failed"
    RUN_TIMED_OUT = "run_timed_out"
    RUN_ABANDONED = "run_abandoned"
    RECOVERY_PLANNED = "recovery_planned"


def ensure_utc_datetime(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware UTC")
    return value.astimezone(UTC)


def _contains_secret_like_value(value: str) -> bool:
    return any(pattern.search(value) for pattern in _SECRET_VALUE_PATTERNS)


def validate_identifier(
    value: str, *, field_name: str, max_length: int = MAX_IDENTIFIER_LENGTH
) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    candidate = value.strip()
    if not candidate:
        raise ValueError(f"{field_name} must not be empty")
    if len(candidate) > max_length:
        raise ValueError(f"{field_name} exceeds the {max_length}-character limit")
    if _CONTROL_CHARACTER_PATTERN.search(candidate):
        raise ValueError(f"{field_name} contains control characters")
    if _contains_secret_like_value(candidate):
        raise ValueError(f"{field_name} must not contain secret-bearing content")
    return candidate


def validate_safe_text(
    value: str,
    *,
    field_name: str,
    max_length: int = MAX_TEXT_LENGTH,
    allow_blank: bool = False,
) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    candidate = value.strip()
    if not candidate and not allow_blank:
        raise ValueError(f"{field_name} must not be blank")
    if len(candidate) > max_length:
        raise ValueError(f"{field_name} exceeds the {max_length}-character limit")
    if _CONTROL_CHARACTER_PATTERN.search(candidate):
        raise ValueError(f"{field_name} contains control characters")
    if _contains_secret_like_value(candidate):
        raise ValueError(f"{field_name} must not contain secret-bearing content")
    return candidate


def _normalize_safe_json(
    value: SafeMetadataValue,
    *,
    field_name: str,
    depth: int = 0,
) -> SafeMetadataValue:
    if depth > MAX_METADATA_DEPTH:
        raise ValueError(f"{field_name} exceeds the maximum nesting depth")
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{field_name} must not contain non-finite numbers")
        return value
    if isinstance(value, str):
        if _CONTROL_CHARACTER_PATTERN.search(value):
            raise ValueError(f"{field_name} contains control characters")
        if len(value) > MAX_TEXT_LENGTH:
            raise ValueError(f"{field_name} contains an oversized string value")
        if _contains_secret_like_value(value):
            raise ValueError(f"{field_name} contains secret-bearing content")
        return value
    if isinstance(value, dict):
        if len(value) > MAX_METADATA_KEYS:
            raise ValueError(f"{field_name} contains too many keys")
        normalized: dict[str, SafeMetadataValue] = {}
        for key, nested in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{field_name} keys must be strings")
            normalized_key = validate_identifier(key, field_name=f"{field_name}.key", max_length=80)
            if _SECRET_KEY_PATTERN.search(normalized_key) or _TOKEN_SECRET_KEY_PATTERN.search(
                normalized_key
            ):
                raise ValueError(f"{field_name} contains a secret-bearing key name")
            if normalized_key in normalized:
                raise ValueError(f"{field_name} contains colliding normalized key names")
            normalized[normalized_key] = _normalize_safe_json(
                nested,
                field_name=f"{field_name}.{normalized_key}",
                depth=depth + 1,
            )
        return dict(sorted(normalized.items()))
    if isinstance(value, list | tuple):
        if len(value) > MAX_METADATA_ITEMS:
            raise ValueError(f"{field_name} contains too many list items")
        return [
            _normalize_safe_json(item, field_name=f"{field_name}[{index}]", depth=depth + 1)
            for index, item in enumerate(value)
        ]
    raise ValueError(f"{field_name} contains a non-serializable value")


def normalize_safe_metadata(
    value: dict[str, SafeMetadataValue] | None,
    *,
    field_name: str,
) -> dict[str, SafeMetadataValue]:
    normalized = _normalize_safe_json(value or {}, field_name=field_name)
    assert isinstance(normalized, dict)
    if len(canonical_json(normalized)) > MAX_METADATA_JSON_LENGTH:
        raise ValueError(f"{field_name} exceeds the maximum serialized size")
    return normalized


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def stable_hash(value: Any) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def build_run_created_payload(specification: dict[str, Any]) -> dict[str, Any]:
    return {"specification": specification, "detail": RUN_CREATED_EVENT_DETAIL}


def validate_run_created_payload_size(specification: dict[str, Any]) -> None:
    if (
        len(canonical_json(build_run_created_payload(specification)))
        > MAX_EVENT_PAYLOAD_JSON_LENGTH
    ):
        raise ValueError("run specification exceeds the run_created event payload size limit")


RunId = Annotated[
    str, AfterValidator(lambda value: validate_identifier(value, field_name="run_id"))
]
TaskId = Annotated[
    str, AfterValidator(lambda value: validate_identifier(value, field_name="task_id"))
]
AgentId = Annotated[
    str, AfterValidator(lambda value: validate_identifier(value, field_name="agent_id"))
]
AttemptId = Annotated[
    str, AfterValidator(lambda value: validate_identifier(value, field_name="attempt_id"))
]
CheckpointId = Annotated[
    str,
    AfterValidator(lambda value: validate_identifier(value, field_name="checkpoint_id")),
]
EventId = Annotated[
    str, AfterValidator(lambda value: validate_identifier(value, field_name="event_id"))
]
CommandId = Annotated[
    str,
    AfterValidator(lambda value: validate_identifier(value, field_name="command_id")),
]
CorrelationId = Annotated[
    str,
    AfterValidator(
        lambda value: validate_identifier(
            value, field_name="correlation_id", max_length=MAX_CORRELATION_ID_LENGTH
        )
    ),
]
CausationId = Annotated[
    str,
    AfterValidator(lambda value: validate_identifier(value, field_name="causation_id")),
]
ParentRunId = Annotated[
    str,
    AfterValidator(lambda value: validate_identifier(value, field_name="parent_run_id")),
]
IdempotencyKey = Annotated[
    str,
    AfterValidator(
        lambda value: validate_identifier(
            value,
            field_name="idempotency_key",
            max_length=MAX_IDEMPOTENCY_KEY_LENGTH,
        )
    ),
]
CapabilityKey = Annotated[
    str,
    AfterValidator(
        lambda value: validate_identifier(value, field_name="capability_key", max_length=80)
    ),
]
OpaqueReference = Annotated[
    str,
    AfterValidator(
        lambda value: validate_identifier(value, field_name="opaque_reference", max_length=160)
    ),
]
ReasonCode = Annotated[
    str,
    AfterValidator(
        lambda value: validate_identifier(value, field_name="reason_code", max_length=80)
    ),
]
SchemaVersion = Annotated[
    str,
    AfterValidator(
        lambda value: validate_identifier(value, field_name="schema_version", max_length=20)
    ),
]
DigestValue = Annotated[
    str,
    AfterValidator(
        lambda value: validate_identifier(value, field_name="integrity_digest", max_length=160)
    ),
]


class ExecutionConstraints(RuntimeContract):
    queue_hint: str | None = None
    max_runtime_seconds: int | None = Field(default=None, ge=1, le=7 * 24 * 3600)
    lease_timeout_seconds: int | None = Field(default=None, ge=1, le=24 * 3600)
    priority_class: str | None = None
    locality_hint: str | None = None
    additional: dict[str, SafeMetadataValue] = Field(default_factory=dict)

    @field_validator("queue_hint", "priority_class", "locality_hint")
    @classmethod
    def _validate_text(cls, value: str | None, info: Any) -> str | None:
        if value is None:
            return None
        return validate_safe_text(value, field_name=str(info.field_name), max_length=160)

    @field_validator("additional")
    @classmethod
    def _validate_additional(
        cls, value: dict[str, SafeMetadataValue]
    ) -> dict[str, SafeMetadataValue]:
        return normalize_safe_metadata(value, field_name="execution_constraints.additional")


class FailureRecord(RuntimeContract):
    category: FailureClassification
    detail: str
    timestamp: datetime
    attempt_id: AttemptId | None = None
    metadata: dict[str, SafeMetadataValue] = Field(default_factory=dict)

    @field_validator("detail")
    @classmethod
    def _validate_detail(cls, value: str) -> str:
        return validate_safe_text(value, field_name="failure.detail")

    @field_validator("timestamp")
    @classmethod
    def _validate_timestamp(cls, value: datetime) -> datetime:
        return ensure_utc_datetime(value, field_name="failure.timestamp")

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(
        cls, value: dict[str, SafeMetadataValue]
    ) -> dict[str, SafeMetadataValue]:
        return normalize_safe_metadata(value, field_name="failure.metadata")


class PauseReason(RuntimeContract):
    code: ReasonCode
    detail: str
    timestamp: datetime
    requested_by: OpaqueReference | None = None
    resume_state: AgentRunState
    metadata: dict[str, SafeMetadataValue] = Field(default_factory=dict)

    @field_validator("detail")
    @classmethod
    def _validate_detail(cls, value: str) -> str:
        return validate_safe_text(value, field_name="pause.detail")

    @field_validator("timestamp")
    @classmethod
    def _validate_timestamp(cls, value: datetime) -> datetime:
        return ensure_utc_datetime(value, field_name="pause.timestamp")

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(
        cls, value: dict[str, SafeMetadataValue]
    ) -> dict[str, SafeMetadataValue]:
        return normalize_safe_metadata(value, field_name="pause.metadata")


class BlockingReason(RuntimeContract):
    code: ReasonCode
    detail: str
    timestamp: datetime
    related_reference: OpaqueReference | None = None
    resume_state: AgentRunState
    metadata: dict[str, SafeMetadataValue] = Field(default_factory=dict)

    @field_validator("detail")
    @classmethod
    def _validate_detail(cls, value: str) -> str:
        return validate_safe_text(value, field_name="blocking.detail")

    @field_validator("timestamp")
    @classmethod
    def _validate_timestamp(cls, value: datetime) -> datetime:
        return ensure_utc_datetime(value, field_name="blocking.timestamp")

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(
        cls, value: dict[str, SafeMetadataValue]
    ) -> dict[str, SafeMetadataValue]:
        return normalize_safe_metadata(value, field_name="blocking.metadata")


class CancellationRecord(RuntimeContract):
    reason_code: ReasonCode
    detail: str
    requester_reference: OpaqueReference
    timestamp: datetime
    metadata: dict[str, SafeMetadataValue] = Field(default_factory=dict)

    @field_validator("detail")
    @classmethod
    def _validate_detail(cls, value: str) -> str:
        return validate_safe_text(value, field_name="cancellation.detail")

    @field_validator("timestamp")
    @classmethod
    def _validate_timestamp(cls, value: datetime) -> datetime:
        return ensure_utc_datetime(value, field_name="cancellation.timestamp")

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(
        cls, value: dict[str, SafeMetadataValue]
    ) -> dict[str, SafeMetadataValue]:
        return normalize_safe_metadata(value, field_name="cancellation.metadata")


class AutonomousExecutionType(StrEnum):
    PLANNING_REVIEW = "planning_review"


class AutonomousExecutionSpecification(RuntimeContract):
    """The fixed, explicitly queued execution request supported by Phase 2C."""

    execution_type: AutonomousExecutionType
    context_assembly_id: OpaqueReference
    output_schema_version: Literal["1.0"] = "1.0"
    provider_preference: OpaqueReference | None = None
    model_name: str | None = Field(default=None, min_length=1, max_length=200)
    maximum_provider_requests: int = Field(default=2, ge=1, le=2)
    maximum_repair_calls: int = Field(default=1, ge=0, le=1)
    maximum_output_tokens: int = Field(default=2048, ge=128, le=16_384)
    maximum_execution_seconds: int = Field(default=300, ge=1, le=3600)

    @field_validator("model_name")
    @classmethod
    def _validate_model_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_safe_text(value, field_name="model_name", max_length=200)

    @model_validator(mode="after")
    def _validate_request_budget(self) -> AutonomousExecutionSpecification:
        required_requests = 1 + self.maximum_repair_calls
        if self.maximum_provider_requests < required_requests:
            raise ValueError("maximum_provider_requests must cover the initial and repair calls")
        return self


class AgentRunSpecification(RuntimeContract):
    run_id: RunId
    task_id: TaskId
    agent_id: AgentId
    requested_operation: str
    created_at: datetime
    deadline: datetime | None = None
    parent_run_id: ParentRunId | None = None
    correlation_id: CorrelationId | None = None
    causation_id: CausationId | None = None
    idempotency_key: IdempotencyKey
    maximum_permitted_attempts: int = Field(ge=1, le=100)
    metadata: dict[str, SafeMetadataValue] = Field(default_factory=dict)
    requested_capabilities: tuple[CapabilityKey, ...] = ()
    execution_constraints: ExecutionConstraints | None = None
    autonomous_execution: AutonomousExecutionSpecification | None = None

    @field_validator("requested_operation")
    @classmethod
    def _validate_operation(cls, value: str) -> str:
        return validate_safe_text(value, field_name="requested_operation", max_length=4_000)

    @field_validator("created_at", "deadline")
    @classmethod
    def _validate_datetimes(cls, value: datetime | None, info: Any) -> datetime | None:
        if value is None:
            return None
        return ensure_utc_datetime(value, field_name=str(info.field_name))

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(
        cls, value: dict[str, SafeMetadataValue]
    ) -> dict[str, SafeMetadataValue]:
        return normalize_safe_metadata(value, field_name="run_specification.metadata")

    @field_validator("requested_capabilities")
    @classmethod
    def _validate_capabilities(
        cls,
        value: tuple[CapabilityKey, ...] | list[CapabilityKey],
    ) -> tuple[CapabilityKey, ...]:
        items = tuple(sorted(set(value)))
        if len(items) > 64:
            raise ValueError("requested_capabilities exceeds the 64-item limit")
        return items

    @model_validator(mode="after")
    def _validate_lineage(self) -> AgentRunSpecification:
        if self.parent_run_id == self.run_id:
            raise ValueError("parent_run_id must not reference the same run")
        if self.deadline is not None and self.deadline <= self.created_at:
            raise ValueError("deadline must be later than created_at")
        validate_run_created_payload_size(self.model_dump(mode="json"))
        return self


class AgentRunAttempt(RuntimeContract):
    attempt_id: AttemptId
    run_id: RunId
    attempt_number: int = Field(ge=1, le=100)
    state: AttemptState
    started_at: datetime
    finished_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    executor_reference: OpaqueReference | None = None
    resumed_from_checkpoint_id: CheckpointId | None = None
    outcome: TerminalOutcome | None = None
    failure_category: FailureClassification | None = None
    failure_detail: str | None = None
    cancellation_acknowledged_at: datetime | None = None
    version: int = Field(default=0, ge=0)

    @field_validator(
        "started_at", "finished_at", "last_heartbeat_at", "cancellation_acknowledged_at"
    )
    @classmethod
    def _validate_datetimes(cls, value: datetime | None, info: Any) -> datetime | None:
        if value is None:
            return None
        return ensure_utc_datetime(value, field_name=str(info.field_name))

    @field_validator("failure_detail")
    @classmethod
    def _validate_failure_detail(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_safe_text(value, field_name="attempt.failure_detail")

    @model_validator(mode="after")
    def _validate_state(self) -> AgentRunAttempt:
        if self.finished_at is not None and self.finished_at < self.started_at:
            raise ValueError("finished_at must not be earlier than started_at")
        if self.last_heartbeat_at is not None and self.last_heartbeat_at < self.started_at:
            raise ValueError("last_heartbeat_at must not be earlier than started_at")
        terminal_states = {
            AttemptState.CANCELLED,
            AttemptState.SUCCEEDED,
            AttemptState.FAILED,
            AttemptState.TIMED_OUT,
            AttemptState.ABANDONED,
        }
        if self.state in terminal_states and self.finished_at is None:
            raise ValueError("terminal attempts must define finished_at")
        if self.state not in terminal_states and self.finished_at is not None:
            raise ValueError("non-terminal attempts must not define finished_at")
        if self.state == AttemptState.SUCCEEDED and self.outcome != TerminalOutcome.SUCCESS:
            raise ValueError("succeeded attempts must have success outcome")
        if self.state == AttemptState.CANCELLED and self.outcome != TerminalOutcome.CANCELLED:
            raise ValueError("cancelled attempts must have cancelled outcome")
        if self.state == AttemptState.FAILED and self.outcome != TerminalOutcome.FAILURE:
            raise ValueError("failed attempts must have failure outcome")
        if self.state == AttemptState.TIMED_OUT and self.outcome != TerminalOutcome.TIMEOUT:
            raise ValueError("timed_out attempts must have timeout outcome")
        if self.state == AttemptState.ABANDONED and self.outcome != TerminalOutcome.ABANDONED:
            raise ValueError("abandoned attempts must have abandoned outcome")
        if (
            self.state in {AttemptState.FAILED, AttemptState.TIMED_OUT}
            and self.failure_category is None
        ):
            raise ValueError("failed or timed_out attempts require failure_category")
        if self.failure_category is not None and self.failure_detail is None:
            raise ValueError("failure_detail is required when failure_category is set")
        return self


class AgentRunCheckpoint(RuntimeContract):
    checkpoint_id: CheckpointId
    run_id: RunId
    attempt_id: AttemptId
    checkpoint_sequence: int = Field(ge=1, le=100_000)
    run_version: int = Field(ge=1)
    event_sequence: int = Field(ge=1)
    schema_version: SchemaVersion = SUPPORTED_EVENT_SCHEMA_VERSION
    timestamp: datetime
    state_reference: OpaqueReference
    integrity_digest: DigestValue
    resume_cursor: str | None = None
    metadata: dict[str, SafeMetadataValue] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def _validate_timestamp(cls, value: datetime) -> datetime:
        return ensure_utc_datetime(value, field_name="checkpoint.timestamp")

    @field_validator("resume_cursor")
    @classmethod
    def _validate_cursor(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_safe_text(value, field_name="checkpoint.resume_cursor", max_length=500)

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(
        cls, value: dict[str, SafeMetadataValue]
    ) -> dict[str, SafeMetadataValue]:
        return normalize_safe_metadata(value, field_name="checkpoint.metadata")

    @model_validator(mode="after")
    def _validate_digest(self) -> AgentRunCheckpoint:
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,32}:[0-9a-fA-F]{16,128}", self.integrity_digest):
            raise ValueError("integrity_digest must use algorithm:hex format")
        return self


class AgentRunSnapshot(RuntimeContract):
    specification: AgentRunSpecification
    state: AgentRunState
    version: int = Field(ge=0)
    event_sequence_number: int = Field(ge=0)
    attempt_count: int = Field(ge=0, le=100)
    active_attempt_id: AttemptId | None = None
    latest_checkpoint_id: CheckpointId | None = None
    created_at: datetime
    queued_at: datetime | None = None
    claimed_at: datetime | None = None
    started_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    paused_at: datetime | None = None
    resumed_at: datetime | None = None
    cancellation_requested_at: datetime | None = None
    completed_at: datetime | None = None
    terminal_outcome: TerminalOutcome | None = None
    failure: FailureRecord | None = None
    status_detail: str | None = None
    blocking_reason: BlockingReason | None = None
    pause_reason: PauseReason | None = None
    cancellation: CancellationRecord | None = None
    recovery_status: RecoveryStatus = RecoveryStatus.NONE

    @field_validator(
        "created_at",
        "queued_at",
        "claimed_at",
        "started_at",
        "last_heartbeat_at",
        "paused_at",
        "resumed_at",
        "cancellation_requested_at",
        "completed_at",
    )
    @classmethod
    def _validate_datetimes(cls, value: datetime | None, info: Any) -> datetime | None:
        if value is None:
            return None
        return ensure_utc_datetime(value, field_name=str(info.field_name))

    @field_validator("status_detail")
    @classmethod
    def _validate_status_detail(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_safe_text(value, field_name="snapshot.status_detail")

    @model_validator(mode="after")
    def _validate_consistency(self) -> AgentRunSnapshot:
        if self.version != self.event_sequence_number:
            raise ValueError("snapshot version must equal event_sequence_number")

        def ensure_not_before_created(
            value: datetime | None,
            *,
            field_name: str,
        ) -> None:
            if value is not None and value < self.created_at:
                raise ValueError(f"{field_name} must not be earlier than created_at")

        def ensure_order(
            earlier: datetime | None,
            later: datetime | None,
            *,
            earlier_name: str,
            later_name: str,
        ) -> None:
            if earlier is not None and later is not None and later < earlier:
                raise ValueError(f"{later_name} must not be earlier than {earlier_name}")

        for field_name, value in (
            ("queued_at", self.queued_at),
            ("claimed_at", self.claimed_at),
            ("started_at", self.started_at),
            ("last_heartbeat_at", self.last_heartbeat_at),
            ("paused_at", self.paused_at),
            ("resumed_at", self.resumed_at),
            ("cancellation_requested_at", self.cancellation_requested_at),
            ("completed_at", self.completed_at),
        ):
            ensure_not_before_created(value, field_name=field_name)

        ensure_order(
            self.queued_at,
            self.claimed_at,
            earlier_name="queued_at",
            later_name="claimed_at",
        )
        ensure_order(
            self.claimed_at,
            self.started_at,
            earlier_name="claimed_at",
            later_name="started_at",
        )
        ensure_order(
            self.started_at,
            self.last_heartbeat_at,
            earlier_name="started_at",
            later_name="last_heartbeat_at",
        )
        ensure_order(
            self.started_at,
            self.completed_at,
            earlier_name="started_at",
            later_name="completed_at",
        )
        ensure_order(
            self.cancellation_requested_at,
            self.completed_at,
            earlier_name="cancellation_requested_at",
            later_name="completed_at",
        )

        terminal_states = {
            AgentRunState.CANCELLED,
            AgentRunState.SUCCEEDED,
            AgentRunState.FAILED,
            AgentRunState.TIMED_OUT,
            AgentRunState.ABANDONED,
        }
        if self.state in terminal_states:
            if self.completed_at is None:
                raise ValueError("terminal runs must define completed_at")
            if self.terminal_outcome is None:
                raise ValueError("terminal runs must define terminal_outcome")
            if self.active_attempt_id is not None:
                raise ValueError("terminal runs must not keep an active attempt")
        else:
            if self.completed_at is not None:
                raise ValueError("non-terminal runs must not define completed_at")
            if self.terminal_outcome is not None:
                raise ValueError("non-terminal runs must not define terminal_outcome")

        if self.state == AgentRunState.PAUSED and self.pause_reason is None:
            raise ValueError("paused runs require pause_reason")
        if (
            self.state != AgentRunState.PAUSED
            and self.pause_reason is not None
            and self.state != AgentRunState.PAUSE_REQUESTED
        ):
            raise ValueError("pause_reason is only retained for pause_requested or paused runs")
        if self.pause_reason is not None:
            ensure_not_before_created(
                self.pause_reason.timestamp,
                field_name="pause_reason.timestamp",
            )
            if self.pause_reason.resume_state == AgentRunState.STARTING:
                raise ValueError("pause_reason.resume_state must not be starting")
            if self.state == AgentRunState.PAUSE_REQUESTED:
                ensure_order(
                    self.resumed_at,
                    self.pause_reason.timestamp,
                    earlier_name="resumed_at",
                    later_name="pause_reason.timestamp",
                )
            if self.state == AgentRunState.PAUSED:
                ensure_order(
                    self.pause_reason.timestamp,
                    self.paused_at,
                    earlier_name="pause_reason.timestamp",
                    later_name="paused_at",
                )
                ensure_order(
                    self.resumed_at,
                    self.paused_at,
                    earlier_name="resumed_at",
                    later_name="paused_at",
                )

        if self.state == AgentRunState.BLOCKED and self.blocking_reason is None:
            raise ValueError("blocked runs require blocking_reason")
        if self.state != AgentRunState.BLOCKED and self.blocking_reason is not None:
            raise ValueError("blocking_reason is only retained while blocked")
        if self.blocking_reason is not None:
            ensure_not_before_created(
                self.blocking_reason.timestamp,
                field_name="blocking_reason.timestamp",
            )
            if self.blocking_reason.resume_state == AgentRunState.STARTING:
                raise ValueError("blocking_reason.resume_state must not be starting")

        if self.state in {
            AgentRunState.CANCEL_REQUESTED,
            AgentRunState.CANCELLING,
            AgentRunState.CANCELLED,
        }:
            if self.cancellation is None or self.cancellation_requested_at is None:
                raise ValueError("cancellation states require cancellation metadata")
        if self.cancellation is not None:
            ensure_not_before_created(
                self.cancellation.timestamp,
                field_name="cancellation.timestamp",
            )
            ensure_order(
                self.cancellation.timestamp,
                self.cancellation_requested_at,
                earlier_name="cancellation.timestamp",
                later_name="cancellation_requested_at",
            )
        return self


class RuntimeEventEnvelope(RuntimeContract):
    event_id: EventId
    event_type: AgentRuntimeEventType
    event_schema_version: SchemaVersion = SUPPORTED_EVENT_SCHEMA_VERSION
    run_id: RunId
    attempt_id: AttemptId | None = None
    sequence_number: int = Field(ge=1)
    run_version: int = Field(ge=1)
    timestamp: datetime
    actor_reference: OpaqueReference | None = None
    command_id: CommandId | None = None
    correlation_id: CorrelationId | None = None
    causation_id: CausationId | None = None
    payload: dict[str, SafeMetadataValue] = Field(default_factory=dict)
    metadata: dict[str, SafeMetadataValue] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def _validate_timestamp(cls, value: datetime) -> datetime:
        return ensure_utc_datetime(value, field_name="event.timestamp")

    @field_validator("payload")
    @classmethod
    def _validate_payload(cls, value: dict[str, SafeMetadataValue]) -> dict[str, SafeMetadataValue]:
        return normalize_safe_metadata(value, field_name="event.payload")

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(
        cls, value: dict[str, SafeMetadataValue]
    ) -> dict[str, SafeMetadataValue]:
        return normalize_safe_metadata(value, field_name="event.metadata")

    @model_validator(mode="after")
    def _validate_schema_version(self) -> RuntimeEventEnvelope:
        if self.event_schema_version != SUPPORTED_EVENT_SCHEMA_VERSION:
            raise ValueError("unsupported event schema version")
        return self


class RuntimeCommand(RuntimeContract):
    run_id: RunId
    command_id: CommandId
    expected_run_version: int = Field(ge=0)
    timestamp: datetime
    actor_reference: OpaqueReference | None = None
    source_metadata: dict[str, SafeMetadataValue] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def _validate_timestamp(cls, value: datetime) -> datetime:
        return ensure_utc_datetime(value, field_name="command.timestamp")

    @field_validator("source_metadata")
    @classmethod
    def _validate_metadata(
        cls, value: dict[str, SafeMetadataValue]
    ) -> dict[str, SafeMetadataValue]:
        return normalize_safe_metadata(value, field_name="command.source_metadata")


class CreateAgentRunCommand(RuntimeContract):
    command_type: Literal["create"] = "create"
    specification: AgentRunSpecification
    command_id: CommandId
    expected_run_version: int = Field(default=0, ge=0)
    timestamp: datetime
    actor_reference: OpaqueReference | None = None
    source_metadata: dict[str, SafeMetadataValue] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def _validate_timestamp(cls, value: datetime) -> datetime:
        return ensure_utc_datetime(value, field_name="command.timestamp")

    @field_validator("source_metadata")
    @classmethod
    def _validate_metadata(
        cls, value: dict[str, SafeMetadataValue]
    ) -> dict[str, SafeMetadataValue]:
        return normalize_safe_metadata(value, field_name="command.source_metadata")


class QueueAgentRunCommand(RuntimeCommand):
    command_type: Literal["queue"] = "queue"
    detail: str = "Queued for execution"

    @field_validator("detail")
    @classmethod
    def _validate_detail(cls, value: str) -> str:
        return validate_safe_text(value, field_name="queue.detail")


class ClaimAgentRunCommand(RuntimeCommand):
    command_type: Literal["claim"] = "claim"
    executor_reference: OpaqueReference
    detail: str = "Claimed for execution"

    @field_validator("detail")
    @classmethod
    def _validate_detail(cls, value: str) -> str:
        return validate_safe_text(value, field_name="claim.detail")


class BeginAttemptCommand(RuntimeCommand):
    command_type: Literal["begin_attempt"] = "begin_attempt"
    attempt_id: AttemptId | None = None
    executor_reference: OpaqueReference
    resume_from_checkpoint_id: CheckpointId | None = None
    detail: str = "Execution start requested"

    @field_validator("detail")
    @classmethod
    def _validate_detail(cls, value: str) -> str:
        return validate_safe_text(value, field_name="begin_attempt.detail")


class StartAttemptCommand(RuntimeCommand):
    command_type: Literal["start_attempt"] = "start_attempt"
    attempt_id: AttemptId | None = None
    detail: str = "Attempt started"

    @field_validator("detail")
    @classmethod
    def _validate_detail(cls, value: str) -> str:
        return validate_safe_text(value, field_name="start_attempt.detail")


class HeartbeatCommand(RuntimeCommand):
    command_type: Literal["heartbeat"] = "heartbeat"
    attempt_id: AttemptId | None = None
    detail: str = "Heartbeat recorded"

    @field_validator("detail")
    @classmethod
    def _validate_detail(cls, value: str) -> str:
        return validate_safe_text(value, field_name="heartbeat.detail")


class RequestPauseCommand(RuntimeCommand):
    command_type: Literal["request_pause"] = "request_pause"
    reason_code: ReasonCode
    detail: str

    @field_validator("detail")
    @classmethod
    def _validate_detail(cls, value: str) -> str:
        return validate_safe_text(value, field_name="request_pause.detail")


class ConfirmPauseCommand(RuntimeCommand):
    command_type: Literal["confirm_pause"] = "confirm_pause"
    detail: str = "Run paused"

    @field_validator("detail")
    @classmethod
    def _validate_detail(cls, value: str) -> str:
        return validate_safe_text(value, field_name="confirm_pause.detail")


class ResumeAgentRunCommand(RuntimeCommand):
    command_type: Literal["resume"] = "resume"
    detail: str = "Run resumed"

    @field_validator("detail")
    @classmethod
    def _validate_detail(cls, value: str) -> str:
        return validate_safe_text(value, field_name="resume.detail")


class BlockAgentRunCommand(RuntimeCommand):
    command_type: Literal["block"] = "block"
    block_code: ReasonCode
    detail: str
    related_reference: OpaqueReference | None = None

    @field_validator("detail")
    @classmethod
    def _validate_detail(cls, value: str) -> str:
        return validate_safe_text(value, field_name="block.detail")


class UnblockAgentRunCommand(RuntimeCommand):
    command_type: Literal["unblock"] = "unblock"
    detail: str = "Block cleared"

    @field_validator("detail")
    @classmethod
    def _validate_detail(cls, value: str) -> str:
        return validate_safe_text(value, field_name="unblock.detail")


class RequestCancellationCommand(RuntimeCommand):
    command_type: Literal["request_cancellation"] = "request_cancellation"
    reason_code: ReasonCode
    detail: str
    requester_reference: OpaqueReference

    @field_validator("detail")
    @classmethod
    def _validate_detail(cls, value: str) -> str:
        return validate_safe_text(value, field_name="cancel.detail")


class ConfirmCancellationStartCommand(RuntimeCommand):
    command_type: Literal["start_cancellation"] = "start_cancellation"
    detail: str = "Cancellation in progress"

    @field_validator("detail")
    @classmethod
    def _validate_detail(cls, value: str) -> str:
        return validate_safe_text(value, field_name="confirm_cancellation_start.detail")


class ConfirmCancellationCommand(RuntimeCommand):
    command_type: Literal["confirm_cancellation"] = "confirm_cancellation"
    detail: str = "Run cancelled"

    @field_validator("detail")
    @classmethod
    def _validate_detail(cls, value: str) -> str:
        return validate_safe_text(value, field_name="confirm_cancellation.detail")


class RecordCheckpointCommand(RuntimeCommand):
    command_type: Literal["record_checkpoint"] = "record_checkpoint"
    checkpoint_id: CheckpointId | None = None
    attempt_id: AttemptId | None = None
    state_reference: OpaqueReference
    integrity_digest: DigestValue
    resume_cursor: str | None = None
    checkpoint_metadata: dict[str, SafeMetadataValue] = Field(default_factory=dict)

    @field_validator("resume_cursor")
    @classmethod
    def _validate_cursor(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_safe_text(value, field_name="checkpoint.resume_cursor", max_length=500)

    @field_validator("checkpoint_metadata")
    @classmethod
    def _validate_metadata(
        cls, value: dict[str, SafeMetadataValue]
    ) -> dict[str, SafeMetadataValue]:
        return normalize_safe_metadata(value, field_name="checkpoint.command_metadata")


class CompleteAttemptCommand(RuntimeCommand):
    command_type: Literal["complete_attempt"] = "complete_attempt"
    attempt_id: AttemptId | None = None
    detail: str = "Attempt succeeded"

    @field_validator("detail")
    @classmethod
    def _validate_detail(cls, value: str) -> str:
        return validate_safe_text(value, field_name="complete_attempt.detail")


class FailAttemptCommand(RuntimeCommand):
    command_type: Literal["fail_attempt"] = "fail_attempt"
    attempt_id: AttemptId | None = None
    failure_category: FailureClassification
    failure_detail: str

    @field_validator("failure_detail")
    @classmethod
    def _validate_detail(cls, value: str) -> str:
        return validate_safe_text(value, field_name="fail_attempt.failure_detail")


class TimeoutAttemptCommand(RuntimeCommand):
    command_type: Literal["timeout_attempt"] = "timeout_attempt"
    attempt_id: AttemptId | None = None
    detail: str = "Attempt timed out"

    @field_validator("detail")
    @classmethod
    def _validate_detail(cls, value: str) -> str:
        return validate_safe_text(value, field_name="timeout_attempt.detail")


class AbandonAttemptCommand(RuntimeCommand):
    command_type: Literal["abandon_attempt"] = "abandon_attempt"
    attempt_id: AttemptId | None = None
    detail: str = "Attempt abandoned"

    @field_validator("detail")
    @classmethod
    def _validate_detail(cls, value: str) -> str:
        return validate_safe_text(value, field_name="abandon_attempt.detail")


class CompleteAgentRunCommand(RuntimeCommand):
    command_type: Literal["complete_run"] = "complete_run"
    detail: str = "Run succeeded"

    @field_validator("detail")
    @classmethod
    def _validate_detail(cls, value: str) -> str:
        return validate_safe_text(value, field_name="complete_run.detail")


class FailAgentRunCommand(RuntimeCommand):
    command_type: Literal["fail_run"] = "fail_run"
    failure_category: FailureClassification
    failure_detail: str

    @field_validator("failure_detail")
    @classmethod
    def _validate_detail(cls, value: str) -> str:
        return validate_safe_text(value, field_name="fail_run.failure_detail")


class TimeoutAgentRunCommand(RuntimeCommand):
    command_type: Literal["timeout_run"] = "timeout_run"
    detail: str = "Run timed out"

    @field_validator("detail")
    @classmethod
    def _validate_detail(cls, value: str) -> str:
        return validate_safe_text(value, field_name="timeout_run.detail")


class AbandonAgentRunCommand(RuntimeCommand):
    command_type: Literal["abandon_run"] = "abandon_run"
    detail: str = "Run abandoned"

    @field_validator("detail")
    @classmethod
    def _validate_detail(cls, value: str) -> str:
        return validate_safe_text(value, field_name="abandon_run.detail")


class RequestRecoveryPlanCommand(RuntimeCommand):
    command_type: Literal["request_recovery_plan"] = "request_recovery_plan"
    detail: str = "Recovery plan requested"

    @field_validator("detail")
    @classmethod
    def _validate_detail(cls, value: str) -> str:
        return validate_safe_text(value, field_name="recovery.detail")


class RecoveryPlan(RuntimeContract):
    run_id: RunId
    recovery_allowed: bool
    selected_checkpoint: AgentRunCheckpoint | None = None
    next_attempt_number: int | None = Field(default=None, ge=1)
    expected_starting_state: AgentRunState | None = None
    required_prior_terminal_attempt_id: AttemptId | None = None
    reason: str
    warnings: tuple[str, ...] = ()
    expected_version: int = Field(ge=0)
    expected_event_sequence: int = Field(ge=0)

    @field_validator("reason")
    @classmethod
    def _validate_reason(cls, value: str) -> str:
        return validate_safe_text(value, field_name="recovery.reason")

    @field_validator("warnings")
    @classmethod
    def _validate_warnings(cls, value: tuple[str, ...] | list[str]) -> tuple[str, ...]:
        warnings = tuple(validate_safe_text(item, field_name="recovery.warning") for item in value)
        return warnings


class RuntimeCommandResult(RuntimeContract):
    run_id: RunId
    snapshot: AgentRunSnapshot | None = None
    events: tuple[RuntimeEventEnvelope, ...] = ()
    recovery_plan: RecoveryPlan | None = None
    idempotent_replay: bool = False


class ProcessedCommandRecord(RuntimeContract):
    run_id: RunId
    command_id: CommandId
    command_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    result: RuntimeCommandResult
    recorded_at: datetime
    verified_actor_id: OpaqueReference | None = None
    command_type: str = Field(default="runtime", min_length=1, max_length=120)
    authorization: dict[str, SafeMetadataValue] = Field(default_factory=dict)

    @field_validator("recorded_at")
    @classmethod
    def _validate_timestamp(cls, value: datetime) -> datetime:
        return ensure_utc_datetime(value, field_name="processed_command.recorded_at")

    @field_validator("authorization")
    @classmethod
    def _validate_authorization(
        cls, value: dict[str, SafeMetadataValue]
    ) -> dict[str, SafeMetadataValue]:
        return normalize_safe_metadata(value, field_name="processed_command.authorization")


class AgentRunQuery(RuntimeContract):
    run_id: RunId | None = None
    task_id: TaskId | None = None
    agent_id: AgentId | None = None
    state: AgentRunState | None = None
    terminal: bool | None = None
    correlation_id: CorrelationId | None = None
    parent_run_id: ParentRunId | None = None
    created_from: datetime | None = None
    created_to: datetime | None = None
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)

    @field_validator("created_from", "created_to")
    @classmethod
    def _validate_datetimes(cls, value: datetime | None, info: Any) -> datetime | None:
        if value is None:
            return None
        return ensure_utc_datetime(value, field_name=str(info.field_name))

    @model_validator(mode="after")
    def _validate_range(self) -> AgentRunQuery:
        if (
            self.created_from is not None
            and self.created_to is not None
            and self.created_to < self.created_from
        ):
            raise ValueError("created_to must not be earlier than created_from")
        return self


class AgentRunQueryResult(RuntimeContract):
    items: tuple[AgentRunSnapshot, ...]
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=MAX_PAGE_SIZE)
    next_offset: int | None = None
    total_count: int = Field(ge=0)


class LineageEntry(RuntimeContract):
    run_id: RunId
    exists: bool
    state: AgentRunState | None = None


class LineageResolution(RuntimeContract):
    run_id: RunId
    entries: tuple[LineageEntry, ...]
    missing_parent_id: RunId | None = None
    truncated: bool = False
    depth_limit: int = Field(
        default=DEFAULT_LINEAGE_DEPTH_LIMIT, ge=1, le=DEFAULT_LINEAGE_DEPTH_LIMIT
    )
