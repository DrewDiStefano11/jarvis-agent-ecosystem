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
    checkpoint_every_step: bool = Field(True, alias="JARVIS_CHECKPOINT_EVERY_STEP")
    web_origin: str = Field("http://localhost:5173", alias="WEB_ORIGIN")

    def ensure_runtime_directory(self) -> None:
        if self.database_url.startswith("sqlite") and ":memory:" not in self.database_url:
            self.data_directory.mkdir(parents=True, exist_ok=True)
