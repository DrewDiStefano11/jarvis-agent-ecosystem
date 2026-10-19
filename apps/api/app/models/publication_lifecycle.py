"""Durable exact-commit publication contracts; no submitted object grants authority."""

from datetime import datetime
from typing import Literal

from pydantic import Field, model_serializer, model_validator

from app.models.github_publication import PullRequestObservation
from app.models.self_build import Commit, Contract, Digest, Identifier
from app.self_build.policy import digest


class PublicationPlan(Contract):
    workspace_id: Identifier
    task_id: Identifier
    task_title: str = Field(min_length=1, max_length=200)
    task_summary: str = Field(max_length=3000)
    actor_id: Identifier
    runtime_run_id: Identifier
    attempt_id: Identifier
    native_command_id: Identifier
    native_plan_hash: Digest
    repository_id: int = Field(strict=True, gt=0)
    repository_identity: str = Field(max_length=200)
    branch: str = Field(max_length=200)
    base_branch: str = Field(max_length=200)
    approved_base_sha: Commit
    source_base_sha: Commit | None = None
    parent_sha: Commit
    commit_sha: Commit
    expected_remote_sha: Commit | None
    source_snapshot_digest: Digest
    validation_journal_id: Identifier
    validation_execution_plan_hash: Digest
    validation_code_state_hash: Digest
    validation_journal_digest: Digest
    publisher_ids: tuple[int, ...] = Field(min_length=1, max_length=256)
    implementation_policy_digest: Digest
    plan_hash: Digest

    @property
    def validation_base_sha(self):
        return self.source_base_sha or self.approved_base_sha

    @model_serializer(mode="wrap")
    def compatible_shape(self, handler):
        value = handler(self)
        if self.source_base_sha is None:
            value.pop("source_base_sha", None)
        return value

    @model_validator(mode="after")
    def exact(self):
        from app.github_publication.transport import safe_branch

        if (
            not safe_branch(self.branch)
            or not self.branch.startswith("codex/")
            or not safe_branch(self.base_branch)
            or self.branch == self.base_branch
            or self.expected_remote_sha not in {None, self.parent_sha}
            or (self.source_base_sha is not None and self.source_base_sha != self.parent_sha)
            or any(type(value) is not int or value <= 0 for value in self.publisher_ids)
            or len(set(self.publisher_ids)) != len(self.publisher_ids)
            or self.plan_hash != digest(self.model_dump(mode="json", exclude={"plan_hash"}))
        ):
            raise ValueError("publication plan requires exact bounded task/repository scope")
        return self


class PublicationEntry(Contract):
    operation_id: Identifier
    sequence: int = Field(strict=True, ge=1, le=128)
    previous_digest: Digest
    kind: Literal[
        "prepared",
        "push_intent",
        "pushed",
        "pr_intent",
        "pr_created",
        "observation",
        "blocked",
        "resumed",
    ]
    plan: PublicationPlan | None = None
    request_digest: Digest | None = None
    pr_number: int | None = Field(default=None, strict=True, gt=0)
    pr_url: str | None = Field(default=None, max_length=500)
    observation: PullRequestObservation | None = None
    validation_confirmed_digest: Digest | None = None
    error_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{0,99}$")
    retry_not_before: datetime | None = None
    timestamp: datetime
    entry_digest: Digest

    @model_validator(mode="after")
    def exact(self):
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("publication entry requires timezone")
        if (
            (self.kind == "prepared") != (self.plan is not None)
            or (self.kind in {"push_intent", "pr_intent"}) != (self.request_digest is not None)
            or (self.kind == "pr_created")
            != (self.pr_number is not None and self.pr_url is not None)
            or (
                self.kind != "pr_created"
                and (self.pr_number is not None or self.pr_url is not None)
            )
            or (self.kind == "observation") != (self.observation is not None)
            or (self.kind != "observation" and self.validation_confirmed_digest is not None)
            or (self.kind == "blocked") != (self.error_code is not None)
            or (self.kind != "blocked" and self.retry_not_before is not None)
            or (self.retry_not_before is not None and self.retry_not_before.tzinfo is None)
            or self.entry_digest != digest(self.model_dump(mode="json", exclude={"entry_digest"}))
        ):
            raise ValueError("publication entry shape or digest differs")
        return self


class PublicationSnapshot(Contract):
    operation_id: Identifier
    plan: PublicationPlan
    sequence: int
    digest: Digest
    state: Literal[
        "preparing",
        "committing",
        "pushing",
        "pr_created",
        "validation_pending",
        "review_pending",
        "blocked",
        "completed",
    ]
    push_request_digest: Digest | None = None
    branch_published: bool = False
    pr_request_digest: Digest | None = None
    pr_number: int | None = None
    pr_url: str | None = None
    observation: PullRequestObservation | None = None
    error_code: str | None = None
    retry_not_before: datetime | None = None


class PublicationRequest(Contract):
    command_id: Identifier
    expected_plan_hash: Digest
    approval_id: Identifier
    worker_id: Identifier
    lease_token: str = Field(min_length=1, max_length=200, repr=False)


class PreviewPublication(Contract):
    native_command_id: Identifier
    validation_journal_id: Identifier


class ApprovePublication(Contract):
    plan: PublicationPlan
    expected_plan_hash: Digest
    valid_for_seconds: int = Field(default=900, strict=True, ge=1, le=3600)


class PublicationApproval(Contract):
    approval_id: Identifier
    approved_by: Identifier
    expires_at: datetime
    plan: PublicationPlan


class AdvancePublication(Contract):
    plan: PublicationPlan
    execution: PublicationRequest


class PublicationStatus(Contract):
    workspace_id: Identifier
    state: Literal[
        "preparing",
        "committing",
        "pushing",
        "pr_created",
        "validation_pending",
        "review_pending",
        "blocked",
        "completed",
    ]
    branch: str
    current_commit_sha: Commit | None
    operation_id: Identifier | None = None
    pr_number: int | None = None
    pr_url: str | None = None
    observation: PullRequestObservation | None = None
    error_code: str | None = None
    retry_not_before: datetime | None = None
    ready_for_manual_merge: bool = False
    automatic_merge_allowed: Literal[False] = False
