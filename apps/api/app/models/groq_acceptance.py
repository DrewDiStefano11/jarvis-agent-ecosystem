"""Operator-owned one-request authority, distinct from normal cloud activation."""

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.model_providers.contracts import validate_safe_identifier
from app.models.groq_capacity import GroqHeaderSnapshot, GroqLimits, StrictRateModel


class GroqAccountEvidence(StrictRateModel):
    account_id: str = Field(min_length=1, max_length=160)
    model: str = Field(min_length=1, max_length=200)
    credential_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    plan: Literal["Free"]
    nonbillable: Literal[True]
    billing_enabled: Literal[False]
    credit_consumption: Literal[False]
    automatic_recharge: Literal[False]
    limits: GroqLimits
    verified_at: datetime
    valid_until: datetime
    evidence_references: list[str] = Field(min_length=1, max_length=8)
    evidence_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    account_key_association_confirmed: Literal[True]

    _safe = field_validator("account_id", "model")(validate_safe_identifier)

    @field_validator("evidence_references")
    @classmethod
    def safe_references(cls, values):
        for value in values:
            if not 1 <= len(value) <= 500:
                raise ValueError("invalid evidence reference")
            validate_safe_identifier(value)
        return values

    @model_validator(mode="after")
    def current_interval(self):
        if self.verified_at >= self.valid_until:
            raise ValueError("invalid verification interval")
        return self


class PrepareGroqAcceptance(StrictRateModel):
    provider_name: str = Field(pattern=r"^[A-Za-z0-9_-]{1,120}$")
    expected_revision: int = Field(ge=1, strict=True)
    account_evidence: GroqAccountEvidence = Field(repr=False)


class GroqAcceptancePlan(StrictRateModel):
    schema_version: Literal["1.0"] = "1.0"
    id: str
    provider_name: str
    profile_revision: int
    profile_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence: GroqAccountEvidence = Field(repr=False)
    created_at: datetime
    expires_at: datetime
    method: Literal["POST"] = "POST"
    endpoint: Literal["https://api.groq.com/openai/v1/chat/completions"] = (
        "https://api.groq.com/openai/v1/chat/completions"
    )
    maximum_requests: Literal[1] = 1
    paid_spending_ceiling_usd: Literal[0] = 0
    ongoing_activation: Literal[False] = False
    token_reservation: int = Field(ge=1, le=10000000, strict=True)
    request_body: dict
    policy_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class ApproveGroqAcceptance(StrictRateModel):
    expected_plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmation: Literal["approve_exactly_one_groq_request"]


class ExecuteGroqAcceptance(StrictRateModel):
    expected_plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class GroqAcceptanceView(StrictRateModel):
    id: str
    plan_hash: str
    status: Literal["prepared", "approved", "dispatch_started", "succeeded", "failed", "uncertain"]
    plan: GroqAcceptancePlan
    result_code: str | None = None
    initialized_capacity: bool = Field(
        default=False,
        description="Native RPD/TPM header tracking initialized. RPM/TPD track local application usage; this does not assert unused account-wide RPM/TPD.",
    )
    quota_observation: GroqHeaderSnapshot | None = None
    approved_actor_id: str | None = None
    approved_actor_version: int | None = None
    approval_grant_id: str | None = None
