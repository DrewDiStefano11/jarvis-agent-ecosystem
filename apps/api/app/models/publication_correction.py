"""Bounded correction triage; references never authorize source effects."""

from typing import Literal

from pydantic import Field, model_validator

from app.models.self_build import Commit, Contract, Digest, Identifier
from app.self_build.policy import digest


class PublicationCorrectionTriage(Contract):
    schema_version: Literal["1.0"] = "1.0"
    task_id: Identifier
    workspace_id: Identifier
    publication_operation_id: Identifier
    source_link_hash: Digest
    observation_digest: Digest
    expected_head_sha: Commit
    disposition: Literal[
        "review_correction_required",
        "investigation_required",
        "checks_pending",
        "no_actionable_evidence",
        "blocked",
    ]
    evidence_ids: tuple[Identifier, ...] = Field(max_length=32)
    evidence_count: int = Field(strict=True, ge=0, le=6144)
    blockers: tuple[str, ...] = Field(max_length=16)
    dispatch_authorized: Literal[False] = False
    mutation_authority: Literal[False] = False
    triage_hash: Digest

    @model_validator(mode="after")
    def exact(self):
        if (
            tuple(sorted(set(self.evidence_ids))) != self.evidence_ids
            or self.evidence_count < len(self.evidence_ids)
            or self.triage_hash != digest(self.model_dump(mode="json", exclude={"triage_hash"}))
        ):
            raise ValueError("correction triage requires bounded immutable evidence references")
        return self
