"""Durable proposal contracts, deliberately separate from mutation authority."""

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from app.models.development_validation import (
    CodeState,
    Contract,
    Digest,
    ValidationPlan,
)
from app.models.self_build import Identifier
from app.models.source_change import (
    SourceChangeIntent,
    SourceChangePlan,
    SourceObservation,
)


class PersistEditingPlanRequest(Contract):
    workspace_id: Identifier
    expected_workspace_version: int = Field(ge=1, strict=True)
    expected_workspace_plan_hash: Digest
    command_id: Identifier
    source_intent: SourceChangeIntent


class EditingPlan(Contract):
    schema_version: Literal["1.0"] = "1.0"
    editing_policy_digest: Digest
    workspace_id: Identifier
    workspace_version: int = Field(ge=1, strict=True)
    workspace_plan_hash: Digest
    task_id: Identifier
    runtime_run_id: Identifier
    runtime_version: int = Field(ge=1, strict=True)
    runtime_attempt_id: Identifier | None
    source_plan: SourceChangePlan
    observation: SourceObservation
    original_code_state: CodeState
    expected_code_state: CodeState
    validation_plan: ValidationPlan
    # No assertion that projected bytes exist or tests were run.
    state: Literal["proposed"] = "proposed"
    mutation_supported: Literal[False] = False
    evidence_status: Literal["unmeasured"] = "unmeasured"
    plan_hash: Digest


class EditingPlanRecord(Contract):
    record_id: Digest
    actor_id: Identifier
    command_id: Identifier
    request_hash: Digest
    plan: EditingPlan
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def aware_time(cls, value):
        if value.tzinfo is None:
            raise ValueError("editing plan timestamps require a timezone")
        return value
