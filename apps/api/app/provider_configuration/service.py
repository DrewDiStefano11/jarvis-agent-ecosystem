"""Fenced operator writes; no model calls, activation or secret-bearing events."""

from datetime import UTC, datetime
from hashlib import sha256

from sqlalchemy import select

from app.agent_runtime.authorization import IdentityRuntimeAuthorizer
from app.core.errors import DomainError
from app.db.models import AuditEventRow, SystemStateRow
from app.model_providers.errors import ModelProviderError
from app.model_providers.free_capacity import require_allowance
from app.models.provider_configuration import (
    ProviderProfileConfiguration,
    ProviderProfileView,
    SaveProviderProfile,
)
from app.provider_configuration.protection import protect, unprotect
from app.provider_configuration.repository import (
    CONFIGURED,
    ModelProviderProfileRow,
    ProviderConfigurationRepository,
    canonical_payload,
    private_digest,
)


class ProviderConfigurationService:
    def __init__(self, app):
        self.app = app
        self.identity = app.state.identity_service
        self.repository = ProviderConfigurationRepository(app.state.repository.session_factory)

    def authorize(self, actor, session):
        if IdentityRuntimeAuthorizer(self.identity).authenticate(actor.actor_id) != actor:
            raise DomainError(
                "MODEL_CONFIGURATION_PERMISSION_DENIED",
                "Model configuration is not authorized.",
                403,
            )
        decision = self.identity.check_permission_resource_access(
            actor.actor_id,
            "models.configure",
            "administrative_function",
            "model_configuration",
            session=session,
        )
        if not decision.allowed:
            raise DomainError(
                "MODEL_CONFIGURATION_PERMISSION_DENIED",
                "Model configuration is not authorized.",
                403,
            )

    def view(self, row, configuration):
        matches = False
        credential_fingerprint = None
        if not row.encrypted_credential:
            state = "credential_required"
        else:
            try:
                key = unprotect(row.encrypted_credential)
            except DomainError:
                state = "credential_unavailable"
            else:
                credential_fingerprint = sha256(key.get_secret_value().encode()).hexdigest()
                grant = configuration.allowance
                if grant is None:
                    state = "evidence_required"
                else:
                    matches = (
                        sha256(key.get_secret_value().encode()).hexdigest()
                        == grant.credential_fingerprint
                    )
                    if not matches:
                        state = "credential_evidence_mismatch"
                    else:
                        try:
                            require_allowance(grant, configuration.selected_model)
                        except ModelProviderError:
                            state = "evidence_expired"
                        else:
                            state = (
                                "disabled"
                                if not configuration.enabled
                                else "configured"
                                if self.app.state.settings.verified_cloud_execution_allowed
                                else "activation_required"
                            )
        return ProviderProfileView(
            **configuration.model_dump(exclude={"allowance"}),
            revision=row.revision,
            credential_configured=bool(row.encrypted_credential),
            credential_evidence_matches=matches,
            credential_fingerprint=credential_fingerprint,
            state="restart_required"
            if state == "configured"
            and not self.app.state.provider_configuration.current(row.provider_name)
            else state,
        )

    def list(self, actor):
        with self.repository.sessions() as session:
            self.authorize(actor, session)
            return [
                self.view(*self.repository.read(session, name))
                for name in self.repository.names(session)
            ]

    def save(self, actor, body: SaveProviderProfile):
        body = SaveProviderProfile.model_validate(body.model_dump())
        configuration = ProviderProfileConfiguration.model_validate(
            body.model_dump(exclude={"api_key", "clear_credential", "expected_revision"})
        )
        with self.app.state.task_leases._write() as session:
            self.authorize(actor, session)
            existing = self.repository.read(session, body.provider_name)
            row = existing[0] if existing else None
            if body.expected_revision != (row.revision if row else 0):
                raise DomainError(
                    "MODEL_CONFIGURATION_CHANGED",
                    "Refresh the current provider configuration before saving.",
                    409,
                )
            names = self.repository.names(session)
            settings = self.app.state.settings
            legacy = settings.parsed_free_allowances()
            if body.provider_name in {
                settings.model_ollama_name,
                settings.model_openai_compatible_name,
            } or any(
                grant.provider == body.provider_name and grant.integration in {"codex", "chatgpt"}
                for grant in legacy
            ):
                raise DomainError(
                    "MODEL_CONFIGURATION_RESERVED_PROVIDER",
                    "This provider name is reserved for an existing integration.",
                    409,
                )
            effective = {
                grant.provider: grant
                for grant in legacy
                if grant.provider not in {*names, body.provider_name}
            }
            profiles = {body.provider_name: configuration}
            for name in names:
                if name != body.provider_name:
                    _, other = self.repository.read(session, name)
                    profiles[name] = other
                    if other.enabled:
                        effective[name] = other.allowance
            if configuration.enabled:
                effective[body.provider_name] = configuration.allowance
            try:
                from app.provider_configuration.bootstrap import ProviderRuntimeControls
                from app.provider_configuration.runtime import configuration_settings

                configuration_settings(
                    self.app.state.provider_configuration.base_settings,
                    ProviderRuntimeControls(
                        _env_file=None,
                        JARVIS_MODEL_EXECUTION_MODE=settings.model_execution_mode,
                        JARVIS_MODEL_VERIFIED_CLOUD_ENABLED=False,
                        JARVIS_AUTONOMOUS_WORKER_ENABLED=False,
                    ),
                    list(effective.values()),
                    profiles,
                )
            except ValueError:
                raise DomainError(
                    "MODEL_CONFIGURATION_ACCOUNT_POLICY",
                    "Provider configuration conflicts with verified account policy.",
                    409,
                ) from None
            if row is None and len(names) >= 20:
                raise DomainError(
                    "MODEL_CONFIGURATION_LIMIT",
                    "At most twenty provider profiles are supported.",
                    409,
                )
            encrypted = row.encrypted_credential if row else None
            if body.clear_credential:
                encrypted = None
            elif body.api_key is not None:
                encrypted = protect(body.api_key)
            if configuration.enabled:
                if not encrypted:
                    raise DomainError(
                        "MODEL_CREDENTIAL_UNAVAILABLE", "A protected credential is required.", 409
                    )
                grant = configuration.allowance
                key = unprotect(encrypted)
                if (
                    sha256(key.get_secret_value().encode()).hexdigest()
                    != grant.credential_fingerprint
                ):
                    raise DomainError(
                        "MODEL_ACCOUNT_EVIDENCE_MISMATCH",
                        "The credential does not match verified account evidence.",
                        409,
                    )
                try:
                    require_allowance(grant, configuration.selected_model)
                except ModelProviderError:
                    raise DomainError(
                        "MODEL_ACCOUNT_EVIDENCE_EXPIRED",
                        "Current nonbillable evidence is required.",
                        409,
                    ) from None
            payload = canonical_payload(configuration)
            revision = body.expected_revision + 1
            fingerprint = private_digest(payload, encrypted)
            self.app.state.task_leases._add_event(
                session,
                CONFIGURED,
                "Operator provider configuration changed",
                actor_identity_id=actor.actor_id,
                payload={
                    "providerName": body.provider_name,
                    "profileRevision": revision,
                    "configurationDigest": fingerprint,
                },
            )
            session.flush()
            state = session.get(SystemStateRow, 1)
            event = session.scalar(
                select(AuditEventRow).where(
                    AuditEventRow.event_session_id == state.event_session_id,
                    AuditEventRow.sequence_number == state.current_sequence_number,
                )
            )
            if row is None:
                row = ModelProviderProfileRow(provider_name=body.provider_name)
                session.add(row)
            row.revision, row.payload, row.encrypted_credential = revision, payload, encrypted
            row.private_digest, row.last_audit_id = fingerprint, event.id
            row.updated_at = datetime.now(UTC)
            session.flush()
            result = self.view(row, configuration)
        self.app.state.repository.refresh_event_cursor()
        return result
