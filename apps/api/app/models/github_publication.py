"""Bounded GitHub observations; contracts alone never establish publication authority."""

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.models.self_build import Commit, Contract, Identifier

CheckState = Literal[
    "queued", "running", "passed", "failed", "cancelled", "skipped", "stale", "unknown"
]


class RequiredCheck(Contract):
    context: str = Field(min_length=1, max_length=200)
    # None means protection did not bind a particular provider.
    app_id: int | None = Field(default=None, strict=True, gt=0)


class PullRequestCheck(Contract):
    evidence_id: Identifier
    context: str = Field(min_length=1, max_length=200)
    app_id: int | None = Field(default=None, strict=True, gt=0)
    head_sha: Commit
    state: CheckState


class PullRequestReview(Contract):
    evidence_id: Identifier
    reviewer_id: int = Field(strict=True, gt=0)
    head_sha: Commit
    state: Literal["approved", "changes_requested", "commented", "dismissed", "pending"]
    submitted_at: datetime
    independent: bool

    @field_validator("submitted_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("review timestamps require a timezone")
        return value


class PullRequestFinding(Contract):
    evidence_id: Identifier
    head_sha: Commit
    resolved: bool
    # A bounded reference, not untrusted review text or executable instructions.
    review_id: Identifier | None = None


class PullRequestObservation(Contract):
    schema_version: Literal["1.0"] = "1.0"
    repository_id: int = Field(strict=True, gt=0)
    pr_number: int = Field(strict=True, gt=0)
    head_sha: Commit
    head_sha_after_collection: Commit
    base_sha: Commit
    approved_base_sha: Commit
    observed_at: datetime
    state: Literal["open", "closed", "merged"]
    draft: bool
    mergeable: bool | None
    merge_state: Literal["clean", "blocked", "dirty", "behind", "unstable", "draft", "unknown"] = (
        "unknown"
    )
    # Collector sets complete only after all pages and required policy are read.
    complete: bool
    protection_known: bool
    policy_blockers: tuple[str, ...] = Field(default=(), max_length=256)
    required_approvals: int = Field(strict=True, ge=0, le=100)
    required_checks: tuple[RequiredCheck, ...] = Field(max_length=256)
    checks: tuple[PullRequestCheck, ...] = Field(max_length=2048)
    reviews: tuple[PullRequestReview, ...] = Field(max_length=2048)
    findings: tuple[PullRequestFinding, ...] = Field(max_length=2048)

    @field_validator("observed_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observation timestamps require a timezone")
        return value

    @model_validator(mode="after")
    def unique_evidence(self):
        for entries in (self.checks, self.reviews, self.findings):
            if len({entry.evidence_id for entry in entries}) != len(entries):
                raise ValueError("duplicate provider evidence identities")
        if len({(item.context, item.app_id) for item in self.required_checks}) != len(
            self.required_checks
        ):
            raise ValueError("duplicate required check definitions")
        return self


class PullRequestReadiness(Contract):
    repository_id: int = Field(strict=True, gt=0)
    pr_number: int = Field(strict=True, gt=0)
    head_sha: Commit
    ready_for_manual_merge: bool
    blockers: tuple[str, ...]
    stale_evidence_ids: tuple[Identifier, ...]
    # A readiness result never authorizes a merge.
    automatic_merge_allowed: Literal[False] = False
