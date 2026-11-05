"""Groq operator ceilings, observed headers and conservative local reservations."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictRateModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    @field_validator("*", mode="after")
    @classmethod
    def timezone_required(cls, value):
        if isinstance(value, datetime) and value.tzinfo is None:
            raise ValueError("rate timestamps require a timezone")
        return value


class GroqLimits(StrictRateModel):
    rpm: int = Field(ge=1, le=20000, strict=True)
    rpd: int = Field(ge=1, le=20000, strict=True)
    tpm: int = Field(ge=1, le=10000000, strict=True)
    tpd: int = Field(ge=1, le=1000000000, strict=True)


class GroqHeaderSnapshot(StrictRateModel):
    received_at: datetime
    rpd_limit: int = Field(ge=1, le=1000000000, strict=True)
    rpd_remaining: int = Field(ge=0, le=1000000000, strict=True)
    rpd_resets_at: datetime
    tpm_limit: int = Field(ge=1, le=1000000000, strict=True)
    tpm_remaining: int = Field(ge=0, le=1000000000, strict=True)
    tpm_resets_at: datetime

    @model_validator(mode="after")
    def valid_snapshot(self):
        if self.rpd_remaining > self.rpd_limit or self.tpm_remaining > self.tpm_limit:
            raise ValueError("remaining capacity exceeds provider limit")
        if min(self.rpd_resets_at, self.tpm_resets_at) < self.received_at:
            raise ValueError("invalid reset boundary")
        return self


class GroqReservation(StrictRateModel):
    created_at: datetime
    reserved_tokens: int = Field(ge=1, le=10000000, strict=True)
    accounted_tokens: int = Field(ge=0, le=10000000, strict=True)
    actual_tokens: int | None = Field(default=None, ge=0, le=10000000, strict=True)
    status: Literal["reserved", "completed", "uncertain", "rejected"] = "reserved"


class GroqRateState(StrictRateModel):
    limits: GroqLimits
    reservations: dict[str, GroqReservation] = Field(default_factory=dict, max_length=25000)
    last_seen: datetime
    last_response_at: datetime | None = None
    last_headers: GroqHeaderSnapshot | None = None
    remote_rpd_limit: int | None = None
    remote_tpm_limit: int | None = None
    remote_rpd_remaining: int | None = None
    remote_tpm_remaining: int | None = None
    remote_rpd_reset: datetime | None = None
    remote_tpm_reset: datetime | None = None
    blocked_until: datetime | None = None
    unknown_provider_capacity: bool = True
