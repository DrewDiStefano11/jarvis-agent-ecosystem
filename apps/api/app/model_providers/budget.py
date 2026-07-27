from __future__ import annotations

import logging
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from app.model_providers.contracts import ModelExecutionRequest, ModelExecutionResponse
from app.model_providers.errors import BudgetExceededError

logger = logging.getLogger(__name__)


class TaskBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    maximum_requests: int = Field(default=1, ge=1)
    maximum_input_tokens: int | None = Field(default=None, ge=1)
    maximum_output_tokens: int | None = Field(default=None, ge=1)
    maximum_total_tokens: int | None = Field(default=None, ge=1)
    maximum_cost_usd: float | None = Field(default=None, gt=0)
    reject_unknown_usage: bool = True


class ModelPricing(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_per_million_usd: float = Field(ge=0)
    output_per_million_usd: float = Field(ge=0)


@dataclass
class BudgetUsage:
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0
    usage_known: bool = True


class BudgetTracker:
    def __init__(self, budget: TaskBudget, pricing: dict[str, ModelPricing] | None = None) -> None:
        self.budget = budget
        self.pricing = pricing or {}
        self.usage = BudgetUsage()

    def before_attempt(self, request: ModelExecutionRequest) -> None:
        if self.usage.requests >= self.budget.maximum_requests:
            logger.info(
                "model budget exceeded category=request task_id=%s correlation_id=%s",
                request.task_id,
                request.correlation_id,
            )
            self._raise("request budget exhausted", request)
        if (
            request.max_output_tokens is not None
            and self.budget.maximum_output_tokens is not None
            and self.usage.output_tokens + request.max_output_tokens
            > self.budget.maximum_output_tokens
        ):
            logger.info(
                "model budget exceeded category=output_preflight task_id=%s correlation_id=%s",
                request.task_id,
                request.correlation_id,
            )
            self._raise("maximum output token request exceeds remaining budget", request)
        if (
            request.max_output_tokens is not None
            and self.budget.maximum_total_tokens is not None
            and self.usage.total_tokens + request.max_output_tokens
            > self.budget.maximum_total_tokens
        ):
            logger.info(
                "model budget exceeded category=total_preflight task_id=%s correlation_id=%s",
                request.task_id,
                request.correlation_id,
            )
            self._raise("maximum output token request exceeds total token budget", request)
        self.usage.requests += 1
        if self.budget.maximum_requests - self.usage.requests <= 1:
            logger.info(
                "model budget threshold reached requests=%s maximum_requests=%s "
                "task_id=%s correlation_id=%s",
                self.usage.requests,
                self.budget.maximum_requests,
                request.task_id,
                request.correlation_id,
            )

    def record(self, response: ModelExecutionResponse) -> float | None:
        values = (response.input_tokens, response.output_tokens, response.total_tokens)
        if any(value is None for value in values):
            self.usage.usage_known = False
            if self.budget.reject_unknown_usage and any(
                limit is not None
                for limit in (
                    self.budget.maximum_input_tokens,
                    self.budget.maximum_output_tokens,
                    self.budget.maximum_total_tokens,
                )
            ):
                raise BudgetExceededError(
                    "provider did not report usage required by the task budget",
                    provider=response.provider,
                    model=response.model,
                    task_id=response.task_id,
                    correlation_id=response.correlation_id,
                )
        input_tokens = response.input_tokens or 0
        output_tokens = response.output_tokens or 0
        total_tokens = response.total_tokens
        if (
            total_tokens is None
            and response.input_tokens is not None
            and response.output_tokens is not None
        ):
            total_tokens = input_tokens + output_tokens
        self.usage.input_tokens += input_tokens
        self.usage.output_tokens += output_tokens
        self.usage.total_tokens += total_tokens or 0
        pricing = self.pricing.get(response.model)
        cost = None
        if pricing and response.input_tokens is not None and response.output_tokens is not None:
            cost = (
                input_tokens * pricing.input_per_million_usd
                + output_tokens * pricing.output_per_million_usd
            ) / 1_000_000
            self.usage.cost_usd += cost
        self._enforce(response)
        return cost

    def _enforce(self, response: ModelExecutionResponse) -> None:
        checks = (
            (self.budget.maximum_input_tokens, self.usage.input_tokens, "input token"),
            (self.budget.maximum_output_tokens, self.usage.output_tokens, "output token"),
            (self.budget.maximum_total_tokens, self.usage.total_tokens, "total token"),
            (self.budget.maximum_cost_usd, self.usage.cost_usd, "cost"),
        )
        for limit, actual, label in checks:
            if limit is not None and actual > limit:
                logger.info(
                    "model budget exceeded category=%s actual=%s limit=%s "
                    "task_id=%s correlation_id=%s",
                    label.replace(" ", "_"),
                    actual,
                    limit,
                    response.task_id,
                    response.correlation_id,
                )
                raise BudgetExceededError(
                    f"{label} budget exceeded",
                    provider=response.provider,
                    model=response.model,
                    task_id=response.task_id,
                    correlation_id=response.correlation_id,
                )

    @staticmethod
    def _raise(message: str, request: ModelExecutionRequest) -> None:
        raise BudgetExceededError(
            message,
            model=request.model,
            task_id=request.task_id,
            correlation_id=request.correlation_id,
        )
