"""Bounded projections of an operator-exported GitHub Actions run."""

from datetime import datetime
from typing import Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.models.self_improvement import Contract, Identifier

Status = Literal["requested", "waiting", "pending", "queued", "in_progress", "completed"]
Conclusion = Literal[
    "",
    "success",
    "failure",
    "timed_out",
    "startup_failure",
    "cancelled",
    "skipped",
    "neutral",
    "action_required",
    "stale",
]


class CIJobEvidence(Contract):
    # Native gh output also includes URLs, steps and timestamps. Those fields
    # are deliberately discarded, including untrusted step/command text.
    model_config = ConfigDict(extra="ignore", frozen=True, hide_input_in_errors=True)
    databaseId: int = Field(strict=True, gt=0, le=2**63 - 1)
    name: Identifier
    status: Status
    conclusion: Conclusion

    @model_validator(mode="after")
    def conclusion_requires_completion(self):
        if self.status != "completed" and self.conclusion:
            raise ValueError("unfinished CI jobs cannot claim a conclusion")
        if self.status == "completed" and not self.conclusion:
            raise ValueError("completed CI jobs require a recorded conclusion")
        return self


class CIRunEvidence(Contract):
    model_config = ConfigDict(extra="ignore", frozen=True, hide_input_in_errors=True)
    databaseId: int = Field(strict=True, gt=0, le=2**63 - 1)
    headSha: str = Field(pattern=r"^[a-f0-9]{40,64}$")
    status: Status
    conclusion: Conclusion
    updatedAt: datetime
    jobs: tuple[CIJobEvidence, ...] = Field(max_length=256)

    @field_validator("updatedAt")
    @classmethod
    def timestamp_requires_timezone(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("CI evidence requires a timezone-aware observation timestamp")
        return value

    @model_validator(mode="after")
    def coherent_run(self):
        if len({job.databaseId for job in self.jobs}) != len(self.jobs):
            raise ValueError("duplicate CI job identities")
        if self.status != "completed" and self.conclusion:
            raise ValueError("unfinished CI runs cannot claim a conclusion")
        if self.status == "completed" and (
            not self.conclusion or any(job.status != "completed" for job in self.jobs)
        ):
            raise ValueError("completed CI runs require completed job observations")
        return self
