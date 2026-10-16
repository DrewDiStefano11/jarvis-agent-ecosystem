"""Quarantined coding configuration. Does not change local worker routing."""

import re
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.model_providers.contracts import ModelCapability
from app.model_providers.router import RoutingRequirements
from app.model_providers.security import redact_secrets


class DevelopmentProviderSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="JARVIS_DEVELOPMENT_",
        env_file=None,
        extra="ignore",
        hide_input_in_errors=True,
    )
    enabled: bool = False
    provider: Literal["codex_app_server", "local", "openai"] = "codex_app_server"
    model: str = Field(default="", max_length=200)
    local_provider: str = Field(default="ollama", pattern=r"^[A-Za-z0-9_.:-]{1,120}$")
    timeout_seconds: float = Field(default=120, ge=1, le=300, allow_inf_nan=False)

    @model_validator(mode="after")
    def explicit_configuration(self):
        if self.enabled and (
            not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", self.model)
            or redact_secrets(self.model) != self.model
        ):
            raise ValueError("DEVELOPMENT_PROVIDER_CONFIGURATION_INVALID")
        return self


def build_development_router(settings, local_router):
    """Quarantined: no credential reads, provider construction or live routing.

    Campaign D must establish independently verified included-only subscription
    use, purchased-credit exclusion and recharge safeguards before integration.
    Configuration and account/model text cannot establish those proofs.
    """
    return None


def development_routing(settings):
    return RoutingRequirements(
        requested_provider="openai-development"
        if settings.provider == "openai"
        else "codex-app-server"
        if settings.provider == "codex_app_server"
        else settings.local_provider,
        required_capability=ModelCapability.CODE_GENERATION,
        preferred_model=settings.model,
        prefer_local=settings.provider == "local",
        allow_remote=False,
        allow_fallback=False,
    )
