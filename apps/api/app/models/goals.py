"""Durable intake; text grants no authority, and optional consent is captured separately."""

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_serializer

from app.models.domain import ContractModel, RequestContractModel
from app.models.goal_planning import GoalPlanningConsent, GoalPlanningLink


class CreateGoalRequest(RequestContractModel):
    request: str = Field(min_length=3, max_length=2000)
    planningConsent: GoalPlanningConsent | None = None

    @model_serializer(mode="wrap")
    def preserve_draft_body(self, handler):
        result = handler(self)
        if self.planningConsent is None:
            result.pop("planningConsent", None)
        return result

    @field_validator("request")
    @classmethod
    def nonblank_request(cls, value: str) -> str:
        if len(value.strip()) < 3:
            raise ValueError("Describe an outcome in at least three nonblank characters.")
        return value


class GoalMessage(ContractModel):
    id: str
    role: Literal["user"] = "user"
    content: str
    createdAt: datetime


class Goal(ContractModel):
    id: str
    conversationId: str
    taskId: str
    title: str
    disposition: Literal["draft"] = "draft"
    createdAt: datetime
    messages: list[GoalMessage]
    planning: GoalPlanningLink | None = None

    @model_serializer(mode="wrap")
    def preserve_draft_response(self, handler):
        result = handler(self)
        if self.planning is None:
            result.pop("planning", None)
        return result
