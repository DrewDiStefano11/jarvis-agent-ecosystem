"""Immutable evidence-to-task admission references; never execution approval."""

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.models.self_improvement import Contract, Digest, Identifier


class ImprovementBacklogEntry(Contract):
    schema_version: Literal["1.0"] = "1.0"
    id: Digest
    baseline_id: Digest
    proposal_id: Digest
    weakness_id: Digest
    scope_key: Digest
    task_id: Identifier
    priority: Literal["critical", "high", "medium", "low"]
    work_kind: Literal["gather_evidence", "prepare_experiment"]
    evidence_ids: tuple[Digest, ...] = Field(min_length=1, max_length=4096)
    experiment_digest: Digest | None
    selected_by: Identifier
    selected_at: datetime

    @field_validator("selected_at")
    @classmethod
    def timezone_required(cls, value):
        if value.tzinfo is None:
            raise ValueError("selection timestamp requires a timezone")
        return value


class SelectImprovementRequest(Contract):
    baseline_ids: tuple[Digest, ...] = Field(min_length=1, max_length=8)

    @field_validator("baseline_ids")
    @classmethod
    def unique_baselines(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("duplicate baseline references")
        return tuple(sorted(value))


class ImprovementBacklogItem(Contract):
    entry: ImprovementBacklogEntry
    task_status: str = Field(min_length=1, max_length=40)


class ImprovementBacklogSelection(Contract):
    outcome: Literal["selected", "replayed", "empty", "blocked"]
    entry: ImprovementBacklogEntry | None = None
    scanned_proposals: int = Field(ge=0, le=2048)
    blocked_proposals: int = Field(ge=0, le=2048)

    @model_validator(mode="after")
    def consistent_outcome(self):
        if (self.entry is not None) != (self.outcome in {"selected", "replayed"}):
            raise ValueError("selection outcome must match its admitted entry")
        if self.blocked_proposals > self.scanned_proposals:
            raise ValueError("blocked proposals exceed scan count")
        return self
