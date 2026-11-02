"""Read native profiles without granting activation or exporting credential values."""

import json
import os

from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings
from app.core.errors import DomainError
from app.provider_configuration.repository import ProviderConfigurationRepository


class ProviderConfigurationSnapshot:
    def __init__(self, sessions, settings):
        self.base_settings = settings
        self.repository = ProviderConfigurationRepository(sessions)
        self.profiles = {}
        self.versions = {}
        self.errors = {}
        self.available = True
        grants = {grant.provider: grant for grant in settings.parsed_free_allowances()}
        try:
            with sessions() as session:
                for name in self.repository.names(session):
                    # A persisted override also removes an environmental candidate
                    # when disabled, missing evidence or corrupt. It never falls back
                    # silently to a different credential for this provider name.
                    grants.pop(name, None)
                    self.versions[name] = None
                    try:
                        row, configuration = self.repository.read(session, name)
                    except DomainError:
                        self.errors[name] = "configuration_corrupt"
                        continue
                    self.versions[name] = (row.revision, row.private_digest)
                    self.profiles[name] = configuration
                    if configuration.enabled:
                        grants[name] = configuration.allowance
        except SQLAlchemyError:
            self.available = False
        self.grants = list(grants.values())

    def current(self, name):
        if not self.available:
            return False
        try:
            with self.repository.sessions() as session:
                result = self.repository.read(session, name)
                if result is None:
                    return name not in self.versions
                row, configuration = result
                return configuration.enabled and self.versions.get(name) == (
                    row.revision,
                    row.private_digest,
                )
        except (DomainError, SQLAlchemyError):
            return False

    def resolve(self, grant):
        if grant.provider not in self.versions:
            return os.environ.get(grant.credential_env)
        if not self.current(grant.provider):
            return None
        try:
            return self.repository.resolve_key(grant.provider)
        except DomainError:
            return None

    def settings(self, bootstrap, controls):
        return configuration_settings(bootstrap, controls, self.grants, self.profiles)

    def assignments(self):
        return {
            name: {"": profile.selected_model, **profile.model_assignments}
            for name, profile in self.profiles.items()
            if profile.enabled
        }


def configuration_settings(bootstrap, controls, grants, profiles):
    """Use the identical complete Settings contract before writes and on startup."""
    values = bootstrap.model_dump(by_alias=True)
    values["JARVIS_MODEL_EXECUTION_MODE"] = controls.execution_mode
    values["JARVIS_AUTONOMOUS_WORKER_ENABLED"] = controls.worker_enabled
    values["JARVIS_MODEL_VERIFIED_CLOUD_ENABLED"] = controls.cloud_enabled and bool(grants)
    values["JARVIS_MODEL_FREE_ALLOWANCES_JSON"] = json.dumps(
        [grant.model_dump(mode="json") for grant in grants]
    )
    preferred = [
        item.provider_name
        for item in sorted(profiles.values(), key=lambda item: (item.priority, item.provider_name))
        if item.enabled
    ]
    preferred.extend(
        name.strip()
        for name in bootstrap.model_provider_priority.split(",")
        if name.strip() and name.strip() not in preferred
    )
    values["JARVIS_MODEL_PROVIDER_PRIORITY"] = ",".join(preferred)
    return Settings(_env_file=None, **values)
