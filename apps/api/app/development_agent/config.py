"""Default-off proposal routing through explicit local or verified nonbillable providers."""

import re
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.model_providers.contracts import ModelCapability
from app.model_providers.errors import UnknownProviderError
from app.model_providers.http import is_loopback_endpoint
from app.model_providers.ollama import OllamaProvider
from app.model_providers.registry import ProviderRegistry
from app.model_providers.retry import RetryExecutor, RetryPolicy
from app.model_providers.router import ModelRouter, RoutingRequirements
from app.model_providers.security import redact_secrets


class DevelopmentProviderSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="JARVIS_DEVELOPMENT_",
        env_file=None,
        extra="ignore",
        hide_input_in_errors=True,
    )
    enabled: bool = False
    provider: Literal["codex_app_server", "local", "openai", "verified"] = "codex_app_server"
    model: str = Field(default="", max_length=200)
    local_provider: str = Field(default="ollama", pattern=r"^[A-Za-z0-9_.:-]{1,120}$")
    hosted_provider: str = Field(default="", max_length=120)
    timeout_seconds: float = Field(default=120, ge=1, le=300, allow_inf_nan=False)

    @model_validator(mode="after")
    def explicit_configuration(self):
        model_pattern = (
            r"[A-Za-z0-9_.:/@-]{1,200}"
            if self.provider == "verified"
            else r"[A-Za-z0-9_.:-]{1,200}"
        )
        if self.enabled and (
            not re.fullmatch(model_pattern, self.model) or redact_secrets(self.model) != self.model
        ):
            raise ValueError("DEVELOPMENT_PROVIDER_CONFIGURATION_INVALID")
        if (
            self.enabled
            and self.provider == "verified"
            and not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", self.hosted_provider)
        ):
            raise ValueError("DEVELOPMENT_PROVIDER_CONFIGURATION_INVALID")
        return self


def _eligible_local_provider(provider):
    return (
        type(provider) is OllamaProvider
        and provider.is_local is True
        and provider.execution_mode == "local_only"
        and not provider.default_model.casefold().endswith(("-cloud", ":cloud"))
        and is_loopback_endpoint(provider.base_url)
        and {ModelCapability.CODE_GENERATION, ModelCapability.STRUCTURED_OUTPUT}
        <= provider.capabilities
    )


def local_development_router_enabled(settings, router):
    if (
        not getattr(settings, "enabled", False)
        or getattr(settings, "provider", None) != "local"
        or settings.model.casefold().endswith(("-cloud", ":cloud"))
        or not isinstance(router, ModelRouter)
        or router.retry.policy.maximum_attempts != 1
    ):
        return False
    providers = router.registry.list()
    return (
        len(providers) == 1
        and _eligible_local_provider(providers[0])
        and providers[0].name == settings.local_provider
        and providers[0].default_model == settings.model
        and ModelCapability.TOOL_CALLING not in providers[0].capabilities
    )


def build_development_router(settings, local_router, application_settings=None):
    """Create a separate bounded route; generic paid/subscription routes stay disabled."""
    if settings.provider == "verified":
        from app.development_agent.hosted import build_verified_router

        return build_verified_router(settings, local_router, application_settings)
    if (
        not settings.enabled
        or settings.provider != "local"
        or settings.model.casefold().endswith(("-cloud", ":cloud"))
        or not isinstance(local_router, ModelRouter)
    ):
        return None
    try:
        original = local_router.registry.get(settings.local_provider)
    except UnknownProviderError:
        return None
    if not _eligible_local_provider(original):
        return None
    selected = OllamaProvider(
        name=original.name,
        base_url=original.base_url,
        default_model=settings.model,
        timeout_seconds=min(original.timeout_seconds, settings.timeout_seconds),
        capabilities=original.capabilities - {ModelCapability.TOOL_CALLING},
        keep_alive=original.keep_alive,
        execution_mode="local_only",
        client=original._client,
    )
    return ModelRouter(ProviderRegistry([selected]), RetryExecutor(RetryPolicy(maximum_attempts=1)))


def development_router_enabled(settings, router, application_settings=None):
    if settings.provider == "verified":
        from app.development_agent.hosted import verified_router_enabled

        return verified_router_enabled(settings, router, application_settings)
    return local_development_router_enabled(settings, router)


def hosted_route_digest(provider):
    from app.development_agent.hosted import route_digest

    return route_digest(provider)


def development_routing(settings):
    return RoutingRequirements(
        requested_provider="openai-development"
        if settings.provider == "openai"
        else "codex-app-server"
        if settings.provider == "codex_app_server"
        else settings.hosted_provider
        if settings.provider == "verified"
        else settings.local_provider,
        required_capability=ModelCapability.CODE_GENERATION,
        preferred_model=settings.model,
        prefer_local=settings.provider == "local",
        allow_remote=settings.provider == "verified",
        allow_fallback=False,
    )
