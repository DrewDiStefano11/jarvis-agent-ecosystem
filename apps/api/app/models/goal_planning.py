"""Exact local standing consent for a bounded number of new planning goals."""

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from app.models.agent_runtime import RuntimeContract, ensure_utc_datetime
from app.models.execution_budget import ExecutionBudget, StageBudget


def direct_goal_budget() -> ExecutionBudget:
    return ExecutionBudget(
        maximumRequests=1,
        maximumTokens=7500,
        maximumAttempts=1,
        stages=(
            StageBudget(
                stage="planning",
                maximumRequests=1,
                maximumInputTokens=5964,
                maximumOutputTokens=1536,
            ),
        ),
    )


class GoalPlanningConsent(RuntimeContract):
    policyId: str = Field(min_length=1, max_length=80)
    policyHash: str = Field(pattern=r"^[a-f0-9]{64}$")


class GoalPlanningLink(GoalPlanningConsent):
    runId: str = Field(min_length=1, max_length=120)


class GoalPlanningOperatorSetup(RuntimeContract):
    confirmation: Literal["configure_bounded_goal_planning"]


class GoalPlanningOperator(RuntimeContract):
    actorId: str


class ConfigureGoalPlanning(RuntimeContract):
    confirmation: Literal["allow_bounded_goal_planning"]
    expectedRevision: int = Field(ge=0, strict=True)
    maximumGoals: int = Field(ge=1, le=20, strict=True)
    expiresAt: datetime
    profile: Literal["direct-v1"]

    @field_validator("expiresAt")
    @classmethod
    def utc_expiry(cls, value):
        return ensure_utc_datetime(value, field_name="expiresAt")


class RevokeGoalPlanning(RuntimeContract):
    expectedPolicyHash: str = Field(pattern=r"^[a-f0-9]{64}$")


class GoalPlanningPolicyView(RuntimeContract):
    id: str | None = None
    revision: int = 0
    enabled: bool = False
    operatorReviewRequired: Literal[True] = True
    profile: Literal["direct-v1"] = "direct-v1"
    policyHash: str | None = None
    maximumGoals: int = 0
    admittedGoals: int = 0
    expiresAt: datetime | None = None
    budget: ExecutionBudget = Field(default_factory=direct_goal_budget)
    unavailableReason: str | None = None


class GoalPlanningAdmissionView(RuntimeContract):
    goalId: str
    taskId: str
    runId: str
    state: str
    errorCode: str | None = None
