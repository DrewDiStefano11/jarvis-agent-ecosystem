"""Write-only credentials and safe operator provider projections."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from app.model_providers.contracts import validate_safe_identifier
from app.models.ai_capacity import FreeAllowance

TextCapability = Literal[
    "chat", "text_generation", "code_generation", "code_editing", "reasoning", "structured_output"
]
HostedIntegration = Literal["groq", "gemini", "openrouter", "cloudflare"]


class ProviderProfileConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    provider_name: str = Field(pattern=r"^[A-Za-z0-9_-]{1,120}$")
    integration: HostedIntegration
    selected_model: str = Field(pattern=r"^[A-Za-z0-9_.:/@-]{1,200}$")
    priority: int = Field(default=50, ge=0, le=100, strict=True)
    model_assignments: dict[TextCapability, str] = Field(default_factory=dict, max_length=6)
    enabled: bool = False
    allowance: FreeAllowance | None = Field(default=None, repr=False)

    _safe_identifiers = field_validator("provider_name", "selected_model")(validate_safe_identifier)

    @model_validator(mode="after")
    def verified_selection(self):
        for model in self.model_assignments.values():
            if not re.fullmatch(r"[A-Za-z0-9_.:/@-]{1,200}", model):
                raise ValueError("MODEL_ASSIGNMENT_INVALID")
            validate_safe_identifier(model)
        if self.enabled and self.allowance is None:
            raise ValueError("MODEL_ACCOUNT_EVIDENCE_REQUIRED")
        if self.allowance is not None and (
            self.allowance.provider != self.provider_name
            or self.allowance.integration != self.integration
            or self.selected_model not in self.allowance.models
            or not set(self.model_assignments) <= self.allowance.capabilities
            or not set(self.model_assignments.values()) <= self.allowance.models
        ):
            raise ValueError("MODEL_ACCOUNT_EVIDENCE_MISMATCH")
        return self


class SaveProviderProfile(ProviderProfileConfiguration):
    api_key: SecretStr | None = Field(default=None, repr=False)
    clear_credential: bool = False
    expected_revision: int = Field(ge=0, le=2147483646, strict=True)

    @field_validator("api_key")
    @classmethod
    def bounded_credential(cls, value):
        if value is not None and not re.fullmatch(
            r"[A-Za-z0-9_.=+/-]{8,4096}", value.get_secret_value()
        ):
            raise ValueError("MODEL_CREDENTIAL_INVALID")
        return value

    @model_validator(mode="after")
    def exclusive_credential_action(self):
        if self.clear_credential and self.api_key is not None:
            raise ValueError("MODEL_CREDENTIAL_ACTION_INVALID")
        return self


class ProviderProfileView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider_name: str
    integration: HostedIntegration
    selected_model: str
    priority: int
    model_assignments: dict[TextCapability, str]
    enabled: bool
    revision: int
    credential_configured: bool
    credential_evidence_matches: bool
    credential_fingerprint: str | None = None
    state: Literal[
        "credential_required",
        "credential_unavailable",
        "evidence_required",
        "evidence_expired",
        "credential_evidence_mismatch",
        "disabled",
        "activation_required",
        "configured",
        "restart_required",
    ]


class ModelOperatorSetupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation: Literal["configure_model_operator"]


class ModelOperatorSetupView(BaseModel):
    actor_id: str


class ModelConfigurationView(BaseModel):
    profiles: list[ProviderProfileView]
    execution_mode: Literal["disabled", "local_only", "free_only"]
    cloud_execution_enabled: bool
    credential_storage: Literal["windows_dpapi", "unavailable"]


class ProviderMetadataRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1, le=2147483647, strict=True)


class ProviderMetadataView(BaseModel):
    provider_name: str
    profile_revision: int
    state: Literal[
        "catalog_confirmed",
        "authentication_failed",
        "rate_limited",
        "unavailable",
        "invalid_catalog",
    ]
    exact_model_available: bool | None = None
    models: list[str] = Field(default_factory=list, max_length=256)
