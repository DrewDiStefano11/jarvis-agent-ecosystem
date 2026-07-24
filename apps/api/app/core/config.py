from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

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
    web_origin: str = Field("http://localhost:5173", alias="WEB_ORIGIN")

    def ensure_runtime_directory(self) -> None:
        if self.database_url.startswith("sqlite") and ":memory:" not in self.database_url:
            self.data_directory.mkdir(parents=True, exist_ok=True)
