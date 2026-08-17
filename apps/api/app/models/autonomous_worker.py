from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class AutonomousWorkerContract(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


Priority = Literal["low", "medium", "high", "critical"]
BoundedListItem = Annotated[str, Field(min_length=1, max_length=2000)]


class PlanningRecommendation(AutonomousWorkerContract):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=4000)
    priority: Priority


class PlanningRisk(AutonomousWorkerContract):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=4000)
    severity: Priority
    mitigation: str = Field(min_length=1, max_length=4000)


class PlanningReviewResult(AutonomousWorkerContract):
    """The only model-produced structure accepted in Phase 2C."""

    schemaVersion: Literal["1.0"] = "1.0"
    summary: str = Field(min_length=1, max_length=2000)
    analysis: str = Field(min_length=1, max_length=20_000)
    recommendations: list[PlanningRecommendation] = Field(default_factory=list, max_length=32)
    risks: list[PlanningRisk] = Field(default_factory=list, max_length=32)
    assumptions: list[BoundedListItem] = Field(default_factory=list, max_length=32)
    missingInformation: list[BoundedListItem] = Field(default_factory=list, max_length=32)
    requiresHumanReview: bool = False

    @classmethod
    def model_json_schema_instruction(cls) -> str:
        return (
            "Return only one JSON object with exactly these fields: "
            "schemaVersion='1.0'; summary string; analysis string; "
            "recommendations array of {title,description,priority}; risks array of "
            "{title,description,severity,mitigation}; assumptions string array; "
            "missingInformation string array; requiresHumanReview boolean. "
            "Priority and severity are low, medium, high, or critical. "
            "The analysis field is user-facing rationale, never hidden chain-of-thought. "
            "Do not include markdown, tools, commands, URLs to follow, or extra fields."
        )


class ModelExecutionStage(StrEnum):
    PREPARED = "prepared"
    CALL_STARTED = "call_started"
    RESPONSE_RECEIVED = "response_received"
    RESULT_PERSISTED = "result_persisted"
    FINALIZATION_PENDING = "finalization_pending"
    COMPLETED = "completed"
    FAILED = "failed"
    HUMAN_REVIEW_REQUIRED = "human_review_required"


class ModelExecutionResult(AutonomousWorkerContract):
    executionId: str
    runtimeRunId: str
    runtimeAttemptId: str
    taskId: str
    targetAgentId: str
    contextAssemblyId: str
    workerId: str
    stage: ModelExecutionStage
    schemaVersion: Literal["1.0"] = "1.0"
    requestHash: str
    executionRequestHash: str
    resultHash: str | None = None
    provider: str | None = None
    model: str | None = None
    result: PlanningReviewResult | None = None
    inputTokens: int | None = Field(default=None, ge=0)
    outputTokens: int | None = Field(default=None, ge=0)
    requestCount: int = Field(default=0, ge=0, le=2)
    latencyMs: float | None = Field(default=None, ge=0)
    finishReason: str | None = Field(default=None, max_length=120)
    estimatedCostUsd: float | None = Field(default=None, ge=0)
    requiresHumanReview: bool = False
    failureCode: str | None = Field(default=None, max_length=80)
    createdAt: datetime
    updatedAt: datetime
    completedAt: datetime | None = None


class AutonomousWorkerStatus(AutonomousWorkerContract):
    enabled: bool
    modelExecutionMode: Literal["disabled", "local_only"]
    activeExecutionCount: int = 0
    queuedEligibleRuntimeCount: int = 0
    completedExecutionCount: int = 0
    failedExecutionCount: int = 0
    reviewRequiredCount: int = 0
    lastWorkerHeartbeat: datetime | None = None
    lastSuccessfulExecutionAt: datetime | None = None
    providerReady: bool = False
    status: Literal["healthy", "degraded", "disabled"] = "disabled"
    reasonCode: str | None = None
