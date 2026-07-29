from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.model_providers.security import redact_secrets

SECRET_KEY_TERMS = ("api_key", "authorization", "token", "password", "secret", "credential")
MAX_METADATA_KEYS = 32
MAX_METADATA_BYTES = 20_000
MAX_METADATA_DEPTH = 16


class ProviderContract(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ModelCapability(StrEnum):
    TEXT_GENERATION = "text_generation"
    CHAT = "chat"
    CODE_GENERATION = "code_generation"
    CODE_EDITING = "code_editing"
    REASONING = "reasoning"
    TOOL_CALLING = "tool_calling"
    STRUCTURED_OUTPUT = "structured_output"
    VISION = "vision"


BUILTIN_ADAPTER_CAPABILITIES = frozenset(
    {
        ModelCapability.TEXT_GENERATION,
        ModelCapability.CHAT,
        ModelCapability.CODE_GENERATION,
        ModelCapability.CODE_EDITING,
        ModelCapability.REASONING,
    }
)


class ProviderType(StrEnum):
    OLLAMA = "ollama"
    OPENAI_COMPATIBLE = "openai_compatible"


class UsageQuality(StrEnum):
    EXACT = "exact"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"


class HealthStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    CONFIGURATION_ONLY = "configuration_only"


class ModelMessage(ProviderContract):
    role: MessageRole
    content: str = Field(min_length=1, max_length=1_000_000)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_metadata(self) -> ModelMessage:
        validate_safe_metadata(self.metadata)
        return self


class ModelExecutionRequest(ProviderContract):
    messages: list[ModelMessage] = Field(default_factory=list, max_length=256)
    prompt: str | None = Field(default=None, min_length=1, max_length=1_000_000)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_output_tokens: int | None = Field(default=None, ge=1, le=1_000_000)
    timeout_seconds: float | None = Field(default=None, gt=0, le=3600)
    task_id: str | None = Field(default=None, min_length=1, max_length=120)
    correlation_id: str | None = Field(default=None, min_length=1, max_length=120)
    required_capability: ModelCapability | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    streaming: bool = False

    @field_validator("task_id", "correlation_id")
    @classmethod
    def reject_secret_bearing_identifiers(cls, value: str | None) -> str | None:
        return validate_safe_identifier(value)

    @model_validator(mode="after")
    def normalize_and_validate(self) -> ModelExecutionRequest:
        if bool(self.messages) == bool(self.prompt):
            raise ValueError("provide exactly one of messages or prompt")
        if self.streaming:
            raise ValueError("streaming is not supported")
        if self.prompt is not None:
            object.__setattr__(
                self,
                "messages",
                [ModelMessage(role=MessageRole.USER, content=self.prompt)],
            )
            object.__setattr__(self, "prompt", None)
        validate_safe_metadata(self.metadata)
        return self


class ModelExecutionResponse(ProviderContract):
    content: str = Field(min_length=1, max_length=10_000_000)
    provider: str = Field(min_length=1, max_length=120)
    model: str = Field(min_length=1, max_length=200)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    usage_quality: UsageQuality = UsageQuality.UNKNOWN
    latency_ms: float = Field(ge=0)
    finish_reason: str | None = Field(default=None, max_length=120)
    task_id: str | None = Field(default=None, max_length=120)
    correlation_id: str | None = Field(default=None, max_length=120)
    request_id: str | None = Field(default=None, max_length=300)
    estimated_cost_usd: float | None = Field(default=None, ge=0)
    provider_metadata: dict[str, Any] = Field(default_factory=dict)
    routing_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("task_id", "correlation_id")
    @classmethod
    def reject_secret_bearing_identifiers(cls, value: str | None) -> str | None:
        return validate_safe_identifier(value)

    @model_validator(mode="after")
    def derive_and_validate(self) -> ModelExecutionResponse:
        if self.input_tokens is not None and self.output_tokens is not None:
            object.__setattr__(
                self,
                "total_tokens",
                self.input_tokens + self.output_tokens,
            )
        validate_safe_metadata(self.provider_metadata)
        validate_safe_metadata(self.routing_metadata)
        return self


class ProviderHealth(ProviderContract):
    provider: str = Field(min_length=1, max_length=120)
    healthy: bool
    status: HealthStatus
    latency_ms: float = Field(ge=0)
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    model_available: bool | None = None
    detail: str | None = Field(default=None, max_length=500)
    error_category: str | None = Field(default=None, max_length=120)


class ProviderSummary(ProviderContract):
    name: str = Field(min_length=1, max_length=120)
    provider_type: ProviderType
    is_local: bool
    capabilities: list[ModelCapability]
    default_model: str


@runtime_checkable
class ModelProvider(Protocol):
    name: str
    provider_type: ProviderType
    is_local: bool
    capabilities: frozenset[ModelCapability]
    default_model: str

    async def health_check(self) -> ProviderHealth: ...

    async def model_available(self, model: str) -> bool | None: ...

    async def execute(self, request: ModelExecutionRequest) -> ModelExecutionResponse: ...

    def safe_summary(self) -> ProviderSummary: ...


def validate_safe_identifier(value: str | None) -> str | None:
    if value is not None and redact_secrets(value) != value:
        raise ValueError("identifier cannot contain secret-bearing text")
    return value


def validate_safe_metadata(metadata: dict[str, Any]) -> None:
    visited: set[int] = set()
    key_count = 0

    def validate(value: Any, depth: int = 0) -> None:
        nonlocal key_count
        if depth > MAX_METADATA_DEPTH:
            raise ValueError("metadata exceeds the safe nesting limit")
        if isinstance(value, Mapping):
            identity = id(value)
            if identity in visited:
                return
            visited.add(identity)
            for key, item in value.items():
                if not isinstance(key, str):
                    raise ValueError("metadata keys must be strings")
                key_count += 1
                if key_count > MAX_METADATA_KEYS:
                    raise ValueError("metadata exceeds the safe key-count limit")
                normalized = str(key).lower().replace("-", "_")
                if any(term in normalized for term in SECRET_KEY_TERMS):
                    raise ValueError(f"secret-bearing metadata key is not allowed: {key}")
                validate(item, depth + 1)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            identity = id(value)
            if identity in visited:
                return
            visited.add(identity)
            for item in value:
                validate(item, depth + 1)
        elif isinstance(value, str):
            if redact_secrets(value) != value:
                raise ValueError("secret-bearing metadata values are not allowed")
        elif isinstance(value, float) and not math.isfinite(value):
            raise ValueError("metadata numbers must be finite")
        elif value is not None and not isinstance(value, (bool, int, float)):
            raise ValueError("metadata values must use JSON-compatible scalar types")

    validate(metadata)
    try:
        size = len(json.dumps(metadata, separators=(",", ":"), allow_nan=False).encode())
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("metadata must be safely serializable") from exc
    if size > MAX_METADATA_BYTES:
        raise ValueError("metadata exceeds the safe serialized-size limit")
