from __future__ import annotations

from app.core.config import Settings
from app.model_providers.budget import ModelPricing, TaskBudget
from app.model_providers.contracts import ModelCapability
from app.model_providers.ollama import OllamaProvider
from app.model_providers.openai_compatible import (
    HealthCheckStrategy,
    OpenAICompatibleProvider,
)
from app.model_providers.registry import ProviderRegistry
from app.model_providers.retry import RetryExecutor, RetryPolicy
from app.model_providers.router import ModelRouter, RoutingRequirements


def build_provider_registry(settings: Settings) -> ProviderRegistry:
    providers = []
    if settings.model_ollama_enabled:
        providers.append(
            OllamaProvider(
                name=settings.model_ollama_name,
                base_url=str(settings.model_ollama_base_url).rstrip("/"),
                default_model=settings.model_ollama_model,
                timeout_seconds=settings.model_ollama_timeout_seconds,
                capabilities=_capabilities(settings.model_ollama_capabilities),
            )
        )
    if settings.model_openai_compatible_enabled:
        assert settings.model_openai_compatible_api_key is not None
        providers.append(
            OpenAICompatibleProvider(
                name=settings.model_openai_compatible_name,
                base_url=str(settings.model_openai_compatible_base_url).rstrip("/"),
                api_key=settings.model_openai_compatible_api_key,
                default_model=settings.model_openai_compatible_model,
                timeout_seconds=settings.model_openai_compatible_timeout_seconds,
                capabilities=_capabilities(settings.model_openai_compatible_capabilities),
                health_strategy=HealthCheckStrategy(
                    settings.model_openai_compatible_health_strategy
                ),
            )
        )
    priority = {
        name.strip(): index
        for index, name in enumerate(settings.model_provider_priority.split(","))
        if name.strip()
    }
    providers.sort(key=lambda provider: priority.get(provider.name, len(priority)))
    return ProviderRegistry(providers)


def build_model_router(settings: Settings) -> ModelRouter:
    policy = RetryPolicy(
        maximum_attempts=settings.model_retry_maximum_attempts,
        initial_backoff_seconds=settings.model_retry_initial_backoff_seconds,
        maximum_backoff_seconds=settings.model_retry_maximum_backoff_seconds,
    )
    return ModelRouter(build_provider_registry(settings), RetryExecutor(policy))


def build_default_task_budget(settings: Settings) -> TaskBudget:
    return TaskBudget(
        maximum_requests=settings.model_default_maximum_requests,
        maximum_total_tokens=settings.model_default_maximum_total_tokens,
        maximum_cost_usd=settings.model_default_maximum_cost_usd,
    )


def build_default_routing_requirements(settings: Settings) -> RoutingRequirements:
    return RoutingRequirements(
        prefer_local=settings.model_prefer_local,
        allow_remote=settings.model_allow_remote,
    )


def build_model_pricing(settings: Settings) -> dict[str, ModelPricing]:
    return {
        model: ModelPricing(**pricing) for model, pricing in settings.parsed_model_pricing().items()
    }


def _capabilities(value: str) -> frozenset[ModelCapability]:
    return frozenset(ModelCapability(item.strip()) for item in value.split(",") if item.strip())
