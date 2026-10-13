"""Separate opt-in coding configuration. Does not change local worker routing."""

import re
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.development_agent.provider import OpenAIProposalProvider
from app.model_providers.contracts import ModelCapability
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
    provider: Literal["local", "openai"] = "local"
    model: str = Field(default="", max_length=200)
    local_provider: str = Field(default="ollama", pattern=r"^[A-Za-z0-9_.:-]{1,120}$")
    timeout_seconds: float = Field(default=120, ge=1, le=300, allow_inf_nan=False)
    openai_api_key: SecretStr | None = Field(
        default=None,
        repr=False,
        validation_alias=AliasChoices("JARVIS_DEVELOPMENT_OPENAI_API_KEY", "OPENAI_API_KEY"),
    )

    @model_validator(mode="after")
    def explicit_configuration(self):
        if self.enabled and (
            not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", self.model)
            or redact_secrets(self.model) != self.model
            or (
                self.provider == "openai"
                and (self.openai_api_key is None or not self.openai_api_key.get_secret_value())
            )
        ):
            raise ValueError("DEVELOPMENT_PROVIDER_CONFIGURATION_INVALID")
        return self


def build_development_router(settings, local_router):
    """Construct metadata only; no HTTP client, traffic, tools or fallback."""
    if not settings.enabled:
        return None
    if settings.provider == "openai":
        providers = [
            OpenAIProposalProvider(
                api_key=settings.openai_api_key,
                default_model=settings.model,
                enabled=True,
                timeout_seconds=settings.timeout_seconds,
            )
        ]
    else:
        # Keep the existing local adapters' own execution-mode gate.
        providers = [
            provider
            for provider in local_router.registry.list()
            if provider.is_local and provider.name == settings.local_provider
        ]
    return ModelRouter(
        ProviderRegistry(providers),
        RetryExecutor(RetryPolicy(maximum_attempts=1)),
    )


def development_routing(settings):
    return RoutingRequirements(
        requested_provider="openai-development"
        if settings.provider == "openai"
        else settings.local_provider,
        required_capability=ModelCapability.CODE_GENERATION,
        preferred_model=settings.model,
        prefer_local=settings.provider == "local",
        allow_remote=settings.enabled and settings.provider == "openai",
        allow_fallback=False,
    )
