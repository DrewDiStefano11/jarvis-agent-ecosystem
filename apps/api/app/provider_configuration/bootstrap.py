"""Read desired flags separately, then validate them with durable native evidence."""

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config import Settings


class ProviderRuntimeControls(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)
    execution_mode: Literal["disabled", "local_only", "free_only"] = Field(
        "disabled", alias="JARVIS_MODEL_EXECUTION_MODE"
    )
    cloud_enabled: bool = Field(False, alias="JARVIS_MODEL_VERIFIED_CLOUD_ENABLED")
    worker_enabled: bool = Field(False, alias="JARVIS_AUTONOMOUS_WORKER_ENABLED")


def bootstrap_settings(database_url=None):
    values = {"JARVIS_DATABASE_URL": database_url} if database_url else {}
    # No provider or worker is constructed from this discovery-only object.
    # The full Settings contract is validated again after migrations/profile load.
    return Settings(
        **values,
        JARVIS_MODEL_EXECUTION_MODE="disabled",
        JARVIS_MODEL_VERIFIED_CLOUD_ENABLED=False,
        JARVIS_AUTONOMOUS_WORKER_ENABLED=False,
    )


def validate_supervisor_settings(environment, database_path):
    """Preflight the same persisted evidence, without creating or migrating a database."""
    if not database_path.is_file():
        return Settings(_env_file=None, **environment)

    import sqlite3

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.provider_configuration.runtime import ProviderConfigurationSnapshot

    controls = ProviderRuntimeControls(_env_file=None, **environment)
    discovery = Settings(
        _env_file=None,
        **{
            **environment,
            "JARVIS_MODEL_EXECUTION_MODE": "disabled",
            "JARVIS_MODEL_VERIFIED_CLOUD_ENABLED": False,
            "JARVIS_AUTONOMOUS_WORKER_ENABLED": False,
        },
    )
    engine = create_engine(
        "sqlite://",
        creator=lambda: sqlite3.connect(database_path.as_uri() + "?mode=ro", uri=True),
    )
    try:
        snapshot = ProviderConfigurationSnapshot(sessionmaker(bind=engine), discovery)
        if not snapshot.available:
            # Old/unmigrated databases retain strict environment-only preflight.
            return Settings(_env_file=None, **environment)
        return snapshot.settings(discovery, controls)
    finally:
        engine.dispose()
