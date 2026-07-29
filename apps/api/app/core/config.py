from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.model_providers.contracts import BUILTIN_ADAPTER_CAPABILITIES
from app.model_providers.http import is_loopback_endpoint


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        hide_input_in_errors=True,
    )

    app_env: str = Field("development", alias="APP_ENV")
    database_url: str = Field("sqlite:///./data/jarvis.db", alias="JARVIS_DATABASE_URL")
    data_directory: Path = Field(Path("./data"), alias="JARVIS_DATA_DIRECTORY")
    sql_echo: bool = Field(False, alias="JARVIS_SQL_ECHO")
    auto_migrate: bool = Field(True, alias="JARVIS_AUTO_MIGRATE")
    simulator_auto_resume: bool = Field(False, alias="JARVIS_SIMULATOR_AUTO_RESUME")
    outbox_poll_interval_ms: int = Field(250, alias="JARVIS_OUTBOX_POLL_INTERVAL_MS", ge=10)
    outbox_max_attempts: int = Field(10, alias="JARVIS_OUTBOX_MAX_ATTEMPTS", ge=1)
    idempotency_lease_seconds: int = Field(30, alias="JARVIS_IDEMPOTENCY_LEASE_SECONDS", ge=1)
    task_lease_seconds: int = Field(30, alias="JARVIS_TASK_LEASE_SECONDS", ge=1, le=3600)
    task_lease_recovery_interval_ms: int = Field(
        1000, alias="JARVIS_TASK_LEASE_RECOVERY_INTERVAL_MS", ge=50, le=60000
    )
    checkpoint_every_step: bool = Field(True, alias="JARVIS_CHECKPOINT_EVERY_STEP")
    context_maximum_sources: int = Field(32, alias="JARVIS_CONTEXT_MAXIMUM_SOURCES", ge=1, le=64)
    context_maximum_tokens: int = Field(
        8192, alias="JARVIS_CONTEXT_MAXIMUM_TOKENS", ge=256, le=65_536
    )
    context_maximum_total_characters: int = Field(
        500_000,
        alias="JARVIS_CONTEXT_MAXIMUM_TOTAL_CHARACTERS",
        ge=1_000,
        le=5_000_000,
    )
    context_cross_project_allowed: bool = Field(False, alias="JARVIS_CONTEXT_CROSS_PROJECT_ALLOWED")
    autonomous_worker_enabled: bool = Field(False, alias="JARVIS_AUTONOMOUS_WORKER_ENABLED")
    autonomous_worker_actor_id: str = Field(
        "", alias="JARVIS_AUTONOMOUS_WORKER_ACTOR_ID", max_length=80
    )
    autonomous_worker_instance_id: str = Field(
        "", alias="JARVIS_AUTONOMOUS_WORKER_INSTANCE_ID", max_length=120
    )
    autonomous_worker_poll_interval_ms: int = Field(
        1000, alias="JARVIS_AUTONOMOUS_WORKER_POLL_INTERVAL_MS", ge=100, le=60_000
    )
    autonomous_worker_max_concurrency: Literal[1] = Field(
        1, alias="JARVIS_AUTONOMOUS_WORKER_MAX_CONCURRENCY"
    )
    autonomous_worker_lease_seconds: int = Field(
        60, alias="JARVIS_AUTONOMOUS_WORKER_LEASE_SECONDS", ge=15, le=3600
    )
    autonomous_worker_heartbeat_interval_seconds: int = Field(
        15,
        alias="JARVIS_AUTONOMOUS_WORKER_HEARTBEAT_INTERVAL_SECONDS",
        ge=1,
        le=300,
    )
    autonomous_worker_max_execution_seconds: int = Field(
        300, alias="JARVIS_AUTONOMOUS_WORKER_MAX_EXECUTION_SECONDS", ge=1, le=3600
    )
    autonomous_worker_max_repair_calls: int = Field(
        1, alias="JARVIS_AUTONOMOUS_WORKER_MAX_REPAIR_CALLS", ge=0, le=1
    )
    model_execution_mode: Literal["disabled", "local_only"] = Field(
        "disabled", alias="JARVIS_MODEL_EXECUTION_MODE"
    )
    model_ollama_enabled: bool = Field(False, alias="JARVIS_MODEL_OLLAMA_ENABLED")
    model_ollama_name: str = Field(
        "ollama", alias="JARVIS_MODEL_OLLAMA_NAME", min_length=1, max_length=120
    )
    model_ollama_base_url: AnyHttpUrl = Field(
        "http://127.0.0.1:11434", alias="JARVIS_MODEL_OLLAMA_BASE_URL"
    )
    model_ollama_model: str = Field(
        "configure-a-model", alias="JARVIS_MODEL_OLLAMA_MODEL", min_length=1
    )
    model_ollama_timeout_seconds: float = Field(
        30, alias="JARVIS_MODEL_OLLAMA_TIMEOUT_SECONDS", gt=0, le=3600
    )
    model_ollama_capabilities: str = Field(
        "chat,text_generation", alias="JARVIS_MODEL_OLLAMA_CAPABILITIES"
    )
    model_openai_compatible_enabled: bool = Field(
        False, alias="JARVIS_MODEL_OPENAI_COMPATIBLE_ENABLED"
    )
    model_openai_compatible_name: str = Field(
        "openai-compatible",
        alias="JARVIS_MODEL_OPENAI_COMPATIBLE_NAME",
        min_length=1,
        max_length=120,
    )
    model_openai_compatible_base_url: AnyHttpUrl = Field(
        "https://example.invalid/v1", alias="JARVIS_MODEL_OPENAI_COMPATIBLE_BASE_URL"
    )
    model_openai_compatible_api_key: SecretStr | None = Field(
        None, alias="JARVIS_MODEL_OPENAI_COMPATIBLE_API_KEY"
    )
    model_openai_compatible_model: str = Field(
        "configure-a-model", alias="JARVIS_MODEL_OPENAI_COMPATIBLE_MODEL", min_length=1
    )
    model_openai_compatible_timeout_seconds: float = Field(
        30, alias="JARVIS_MODEL_OPENAI_COMPATIBLE_TIMEOUT_SECONDS", gt=0, le=3600
    )
    model_openai_compatible_capabilities: str = Field(
        "chat,text_generation", alias="JARVIS_MODEL_OPENAI_COMPATIBLE_CAPABILITIES"
    )
    model_openai_compatible_health_strategy: Literal["models", "root", "configuration"] = Field(
        "models", alias="JARVIS_MODEL_OPENAI_COMPATIBLE_HEALTH_STRATEGY"
    )
    model_provider_priority: str = Field(
        "ollama,openai-compatible", alias="JARVIS_MODEL_PROVIDER_PRIORITY"
    )
    model_allow_remote: bool = Field(False, alias="JARVIS_MODEL_ALLOW_REMOTE")
    model_prefer_local: bool = Field(True, alias="JARVIS_MODEL_PREFER_LOCAL")
    model_retry_maximum_attempts: int = Field(
        2, alias="JARVIS_MODEL_RETRY_MAXIMUM_ATTEMPTS", ge=1, le=10
    )
    model_retry_initial_backoff_seconds: float = Field(
        0.25, alias="JARVIS_MODEL_RETRY_INITIAL_BACKOFF_SECONDS", ge=0, le=60
    )
    model_retry_maximum_backoff_seconds: float = Field(
        5, alias="JARVIS_MODEL_RETRY_MAXIMUM_BACKOFF_SECONDS", ge=0, le=300
    )
    model_default_maximum_requests: int = Field(
        2, alias="JARVIS_MODEL_DEFAULT_MAXIMUM_REQUESTS", ge=1
    )
    model_default_maximum_input_tokens: int | None = Field(
        None, alias="JARVIS_MODEL_DEFAULT_MAXIMUM_INPUT_TOKENS", ge=1
    )
    model_default_maximum_output_tokens: int | None = Field(
        None, alias="JARVIS_MODEL_DEFAULT_MAXIMUM_OUTPUT_TOKENS", ge=1
    )
    model_default_maximum_total_tokens: int | None = Field(
        None, alias="JARVIS_MODEL_DEFAULT_MAXIMUM_TOTAL_TOKENS", ge=1
    )
    model_default_maximum_cost_usd: float | None = Field(
        None, alias="JARVIS_MODEL_DEFAULT_MAXIMUM_COST_USD", gt=0
    )
    model_pricing_json: str = Field("{}", alias="JARVIS_MODEL_PRICING_JSON")
    web_origin: str = Field("http://localhost:5173", alias="WEB_ORIGIN")

    @model_validator(mode="after")
    def validate_model_provider_settings(self) -> Settings:
        if self.autonomous_worker_enabled:
            if self.model_execution_mode != "local_only":
                raise ValueError(
                    "JARVIS_AUTONOMOUS_WORKER_ENABLED requires "
                    "JARVIS_MODEL_EXECUTION_MODE=local_only"
                )
            if not self.autonomous_worker_actor_id.strip():
                raise ValueError(
                    "JARVIS_AUTONOMOUS_WORKER_ACTOR_ID is required when the worker is enabled"
                )
            if not self.autonomous_worker_instance_id.strip():
                raise ValueError(
                    "JARVIS_AUTONOMOUS_WORKER_INSTANCE_ID is required when the worker is enabled"
                )
            if (
                self.autonomous_worker_heartbeat_interval_seconds * 2
                >= self.autonomous_worker_lease_seconds
            ):
                raise ValueError("worker heartbeat interval must be less than half the lease")
        if self.model_openai_compatible_enabled and (
            self.model_openai_compatible_api_key is None
            or not self.model_openai_compatible_api_key.get_secret_value()
        ):
            raise ValueError(
                "JARVIS_MODEL_OPENAI_COMPATIBLE_API_KEY is required when the provider is enabled"
            )
        if self.model_retry_initial_backoff_seconds > self.model_retry_maximum_backoff_seconds:
            raise ValueError("initial model retry backoff cannot exceed maximum backoff")
        if self.model_default_maximum_requests < self.model_retry_maximum_attempts:
            raise ValueError("default request budget must cover configured retry attempts")
        for label, url in (
            ("Ollama", self.model_ollama_base_url),
            ("OpenAI-compatible", self.model_openai_compatible_base_url),
        ):
            if url.username is not None or url.password is not None or url.query or url.fragment:
                raise ValueError(f"{label} base URL cannot contain credentials, query, or fragment")
        if self.model_execution_mode == "local_only":
            for enabled, label, url in (
                (self.model_ollama_enabled, "Ollama", self.model_ollama_base_url),
                (
                    self.model_openai_compatible_enabled,
                    "OpenAI-compatible",
                    self.model_openai_compatible_base_url,
                ),
            ):
                if enabled and not is_loopback_endpoint(str(url)):
                    raise ValueError(f"{label} must use a structurally loopback base URL")
        supported = {capability.value for capability in BUILTIN_ADAPTER_CAPABILITIES}
        for label, value in (
            ("Ollama", self.model_ollama_capabilities),
            ("OpenAI-compatible", self.model_openai_compatible_capabilities),
        ):
            parts = [item.strip() for item in value.split(",") if item.strip()]
            if not parts or any(item != item.lower() for item in parts):
                raise ValueError(f"{label} capabilities must be lowercase enum values")
            if not set(parts) <= supported:
                raise ValueError(f"{label} capabilities contain an unsupported value")
        priority = [
            item.strip() for item in self.model_provider_priority.split(",") if item.strip()
        ]
        if len(priority) != len(set(priority)):
            raise ValueError("model provider priority cannot contain duplicates")
        self.parsed_model_pricing()
        return self

    def parsed_model_pricing(self) -> dict[str, dict[str, dict[str, float]]]:
        try:
            payload = json.loads(self.model_pricing_json)
        except json.JSONDecodeError as exc:
            raise ValueError("JARVIS_MODEL_PRICING_JSON must contain valid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("JARVIS_MODEL_PRICING_JSON must contain an object")
        required = {"input_per_million_usd", "output_per_million_usd"}
        result: dict[str, dict[str, dict[str, float]]] = {}
        for provider, models in payload.items():
            if not isinstance(provider, str) or not provider or not isinstance(models, dict):
                raise ValueError("pricing entries must map provider names to model objects")
            result[provider] = {}
            for model, pricing in models.items():
                if not isinstance(model, str) or not model or not isinstance(pricing, dict):
                    raise ValueError("provider pricing must map model names to rate objects")
                if set(pricing) != required:
                    raise ValueError("model pricing entries require exact input and output rates")
                if any(
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or value < 0
                    or (isinstance(value, float) and not math.isfinite(value))
                    for value in pricing.values()
                ):
                    raise ValueError("model pricing rates must be nonnegative numbers")
                result[provider][model] = {key: float(value) for key, value in pricing.items()}
        return result

    def ensure_runtime_directory(self) -> None:
        if self.database_url.startswith("sqlite") and ":memory:" not in self.database_url:
            self.data_directory.mkdir(parents=True, exist_ok=True)
