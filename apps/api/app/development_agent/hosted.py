"""Text-only development proposals under current Campaign D account authority."""

from hashlib import sha256

from pydantic import SecretStr

from app.core.config import Settings
from app.model_providers.contracts import ModelCapability
from app.model_providers.errors import ModelProviderError
from app.model_providers.free_capacity import require_allowance
from app.model_providers.free_endpoints import compatible_endpoint
from app.model_providers.openai_compatible import HealthCheckStrategy, OpenAICompatibleProvider
from app.model_providers.registry import ProviderRegistry
from app.model_providers.retry import RetryExecutor, RetryPolicy
from app.model_providers.router import ModelRouter
from app.self_build.policy import digest

REQUIRED = frozenset({ModelCapability.CODE_GENERATION, ModelCapability.STRUCTURED_OUTPUT})


def eligible(settings, provider, application_settings):
    if (
        not settings.enabled
        or settings.provider != "verified"
        or not isinstance(application_settings, Settings)
        or not application_settings.verified_cloud_execution_allowed
        or type(provider) is not OpenAICompatibleProvider
        or provider.is_local
        or provider.execution_mode != "free_only"
        or provider.name != settings.hosted_provider
        or provider.free_allowance is None
        or provider.free_capacity is None
        or not isinstance(provider.api_key, SecretStr)
        or not REQUIRED <= provider.capabilities
    ):
        return False
    grant = provider.free_allowance
    try:
        if (
            grant not in application_settings.parsed_free_allowances()
            or provider.base_url != compatible_endpoint(grant)
            or not {item.value for item in REQUIRED} <= grant.capabilities
            or sha256(provider.api_key.get_secret_value().encode()).hexdigest()
            != grant.credential_fingerprint
            or (
                provider._client is not None and str(provider._client.base_url) != provider.base_url
            )
        ):
            return False
        require_allowance(grant, settings.model)
    except (ModelProviderError, ValueError):
        return False
    return True


def build_verified_router(settings, existing, application_settings):
    if not isinstance(existing, ModelRouter):
        return None
    providers = [item for item in existing.registry.list() if item.name == settings.hosted_provider]
    if len(providers) != 1 or not eligible(settings, providers[0], application_settings):
        return None
    original = providers[0]
    selected = OpenAICompatibleProvider(
        name=original.name,
        base_url=original.base_url,
        api_key=original.api_key,
        default_model=settings.model,
        timeout_seconds=min(original.timeout_seconds, settings.timeout_seconds),
        capabilities=REQUIRED,
        health_strategy=HealthCheckStrategy.CONFIGURATION,
        execution_mode="free_only",
        client=original._client,
        free_allowance=original.free_allowance,
        free_capacity=original.free_capacity,
        configuration_guard=original.configuration_guard,
    )
    return ModelRouter(ProviderRegistry([selected]), RetryExecutor(RetryPolicy(maximum_attempts=1)))


def verified_router_enabled(settings, router, application_settings):
    if not isinstance(router, ModelRouter) or router.retry.policy.maximum_attempts != 1:
        return False
    providers = router.registry.list()
    return (
        len(providers) == 1
        and eligible(settings, providers[0], application_settings)
        and providers[0].default_model == settings.model
        and providers[0].capabilities == REQUIRED
    )


def route_digest(provider):
    grant = provider.free_allowance.model_dump(mode="json")
    grant["models"] = sorted(grant["models"])
    grant["capabilities"] = sorted(grant["capabilities"])
    return digest(
        {
            "provider": provider.name,
            "endpoint": provider.base_url,
            "model": provider.default_model,
            "capabilities": sorted(item.value for item in provider.capabilities),
            "allowance": grant,
        }
    )
