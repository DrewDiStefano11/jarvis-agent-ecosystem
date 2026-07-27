from __future__ import annotations

import logging

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.model_providers.budget import BudgetTracker, TaskBudget
from app.model_providers.contracts import (
    ModelCapability,
    ModelExecutionRequest,
    ModelExecutionResponse,
    ModelProvider,
)
from app.model_providers.errors import (
    AuthenticationError,
    BudgetExceededError,
    InvalidModelRequestError,
    ModelProviderError,
    ProviderExecutionDisabledError,
    QuotaExhaustedError,
    UnknownProviderError,
)
from app.model_providers.registry import ProviderRegistry
from app.model_providers.retry import RetryExecutor

logger = logging.getLogger(__name__)


class RoutingRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested_provider: str | None = None
    required_capability: ModelCapability | None = None
    preferred_model: str | None = None
    prefer_local: bool = True
    allow_remote: bool = False
    allow_fallback: bool = False
    allow_quota_fallback: bool = False
    maximum_fallbacks: int = Field(default=0, ge=0, le=20)
    provider_allowlist: frozenset[str] | None = None
    provider_denylist: frozenset[str] = frozenset()

    @model_validator(mode="after")
    def validate_policy(self) -> RoutingRequirements:
        if self.provider_allowlist and self.provider_allowlist & self.provider_denylist:
            raise ValueError("provider allowlist and denylist overlap")
        if not self.allow_fallback and self.maximum_fallbacks:
            raise ValueError("maximum_fallbacks requires allow_fallback")
        if self.allow_quota_fallback and not self.allow_fallback:
            raise ValueError("quota fallback requires allow_fallback")
        return self


class ModelRouter:
    def __init__(self, registry: ProviderRegistry, retry: RetryExecutor) -> None:
        self.registry = registry
        self.retry = retry

    async def eligible(
        self,
        request: ModelExecutionRequest,
        requirements: RoutingRequirements,
    ) -> list[ModelProvider]:
        capability = requirements.required_capability or request.required_capability
        providers = self.registry.list()
        if requirements.requested_provider:
            requested = self.registry.get(requirements.requested_provider)
            providers = [requested]
            if requirements.allow_fallback:
                providers += [item for item in self.registry.list() if item is not requested]
        filtered = [
            provider
            for provider in providers
            if (capability is None or capability in provider.capabilities)
            and (
                requirements.provider_allowlist is None
                or provider.name in requirements.provider_allowlist
            )
            and provider.name not in requirements.provider_denylist
            and (requirements.allow_remote or provider.is_local)
        ]
        health = await self.registry.health(filtered)
        filtered = [provider for provider in filtered if health[provider.name].healthy]
        available: list[ModelProvider] = []
        for provider in filtered:
            routed_model = requirements.preferred_model or request.model or provider.default_model
            known = health[provider.name].model_available
            if routed_model != provider.default_model or known is None:
                known = await provider.model_available(routed_model)
            if known is not False:
                available.append(provider)
        filtered = available
        if requirements.requested_provider:
            requested = requirements.requested_provider
            requested_items = [provider for provider in filtered if provider.name == requested]
            if not requested_items and not requirements.allow_fallback:
                raise UnknownProviderError(
                    "explicitly requested provider is not eligible", provider=requested
                )
        if requirements.prefer_local and not requirements.requested_provider:
            filtered.sort(key=lambda provider: not provider.is_local)
        return filtered

    async def execute(
        self,
        *,
        request: ModelExecutionRequest,
        requirements: RoutingRequirements,
        budget: TaskBudget,
        pricing: dict | None = None,
    ) -> ModelExecutionResponse:
        tracker = BudgetTracker(budget, pricing)
        candidates = await self.eligible(request, requirements)
        if not candidates:
            raise UnknownProviderError("no healthy provider satisfies the routing requirements")
        allowed_count = 1 + (requirements.maximum_fallbacks if requirements.allow_fallback else 0)
        attempted: list[str] = []
        failures: list[str] = []
        for provider in candidates[:allowed_count]:
            attempted.append(provider.name)
            routed_request = request.model_copy(
                update={
                    "model": requirements.preferred_model or request.model or provider.default_model
                }
            )
            logger.info(
                "model request started provider=%s model=%s task_id=%s correlation_id=%s",
                provider.name,
                routed_request.model,
                request.task_id,
                request.correlation_id,
            )
            try:
                response = await self.retry.execute(
                    lambda provider=provider, routed_request=routed_request: provider.execute(
                        routed_request
                    ),
                    request=routed_request,
                    budget=tracker,
                    provider_name=provider.name,
                )
                response.estimated_cost_usd = tracker.record(response)
                response.routing_metadata = {
                    "selected_provider": provider.name,
                    "selected_model": response.model,
                    "is_local": provider.is_local,
                    "fallback_count": len(attempted) - 1,
                    "attempted_providers": attempted,
                    "failure_categories": failures,
                }
                logger.info(
                    "model request completed provider=%s model=%s latency_ms=%.3f",
                    provider.name,
                    response.model,
                    response.latency_ms,
                )
                return response
            except ModelProviderError as exc:
                failures.append(type(exc).__name__)
                logger.info(
                    "model request failed provider=%s category=%s retryable=%s",
                    provider.name,
                    type(exc).__name__,
                    exc.retryable,
                )
                if isinstance(
                    exc,
                    (
                        AuthenticationError,
                        InvalidModelRequestError,
                        BudgetExceededError,
                        ProviderExecutionDisabledError,
                    ),
                ):
                    raise
                if isinstance(exc, QuotaExhaustedError) and not requirements.allow_quota_fallback:
                    raise
                if (
                    not requirements.allow_fallback
                    or provider is candidates[min(len(candidates), allowed_count) - 1]
                ):
                    raise
        raise UnknownProviderError("provider routing exhausted without a result")
