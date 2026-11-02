"""Private encrypted profiles bound to their append-only native audit record."""

from datetime import datetime
from hashlib import sha256

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.errors import DomainError
from app.db.base import Base
from app.db.models import AuditEventRow
from app.models.provider_configuration import ProviderProfileConfiguration
from app.provider_configuration.protection import unprotect
from app.self_build.policy import digest

CONFIGURED = "model.provider.configuration_changed"


class ModelProviderProfileRow(Base):
    __tablename__ = "model_provider_profiles"
    provider_name: Mapped[str] = mapped_column(String(120), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON)
    encrypted_credential: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    private_digest: Mapped[str] = mapped_column(String(64))
    last_audit_id: Mapped[str] = mapped_column(ForeignKey("audit_events.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint("revision > 0", name="ck_model_profile_revision"),)


def private_digest(payload, encrypted):
    return digest(
        {
            "configuration": payload,
            "credential_sha256": sha256(encrypted).hexdigest() if encrypted else None,
        }
    )


def canonical_payload(configuration):
    payload = configuration.model_dump(mode="json")
    if payload["allowance"] is not None:
        payload["allowance"]["models"] = sorted(payload["allowance"]["models"])
        payload["allowance"]["capabilities"] = sorted(payload["allowance"]["capabilities"])
    return payload


class ProviderConfigurationRepository:
    def __init__(self, sessions):
        self.sessions = sessions

    def read(self, session, name):
        from app.provider_configuration.proofs import latest_proof

        latest = latest_proof(session, CONFIGURED, "providerName", name, "profileRevision")
        row = session.get(ModelProviderProfileRow, name)
        if row is None:
            if latest is not None:
                raise DomainError(
                    "MODEL_CONFIGURATION_CORRUPT",
                    "Saved provider projection is missing native history.",
                    409,
                )
            return None
        try:
            configuration = ProviderProfileConfiguration.model_validate(row.payload)
            event = session.get(AuditEventRow, row.last_audit_id)
            if (
                configuration.provider_name != name
                or row.revision < 1
                or row.private_digest != private_digest(row.payload, row.encrypted_credential)
                or event is None
                or event.event_type != CONFIGURED
                or row.last_audit_id != latest
                or event.payload["payload"]
                != {
                    "providerName": name,
                    "profileRevision": row.revision,
                    "configurationDigest": row.private_digest,
                }
            ):
                raise ValueError
            return row, configuration
        except (ValueError, TypeError, KeyError):
            raise DomainError(
                "MODEL_CONFIGURATION_CORRUPT",
                "Saved provider configuration failed integrity checks.",
                409,
            ) from None

    def names(self, session):
        names = list(
            session.scalars(
                select(ModelProviderProfileRow.provider_name)
                .order_by(ModelProviderProfileRow.provider_name)
                .limit(21)
            )
        )
        history = session.scalars(
            select(AuditEventRow.payload["payload"]["providerName"].as_string())
            .where(AuditEventRow.event_type == CONFIGURED)
            .distinct()
            .limit(21)
        )
        names = sorted(set(names) | {name for name in history if name is not None})
        if len(names) > 20:
            raise DomainError(
                "MODEL_CONFIGURATION_LIMIT", "Provider configuration exceeds its bound.", 409
            )
        return names

    def resolve_key(self, name):
        """Backend-only credential broker. Never exposed through an HTTP response."""
        with self.sessions() as session:
            result = self.read(session, name)
            if result is None or not result[0].encrypted_credential:
                raise DomainError(
                    "MODEL_CREDENTIAL_UNAVAILABLE", "Provider credential is unavailable.", 503
                )
            return unprotect(result[0].encrypted_credential)
