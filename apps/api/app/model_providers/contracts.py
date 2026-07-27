from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
        _validate_safe_metadata(self.metadata)
        return self


class ModelExecutionRequest(ProviderContract):
    messages: list[ModelMessage] = Field(default_factory=list, max_length=256)
    prompt: str | None = Field(default=None, min_length=1, max_length=1_000_000)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_output_tokens: int | None = Field(default=None, ge=1)
    timeout_seconds: float | None = Field(default=None, gt=0, le=3600)
    task_id: str | None = Field(default=None, min_length=1, max_length=120)
    correlation_id: str | None = Field(default=None, min_length=1, max_length=120)
    required_capability: ModelCapability | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    streaming: bool = False

    @model_validator(mode="after")
    def normalize_and_validate(self) -> ModelExecutionRequest:
        if bool(self.messages) == bool(self.prompt):
            raise ValueError("provide exactly one of messages or prompt")
        if self.prompt is not None:
            object.__setattr__(
                self,
                "messages",
                [ModelMessage(role=MessageRole.USER, content=self.prompt)],
            )
            object.__setattr__(self, "prompt", None)
        if self.streaming:
            raise ValueError("streaming is not supported")
        _validate_safe_metadata(self.metadata)
        return self


class ModelExecutionResponse(ProviderContract):
    content: str = Field(min_length=1)
    provider: str
    model: str
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    usage_quality: UsageQuality = UsageQuality.UNKNOWN
    latency_ms: float = Field(ge=0)
    finish_reason: str | None = None
    task_id: str | None = None
    correlation_id: str | None = None
    request_id: str | None = None
    provider_metadata: dict[str, Any] = Field(default_factory=dict)
    estimated_cost_usd: float | None = Field(default=None, ge=0)
    routing_metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def derive_total(self) -> ModelExecutionResponse:
        if (
            self.total_tokens is None
            and self.input_tokens is not None
            and self.output_tokens is not None
        ):
            self.total_tokens = self.input_tokens + self.output_tokens
        _validate_safe_metadata(self.provider_metadata)
        _validate_safe_metadata(self.routing_metadata)
        return self


class ProviderHealth(ProviderContract):
    provider: str
    healthy: bool
    status: HealthStatus
    latency_ms: float = Field(ge=0)
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    model_available: bool | None = None
    detail: str | None = None
    error_category: str | None = None


class ProviderSummary(ProviderContract):
    name: str
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


def _validate_safe_metadata(metadata: dict[str, Any]) -> None:
    if len(metadata) > 32 or len(str(metadata)) > 20_000:
        raise ValueError("metadata exceeds the safe size limit")
    forbidden = ("api_key", "authorization", "token", "password", "secret", "credential")
    visited: set[int] = set()

    def validate(value: Any, depth: int = 0) -> None:
        if depth > 32:
            raise ValueError("metadata exceeds the safe nesting limit")
        if isinstance(value, Mapping):
            identity = id(value)
            if identity in visited:
                return
            visited.add(identity)
            for key, item in value.items():
                normalized = str(key).lower().replace("-", "_")
                if any(term in normalized for term in forbidden):
                    raise ValueError(f"secret-bearing metadata key is not allowed: {key}")
                validate(item, depth + 1)
            return
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            identity = id(value)
            if identity in visited:
                return
            visited.add(identity)
            for item in value:
                validate(item, depth + 1)

    validate(metadata)
