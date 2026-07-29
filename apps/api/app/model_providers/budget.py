from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.model_providers.contracts import ModelExecutionRequest, ModelExecutionResponse
from app.model_providers.errors import BudgetExceededError


class TaskBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    maximum_requests: int = Field(default=1, ge=1, le=10_000)
    maximum_input_tokens: int | None = Field(default=None, ge=1)
    maximum_output_tokens: int | None = Field(default=None, ge=1)
    maximum_total_tokens: int | None = Field(default=None, ge=1)
    maximum_cost_usd: Decimal | None = Field(default=None, gt=0)
    reject_unknown_usage: bool = True


class ModelPricing(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_per_million_usd: Decimal = Field(ge=0)
    output_per_million_usd: Decimal = Field(ge=0)


@dataclass
class BudgetUsage:
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: Decimal = Decimal("0")
    usage_known: bool = True


class BudgetTracker:
    def __init__(self, budget: TaskBudget, pricing: dict[str, ModelPricing] | None = None) -> None:
        self.budget = budget
        self.pricing = pricing or {}
        self.usage = BudgetUsage()

    def before_attempt(
        self, request: ModelExecutionRequest, *, provider: str | None = None
    ) -> None:
        if self.usage.requests >= self.budget.maximum_requests:
            self._raise("request budget exhausted", request, provider)
        if self.budget.maximum_cost_usd is not None:
            if request.model is None or request.model not in self.pricing:
                self._raise(
                    "cost budget requires exact pricing for the routed model",
                    request,
                    provider,
                    reason="pricing_unavailable",
                )
            if not self.usage.usage_known:
                self._raise(
                    "cost budget accounting is unavailable after unknown usage",
                    request,
                    provider,
                    reason="usage_unavailable_for_cost_budget",
                )
        if (
            request.max_output_tokens is not None
            and self.budget.maximum_output_tokens is not None
            and self.usage.output_tokens + request.max_output_tokens
            > self.budget.maximum_output_tokens
        ):
            self._raise("maximum output token request exceeds remaining budget", request, provider)
        if (
            request.max_output_tokens is not None
            and self.budget.maximum_total_tokens is not None
            and self.usage.total_tokens + request.max_output_tokens
            > self.budget.maximum_total_tokens
        ):
            self._raise(
                "maximum output token request exceeds total token budget", request, provider
            )
        self.usage.requests += 1

    def record(self, response: ModelExecutionResponse) -> float | None:
        input_known = response.input_tokens is not None
        output_known = response.output_tokens is not None
        total_known = response.total_tokens is not None
        relevant_token_limit = any(
            limit is not None
            for limit in (
                self.budget.maximum_input_tokens,
                self.budget.maximum_output_tokens,
                self.budget.maximum_total_tokens,
            )
        )
        if not (input_known and output_known and total_known):
            self.usage.usage_known = False
            if self.budget.maximum_cost_usd is not None and not (input_known and output_known):
                self._raise_response(
                    "provider did not report usage required by the cost budget",
                    response,
                    reason="usage_unavailable_for_cost_budget",
                )
            if relevant_token_limit and self.budget.reject_unknown_usage:
                self._raise_response(
                    "provider did not report usage required by the token budget", response
                )
        input_tokens = response.input_tokens or 0
        output_tokens = response.output_tokens or 0
        total_tokens = response.total_tokens
        if total_tokens is None and input_known and output_known:
            total_tokens = input_tokens + output_tokens
        self.usage.input_tokens += input_tokens
        self.usage.output_tokens += output_tokens
        self.usage.total_tokens += total_tokens or 0

        pricing = self.pricing.get(response.model)
        if self.budget.maximum_cost_usd is not None and pricing is None:
            self._raise_response(
                "cost budget cannot account for the exact provider response model",
                response,
                reason="response_model_pricing_unavailable",
            )
        cost: Decimal | None = None
        if pricing is not None and input_known and output_known:
            cost = (
                Decimal(input_tokens) * pricing.input_per_million_usd
                + Decimal(output_tokens) * pricing.output_per_million_usd
            ) / Decimal(1_000_000)
            self.usage.cost_usd += cost
        self._enforce(response)
        return float(cost) if cost is not None else None

    def _enforce(self, response: ModelExecutionResponse) -> None:
        checks = (
            (self.budget.maximum_input_tokens, self.usage.input_tokens, "input token"),
            (self.budget.maximum_output_tokens, self.usage.output_tokens, "output token"),
            (self.budget.maximum_total_tokens, self.usage.total_tokens, "total token"),
            (self.budget.maximum_cost_usd, self.usage.cost_usd, "cost"),
        )
        for limit, actual, label in checks:
            if limit is not None and actual > limit:
                self._raise_response(f"{label} budget exceeded", response)

    @staticmethod
    def _raise(
        message: str,
        request: ModelExecutionRequest,
        provider: str | None,
        *,
        reason: str | None = None,
    ) -> None:
        raise BudgetExceededError(
            message,
            provider=provider,
            model=request.model,
            task_id=request.task_id,
            correlation_id=request.correlation_id,
            metadata={"reason": reason} if reason else None,
        )

    @staticmethod
    def _raise_response(
        message: str, response: ModelExecutionResponse, *, reason: str | None = None
    ) -> None:
        raise BudgetExceededError(
            message,
            provider=response.provider,
            model=response.model,
            task_id=response.task_id,
            correlation_id=response.correlation_id,
            metadata={"reason": reason} if reason else None,
        )
