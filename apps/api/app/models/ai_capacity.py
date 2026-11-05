"""Operator-owned eligibility evidence, never accepted from model output."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_QUOTA_WINDOW_SCHEMA = {
    "anyOf": [
        {
            "required": ["window_start", "resets_at", "remaining_requests"],
            "properties": {
                "window_start": {"type": "string", "format": "date-time"},
                "resets_at": {"type": "string", "format": "date-time"},
                "remaining_requests": {"type": "integer", "minimum": 0, "maximum": 1_000_000},
            },
        },
        {
            "properties": {
                "integration": {"const": "groq"},
                "window_start": {"type": "null"},
                "resets_at": {"type": "null"},
                "remaining_requests": {"type": "null"},
            },
        },
    ]
}


class FreeAllowance(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        str_strip_whitespace=True,
        json_schema_extra=_QUOTA_WINDOW_SCHEMA,
    )

    provider: str = Field(min_length=1, max_length=120, pattern=r"^[a-zA-Z0-9_-]+$")
    integration: Literal["gemini", "groq", "openrouter", "cloudflare", "chatgpt", "codex"]
    account_id: str = Field(min_length=1, max_length=160)
    session_identity: str | None = Field(default=None, min_length=1, max_length=160)
    shared_bucket: str = Field(min_length=1, max_length=160)
    models: frozenset[str] = Field(min_length=1, max_length=100)
    capabilities: frozenset[
        Literal[
            "chat",
            "text_generation",
            "code_generation",
            "code_editing",
            "reasoning",
            "structured_output",
        ]
    ] = Field(default=frozenset({"chat", "text_generation"}), min_length=1, max_length=6)
    credential_env: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,119}$")
    credential_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    # This must reference account-specific evidence, not a public pricing page.
    evidence_reference: str = Field(min_length=1, max_length=500)
    verified_at: datetime
    valid_until: datetime
    # Groq obtains quota authority exclusively from the native acceptance/header ledger.
    # Unknown legacy quota observations must remain absent, never fabricated.
    window_start: datetime | None = None
    resets_at: datetime | None = None
    remaining_requests: int | None = Field(default=None, ge=0, le=1_000_000, strict=True)
    nonbillable: Literal[True]
    automatic_recharge: Literal[False]
    credit_consumption: Literal[False]
    billing_enabled: Literal[False]

    @field_validator("verified_at", "valid_until", "window_start", "resets_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("allowance evidence requires timezone-aware timestamps")
        return value

    @field_validator("models")
    @classmethod
    def exact_models(cls, value: frozenset[str]) -> frozenset[str]:
        if any(not item.strip() or len(item) > 200 or "*" in item for item in value):
            raise ValueError("allowances require exact model names")
        return value

    @model_validator(mode="after")
    def valid_evidence(self) -> "FreeAllowance":
        if (
            self.integration in {"gemini", "groq", "openrouter", "cloudflare"}
            and self.credential_fingerprint is None
        ):
            raise ValueError("hosted API evidence must bind the verified credential fingerprint")
        if not self.verified_at < self.valid_until:
            raise ValueError("invalid evidence validity interval")
        quota = (self.window_start, self.resets_at, self.remaining_requests)
        if all(value is None for value in quota):
            if self.integration != "groq":
                raise ValueError("complete verified quota observations are required")
        elif any(value is None for value in quota):
            raise ValueError("quota observations must be complete or absent for native Groq")
        else:
            if not self.window_start < self.resets_at:
                raise ValueError("invalid quota window")
            if self.verified_at < self.window_start:
                raise ValueError(
                    "quota evidence must be verified within its actual allowance window"
                )
        if self.integration == "openrouter" and any(not m.endswith(":free") for m in self.models):
            raise ValueError("OpenRouter requires exact :free models")
        if self.integration in {"chatgpt", "codex"} and self.credential_env == "OPENAI_API_KEY":
            raise ValueError("subscription inference cannot use an OpenAI API key")
        return self
