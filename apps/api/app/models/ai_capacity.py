"""Operator-owned eligibility evidence, never accepted from model output."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class FreeAllowance(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, hide_input_in_errors=True, str_strip_whitespace=True
    )

    provider: str = Field(min_length=1, max_length=120, pattern=r"^[a-zA-Z0-9_-]+$")
    integration: Literal["gemini", "groq", "openrouter", "cloudflare", "chatgpt", "codex"]
    account_id: str = Field(min_length=1, max_length=160)
    session_identity: str | None = Field(default=None, min_length=1, max_length=160)
    shared_bucket: str = Field(min_length=1, max_length=160)
    models: frozenset[str] = Field(min_length=1, max_length=100)
    credential_env: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,119}$")
    credential_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    # This must reference account-specific evidence, not a public pricing page.
    evidence_reference: str = Field(min_length=1, max_length=500)
    verified_at: datetime
    valid_until: datetime
    window_start: datetime
    resets_at: datetime
    remaining_requests: int = Field(ge=0, le=1_000_000, strict=True)
    nonbillable: Literal[True]
    automatic_recharge: Literal[False]
    credit_consumption: Literal[False]
    billing_enabled: Literal[False]

    @field_validator("verified_at", "valid_until", "window_start", "resets_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
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
        if not self.verified_at < self.valid_until or not self.window_start < self.resets_at:
            raise ValueError("invalid evidence or quota window")
        if self.verified_at < self.window_start:
            raise ValueError("quota evidence must be verified within its actual allowance window")
        if self.integration == "openrouter" and any(not m.endswith(":free") for m in self.models):
            raise ValueError("OpenRouter requires exact :free models")
        if self.integration in {"chatgpt", "codex"} and self.credential_env == "OPENAI_API_KEY":
            raise ValueError("subscription inference cannot use an OpenAI API key")
        return self
