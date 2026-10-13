from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.model_providers.budget import BudgetTracker, PricingTable, TaskBudget
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
    MalformedProviderResponseError,
    ModelProviderError,
    ProviderExecutionDisabledError,
    QuotaExhaustedError,
    UnknownProviderError,
)
from app.model_providers.registry import ProviderRegistry
from app.model_providers.retry import RetryExecutor
from app.model_providers.tool_calls import validate_tool_response

logger = logging.getLogger(__name__)


class RoutingRequirements(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested_provider: str | None = Field(default=None, min_length=1, max_length=120)
    required_capability: ModelCapability | None = None
    preferred_model: str | None = Field(default=None, min_length=1, max_length=200)
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
        if requirements.requested_provider is not None:
            requested = self.registry.get(requirements.requested_provider)
            providers = [requested]
            if requirements.allow_fallback:
                providers.extend(item for item in self.registry.list() if item is not requested)

        # Static policy always runs before any provider health or availability operation.
        candidates = [
            provider
            for provider in providers
            if (capability is None or capability in provider.capabilities)
            and (not request.tools or ModelCapability.TOOL_CALLING in provider.capabilities)
            and (
                requirements.provider_allowlist is None
                or provider.name in requirements.provider_allowlist
            )
            and provider.name not in requirements.provider_denylist
            and (requirements.allow_remote or provider.is_local)
            and (
                not hasattr(provider, "free_eligible")
                or provider.free_eligible(
                    requirements.preferred_model or request.model or provider.default_model
                )
            )
        ]
        health = await self.registry.health(candidates)
        candidates = [item for item in candidates if health[item.name].healthy]

        available: list[ModelProvider] = []
        for provider in candidates:
            effective_model = (
                requirements.preferred_model or request.model or provider.default_model
            )
            known = await provider.model_available(effective_model)
            if known is not False:
                available.append(provider)

        if requirements.requested_provider and not requirements.allow_fallback:
            if not available or available[0].name != requirements.requested_provider:
                raise UnknownProviderError(
                    "explicitly requested provider is not eligible",
                    provider=requirements.requested_provider,
                )
        if requirements.prefer_local and not requirements.requested_provider:
            available.sort(key=lambda provider: not provider.is_local)
        return available

    async def execute(
        self,
        *,
        request: ModelExecutionRequest,
        requirements: RoutingRequirements,
        budget: TaskBudget,
        pricing: PricingTable | None = None,
        before_dispatch: Callable[[str, str], Awaitable[None]] | None = None,
        after_failure: Callable[[str, str, str], Awaitable[None]] | None = None,
    ) -> ModelExecutionResponse:
        request = ModelExecutionRequest.model_validate(request.model_dump())
        tracker = BudgetTracker(budget, pricing)
        candidates = await self.eligible(request, requirements)
        if not candidates:
            if (
                any(hasattr(p, "free_eligible") for p in self.registry.list())
                and requirements.allow_remote
            ):
                raise QuotaExhaustedError(
                    "no verified free capacity; pause until allowance is verified"
                )
            raise UnknownProviderError("no healthy provider satisfies the routing requirements")
        fallback_offset = 0
        maximum_candidates = 1
        if requirements.requested_provider and requirements.allow_fallback:
            requested_is_eligible = (
                bool(candidates) and candidates[0].name == requirements.requested_provider
            )
            if requested_is_eligible:
                maximum_candidates += requirements.maximum_fallbacks
            else:
                maximum_candidates = requirements.maximum_fallbacks
                fallback_offset = 1
        elif requirements.allow_fallback:
            maximum_candidates += requirements.maximum_fallbacks
        selected_candidates = candidates[:maximum_candidates]
        if not selected_candidates:
            raise UnknownProviderError("provider routing fallback allowance is exhausted")
        attempted: list[str] = []
        failures: list[str] = []

        for index, provider in enumerate(selected_candidates):
            # A prior candidate may have exhausted this same account bucket.
            # Recheck before checkpointing or spending another request slot.
            if hasattr(provider, "free_eligible") and not provider.free_eligible(
                requirements.preferred_model or request.model or provider.default_model
            ):
                continue
            attempted.append(provider.name)
            effective_model = (
                requirements.preferred_model or request.model or provider.default_model
            )
            routed_request = request.model_copy(update={"model": effective_model})
            dispatched = False

            async def dispatch(provider=provider, routed_request=routed_request):
                nonlocal dispatched
                if before_dispatch is not None:
                    await before_dispatch(provider.name, routed_request.model)
                dispatched = True
                return await provider.execute(routed_request)

            try:
                response = await self.retry.execute(
                    dispatch,
                    request=routed_request,
                    budget=tracker,
                    provider_name=provider.name,
                )
                if response.provider != provider.name:
                    raise MalformedProviderResponseError(
                        "provider response identity does not match the selected provider",
                        provider=provider.name,
                        model=effective_model,
                        task_id=request.task_id,
                        correlation_id=request.correlation_id,
                    )
                validate_tool_response(response, routed_request)
                response.estimated_cost_usd = tracker.record(response)
                if not provider.is_local and hasattr(provider, "free_eligible"):
                    response.estimated_cost_usd = 0
                response.routing_metadata = {
                    "selected_provider": provider.name,
                    "selected_model": response.model,
                    "is_local": provider.is_local,
                    "fallback_count": index + fallback_offset,
                    "attempted_providers": attempted.copy(),
                    "failure_categories": failures.copy(),
                }
                logger.info(
                    "model request completed provider=%s model=%s latency_ms=%.3f "
                    "task_id=%s correlation_id=%s",
                    provider.name,
                    response.model,
                    response.latency_ms,
                    request.task_id,
                    request.correlation_id,
                )
                return response
            except ModelProviderError as exc:
                failures.append(exc.category.value)
                if after_failure is not None and dispatched:
                    await after_failure(provider.name, effective_model, exc.category.value)
                logger.info(
                    "model request failed provider=%s model=%s category=%s "
                    "task_id=%s correlation_id=%s",
                    provider.name,
                    effective_model,
                    exc.category.value,
                    request.task_id,
                    request.correlation_id,
                )
                if isinstance(
                    exc,
                    (
                        AuthenticationError,
                        InvalidModelRequestError,
                        MalformedProviderResponseError,
                        BudgetExceededError,
                        ProviderExecutionDisabledError,
                    ),
                ):
                    raise
                if isinstance(exc, QuotaExhaustedError) and not requirements.allow_quota_fallback:
                    raise
                if not requirements.allow_fallback or index == len(selected_candidates) - 1:
                    raise
        if requirements.allow_remote:
            raise QuotaExhaustedError("verified free provider routing exhausted; pause")
        raise UnknownProviderError("provider routing exhausted without a result")
