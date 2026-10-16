"""Protected development admission and dispatch evidence. No mutation authority."""

from datetime import UTC, datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.model_providers.contracts import (
    ModelExecutionRequest,
    ModelExecutionResponse,
    validate_safe_identifier,
)
from app.models.development_agent import ContextSelection, RepositoryContext
from app.models.development_validation import Contract, Digest
from app.models.self_build import Identifier


class DevelopmentLimits(Contract):
    provider: Identifier
    model: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,200}$")
    completion_criteria: tuple[str, ...] = Field(min_length=1, max_length=16, repr=False)
    maximum_dispatches: int = Field(default=2, ge=1, le=8, strict=True)
    maximum_reserved_output_tokens: int = Field(default=8192, ge=1, le=131072, strict=True)
    output_tokens_per_dispatch: int = Field(default=4096, ge=1, le=16384, strict=True)
    maximum_seconds: int = Field(default=600, ge=1, le=3600, strict=True)
    allow_delete: bool = Field(default=False, strict=True)

    _safe_identifiers = field_validator("provider", "model")(validate_safe_identifier)

    @model_validator(mode="after")
    def bounded(self):
        if (
            self.output_tokens_per_dispatch > self.maximum_reserved_output_tokens
            or any(not text.strip() for text in self.completion_criteria)
            or len("\n".join(self.completion_criteria)) > 2000
        ):
            raise ValueError("DEVELOPMENT_LIMITS_INVALID")
        return self


class AssemblyLimits(Contract):
    maximum_sources: int = Field(ge=1, strict=True)
    maximum_tokens: int = Field(ge=1, strict=True)
    maximum_total_characters: int = Field(ge=1, strict=True)
    cross_project_context_allowed: bool = Field(strict=True)

    @classmethod
    def capture(cls, assembler):
        return cls(**{name: getattr(assembler, name) for name in cls.model_fields})


class AdmissionPlan(Contract):
    schema_version: Literal["1.0"] = "1.0"
    task_id: Identifier
    task_fingerprint: Digest
    runtime_run_id: Identifier
    attempt_id: Identifier
    workspace_id: Identifier
    creation_operation_id: Identifier
    creation_plan_hash: Digest
    workspace_plan_hash: Digest
    workspace_approval_id: Identifier
    actor_id: Identifier
    worker_id: Identifier
    lease_fingerprint: Digest
    initial_context_hash: Digest
    selection: ContextSelection
    limits: DevelopmentLimits
    assembly_limits: AssemblyLimits
    policy_digest: Digest
    plan_hash: Digest


def utc(value):
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("DEVELOPMENT_TIMESTAMP_INVALID")
    return value.astimezone(UTC)


class DevelopmentAdmission(Contract):
    plan: AdmissionPlan
    approval_id: Identifier
    approved_by: Identifier
    admitted_at: datetime

    _utc = field_validator("admitted_at")(utc)


class DispatchReservation(Contract):
    schema_version: Literal["1.0"] = "1.0"
    dispatch_id: Digest
    command_id: Identifier
    ordinal: int = Field(ge=1, le=8, strict=True)
    admission_digest: Digest
    context: RepositoryContext = Field(repr=False)
    request: ModelExecutionRequest = Field(repr=False)
    context_manifest: dict = Field(repr=False)
    request_digest: Digest
    audit_id: Identifier
    reserved_at: datetime

    _utc = field_validator("reserved_at")(utc)


class DispatchFailure(Contract):
    category: Literal[
        "provider_unavailable",
        "authentication_failure",
        "temporary_rate_limit",
        "hard_quota_exhausted",
        "timeout",
        "invalid_model_request",
        "model_unavailable",
        "budget_exceeded",
        "malformed_provider_response",
        "unknown_provider",
        "provider_configuration_failure",
        "provider_execution_disabled",
        "transient_provider_failure",
        "authority_lost",
        "cancelled",
        "internal_error",
    ]
    observed_at: datetime
    audit_id: Identifier
    outcome: Literal["unknown"] = "unknown"

    _utc = field_validator("observed_at")(utc)


class EditingBinding(Contract):
    editing_record_id: Digest
    editing_plan_hash: Digest
    response_digest: Digest
    audit_id: Identifier


class DevelopmentIteration(Contract):
    schema_version: Literal["1.0"] = "1.0"
    task_id: Identifier
    runtime_run_id: Identifier
    attempt_id: Identifier
    workspace_id: Identifier
    dispatch_id: Digest
    ordinal: int = Field(ge=1, le=8)
    provider: Identifier
    requested_model: str
    actual_model: str
    context_hash: Digest
    response_digest: Digest
    editing_record_id: Digest
    editing_plan_hash: Digest
    state: Literal["awaiting_source_approval", "source_recovery_required", "mutation_recorded"] = (
        "awaiting_source_approval"
    )
    current_source_measured: Literal[False] = False


class DevelopmentDispatch(Contract):
    reservation: DispatchReservation
    response: ModelExecutionResponse | None = Field(default=None, repr=False)
    response_audit_id: Identifier | None = None
    failure: DispatchFailure | None = None
    editing_binding: EditingBinding | None = None

    @model_validator(mode="after")
    def response_shape(self):
        if (self.response is None) != (self.response_audit_id is None):
            raise ValueError("DEVELOPMENT_RESPONSE_INCOMPLETE")
        if (self.failure is not None and self.response is not None) or (
            self.editing_binding is not None and self.response is None
        ):
            raise ValueError("DEVELOPMENT_DISPATCH_STATE_INVALID")
        return self
