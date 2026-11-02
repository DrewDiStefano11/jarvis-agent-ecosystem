"""Operator policy and immutable advisory scheduler evidence, never execution grants."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.models.self_improvement import (
    Contract,
    Digest,
    EvaluationObservation,
    Identifier,
    SourceProvenance,
)

Revision = Annotated[str, Field(pattern=r"^[a-f0-9]{40}$")]


class ImprovementSchedulePolicy(Contract):
    enabled: bool = False
    actor_id: Identifier = "unconfigured"
    interval_seconds: int = Field(default=3600, ge=300, le=86400)
    lookback_seconds: int = Field(default=3600, ge=300, le=86400)
    repo_sha: Revision | None = None
    configuration_fingerprint: Digest | None = None
    safety_fingerprint: Digest | None = None
    auto_admit: bool = False

    @model_validator(mode="after")
    def activation(self):
        if self.enabled and (
            self.actor_id == "unconfigured"
            or self.actor_id != self.actor_id.strip()
            or not self.repo_sha
            or not self.configuration_fingerprint
            or not self.safety_fingerprint
        ):
            raise ValueError(
                "Scheduling requires exact operator identity and evidence fingerprints"
            )
        return self


class ImprovementScheduleCapture(Contract):
    schema_version: Literal["1.0"] = "1.0"
    id: Digest
    policy_digest: Digest
    window_end: datetime
    provenance: SourceProvenance
    observations: tuple[EvaluationObservation, ...] = Field(max_length=4096)
    snapshot_digest: Digest

    @model_validator(mode="after")
    def identity(self):
        from app.self_improvement.engine import digest

        if self.window_end.tzinfo is None:
            raise ValueError("schedule window requires a timezone")
        if self.id != digest(
            ["improvement-schedule-v1", self.policy_digest, int(self.window_end.timestamp())]
        ):
            raise ValueError("schedule capture identity differs")
        if self.snapshot_digest != digest(
            [
                self.provenance.model_dump(mode="json"),
                [item.model_dump(mode="json") for item in self.observations],
            ]
        ):
            raise ValueError("schedule snapshot differs")
        return self


class ImprovementScheduleResult(Contract):
    schema_version: Literal["1.0"] = "1.0"
    capture_id: Digest
    baseline_id: Digest
    outcome: Literal["analyzed", "selected", "empty", "blocked"]
    task_id: Identifier | None = None

    @model_validator(mode="after")
    def selected(self):
        if (self.outcome == "selected") != (self.task_id is not None):
            raise ValueError("selected outcome requires its native task reference")
        return self


class ImprovementScheduleStatus(Contract):
    enabled: bool
    auto_admit: bool
    interval_seconds: int = Field(ge=300, le=86400)
    capture_id: Digest | None = None
    window_end: datetime | None = None
    result: ImprovementScheduleResult | None = None
