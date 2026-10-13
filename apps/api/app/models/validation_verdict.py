"""Durable evidence assessment references, never publication or mutation authority."""

from datetime import datetime
from typing import Literal

from pydantic import field_validator

from app.models.development_validation import Contract, Digest, ValidationAssessment
from app.models.validation_execution import Identifier
from app.models.validation_repair import RepairEvidence


class ValidationVerdict(Contract):
    schema_version: Literal["1.0"] = "1.0"
    workspace_id: Identifier
    journal_id: Identifier
    journal_digest: Digest
    approval_id: Identifier
    execution_plan_hash: Digest
    current_code_state_hash: Digest
    observed_at: datetime
    assessment: ValidationAssessment
    repair: RepairEvidence
    # A stored verdict is historical evidence. Consumers must reassess current
    # source/policy/freshness and independently establish execution provenance.
    execution_authorized: Literal[False] = False
    source_mutation_authorized: Literal[False] = False
    publication_authorized: Literal[False] = False
    verdict_digest: Digest

    @field_validator("observed_at")
    @classmethod
    def aware_observation(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("verdict requires a timezone-aware observation")
        return value
