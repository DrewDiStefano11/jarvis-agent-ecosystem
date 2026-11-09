"""Operator decisions bind immutable model evidence to native runtime position."""

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from app.models.agent_runtime import RuntimeCommand, RuntimeContract
from app.models.autonomous_worker import ModelExecutionResult


class ResultReviewerSetup(RuntimeContract):
    confirmation: Literal["enable_local_result_review"]


class ResultReviewer(RuntimeContract):
    actorId: str


class ReviewResultCommand(RuntimeCommand):
    command_type: Literal["review_result"] = "review_result"
    actor_reference: str = Field(min_length=1, max_length=120)
    execution_id: str = Field(min_length=1, max_length=80)
    expected_result_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_checkpoint_id: str = Field(min_length=1, max_length=120)
    expected_task_updated_at: datetime
    decision: Literal["accept", "request_changes", "reject"]
    reason: str = Field(min_length=3, max_length=2000)
    revised_request: str | None = Field(default=None, min_length=3, max_length=2000)

    @model_validator(mode="after")
    def validate_revision(self):
        if (self.decision == "request_changes") != (self.revised_request is not None):
            raise ValueError("Only request_changes requires a full revised_request")
        if self.expected_task_updated_at.tzinfo is None:
            raise ValueError("expected_task_updated_at requires a timezone")
        if not self.reason.strip() or (
            self.revised_request is not None and not self.revised_request.strip()
        ):
            raise ValueError("Review text cannot be blank")
        return self


class ResultReviewItem(RuntimeContract):
    execution: ModelExecutionResult
    taskTitle: str
    taskUpdatedAt: datetime
    runVersion: int
    checkpointId: str


class ResultReviewReceipt(RuntimeContract):
    id: str
    executionId: str
    taskId: str
    actorId: str
    commandId: str
    decision: Literal["accept", "request_changes", "reject"]
    resultHash: str
    artifactId: str | None = None
    correctionTaskId: str | None = None
    reviewedAt: datetime


class ReviewedArtifact(RuntimeContract):
    id: str
    taskId: str
    name: str
    summary: str
    createdAt: datetime
    resultHash: str
    review: ResultReviewReceipt
