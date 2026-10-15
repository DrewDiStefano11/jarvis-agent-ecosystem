"""Ephemeral native source measurements do not grant execution authority."""

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from app.models.development_validation import CodeState, Contract, Digest
from app.models.self_build import Identifier


class MutationInspection(Contract):
    operation_id: Identifier
    mutation_plan_hash: Digest
    checkpoint_id: Identifier
    code_state: CodeState
    source_observation_digest: Digest
    matches_completed_mutation: bool = Field(strict=True)
    inspection_fingerprint: Digest
    measured_at: datetime
    current_source_measured: Literal[True] = True
    execution_authorized: Literal[False] = False

    @model_validator(mode="after")
    def aware_measurement(self):
        if self.measured_at.tzinfo is None:
            raise ValueError("SOURCE_MUTATION_INSPECTION_TIME_INVALID")
        return self
