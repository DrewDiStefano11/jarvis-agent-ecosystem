"""Durable operator intake contracts; saving input never grants execution authority."""

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from app.models.domain import ContractModel, RequestContractModel


class CreateGoalRequest(RequestContractModel):
    request: str = Field(min_length=3, max_length=2000)

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
