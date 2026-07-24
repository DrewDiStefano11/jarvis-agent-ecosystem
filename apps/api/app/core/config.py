from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.filesystem.config import SandboxConfiguration


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
    checkpoint_every_step: bool = Field(True, alias="JARVIS_CHECKPOINT_EVERY_STEP")
    web_origin: str = Field("http://localhost:5173", alias="WEB_ORIGIN")
    sandbox_root: Path = Field(Path("./data/sandbox"), alias="JARVIS_SANDBOX_ROOT")
    sandbox_maximum_file_size: int = Field(
        10 * 1024 * 1024,
        alias="JARVIS_SANDBOX_MAXIMUM_FILE_SIZE",
        ge=1,
    )
    sandbox_allowed_extensions: str = Field(
        "",
        alias="JARVIS_SANDBOX_ALLOWED_EXTENSIONS",
    )
    sandbox_restricted_directories: str = Field(
        "",
        alias="JARVIS_SANDBOX_RESTRICTED_DIRECTORIES",
    )
    sandbox_temporary_directory: str = Field(
        ".sandbox-tmp",
        alias="JARVIS_SANDBOX_TEMPORARY_DIRECTORY",
    )
    sandbox_read_only: bool = Field(False, alias="JARVIS_SANDBOX_READ_ONLY")

    @field_validator("sandbox_root", mode="before")
    @classmethod
    def validate_sandbox_root(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            raise ValueError("sandbox root must not be empty")
        return value

    @model_validator(mode="after")
    def validate_sandbox_configuration(self) -> Settings:
        self.filesystem_sandbox_configuration()
        return self

    def filesystem_sandbox_configuration(self) -> SandboxConfiguration:
        return SandboxConfiguration.build(
            root=self.sandbox_root,
            maximum_file_size=self.sandbox_maximum_file_size,
            allowed_extensions=self.sandbox_allowed_extensions,
            restricted_directories=self.sandbox_restricted_directories,
            temporary_directory=self.sandbox_temporary_directory,
            read_only=self.sandbox_read_only,
        )

    def ensure_runtime_directory(self) -> None:
        if self.database_url.startswith("sqlite") and ":memory:" not in self.database_url:
            self.data_directory.mkdir(parents=True, exist_ok=True)
