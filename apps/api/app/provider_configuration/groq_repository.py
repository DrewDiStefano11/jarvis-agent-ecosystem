"""Native transaction/audit-bound acceptance and four-dimensional rate state."""

from datetime import UTC, datetime
from hashlib import sha256

from sqlalchemy import JSON, CheckConstraint, ForeignKey, Integer, String, select
from sqlalchemy.orm import Mapped, mapped_column

from app.core.errors import DomainError
from app.db.base import Base
from app.db.models import AuditEventRow, SystemStateRow
from app.models.groq_acceptance import GroqAcceptanceView
from app.models.groq_capacity import GroqRateState
from app.provider_configuration.proofs import latest_proof
from app.self_build.policy import digest


class GroqAcceptanceRow(Base):
    __tablename__ = "groq_acceptance_runs"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    scope_key: Mapped[str] = mapped_column(String(64), unique=True)
    revision: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON)
    private_digest: Mapped[str] = mapped_column(String(64))
    last_audit_id: Mapped[str] = mapped_column(ForeignKey("audit_events.id"))
    __table_args__ = (CheckConstraint("revision > 0", name="ck_groq_acceptance_revision"),)


class GroqRateStateRow(Base):
    __tablename__ = "groq_rate_states"
    scope_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    acceptance_id: Mapped[str] = mapped_column(ForeignKey("groq_acceptance_runs.id"), unique=True)
    revision: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON)
    private_digest: Mapped[str] = mapped_column(String(64))
    last_audit_id: Mapped[str] = mapped_column(ForeignKey("audit_events.id"))
    __table_args__ = (CheckConstraint("revision > 0", name="ck_groq_rate_revision"),)


def rate_scope(account_id, model):
    # Provider aliases, keys and human bucket labels cannot reset a model's
    # organization-wide history. Groq's published limits are per model.
    return sha256(f"groq\0{account_id}\0{model}".encode()).hexdigest()


class GroqRepository:
    def __init__(self, app):
        self.app = app
        self.sessions = app.state.repository.session_factory

    def _read(self, session, row, kind):
        if row is None:
            return None
        expected = {
            "id": row.id if kind == "acceptance" else row.scope_key,
            "revision": row.revision,
            "digest": row.private_digest,
            "kind": kind,
        }
        event = session.get(AuditEventRow, row.last_audit_id)
        if (
            row.private_digest != digest(row.payload)
            or event is None
            or event.event_type != "model.groq.authority_changed"
            or event.payload.get("payload") != expected
            or row.last_audit_id
            != latest_proof(
                session, "model.groq.authority_changed", "id", expected["id"], "revision", kind=kind
            )
        ):
            raise DomainError(
                "GROQ_AUTHORITY_CORRUPT", "Groq authority failed native integrity checks.", 409
            )
        return row

    def acceptance(self, session, identifier):
        row = self._read(session, session.get(GroqAcceptanceRow, identifier), "acceptance")
        if row is None:
            raise DomainError("GROQ_ACCEPTANCE_NOT_FOUND", "Acceptance request was not found.", 404)
        view = GroqAcceptanceView.model_validate(row.payload)
        if (
            view.id != row.id
            or view.plan.id != row.id
            or digest(view.plan.model_dump(mode="json")) != view.plan_hash
            or rate_scope(view.plan.evidence.account_id, view.plan.evidence.model) != row.scope_key
        ):
            raise DomainError(
                "GROQ_AUTHORITY_CORRUPT", "Acceptance binding failed integrity checks.", 409
            )
        return row, view

    def rate(self, session, scope):
        row = self._read(session, session.get(GroqRateStateRow, scope), "rate")
        if row is None:
            if (
                latest_proof(
                    session, "model.groq.authority_changed", "id", scope, "revision", kind="rate"
                )
                is not None
            ):
                raise DomainError(
                    "GROQ_AUTHORITY_CORRUPT", "Groq rate projection is missing native history.", 409
                )
            raise DomainError(
                "GROQ_CAPACITY_UNINITIALIZED", "A successful approved bootstrap is required.", 409
            )
        _, acceptance = self.acceptance(session, row.acceptance_id)
        if rate_scope(acceptance.plan.evidence.account_id, acceptance.plan.evidence.model) != scope:
            raise DomainError(
                "GROQ_AUTHORITY_CORRUPT", "Rate scope binding failed integrity checks.", 409
            )
        return row, GroqRateState.model_validate(row.payload)

    def write(self, session, row, value, kind, actor_id=None):
        if row.revision:
            self._read(session, row, kind)
        elif (
            latest_proof(
                session,
                "model.groq.authority_changed",
                "id",
                row.id if kind == "acceptance" else row.scope_key,
                "revision",
                kind=kind,
            )
            is not None
        ):
            raise DomainError(
                "GROQ_AUTHORITY_CORRUPT",
                "A new projection cannot replace prior Groq authority.",
                409,
            )
        payload = value.model_dump(mode="json")
        row.payload = payload
        row.revision = (row.revision or 0) + 1
        row.private_digest = digest(payload)
        self.app.state.task_leases._add_event(
            session,
            "model.groq.authority_changed",
            "Native Groq acceptance or rate state changed",
            actor_identity_id=actor_id,
            payload={
                "id": row.id if kind == "acceptance" else row.scope_key,
                "revision": row.revision,
                "digest": row.private_digest,
                "kind": kind,
            },
        )
        session.flush()
        state = session.get(SystemStateRow, 1)
        row.last_audit_id = session.scalar(
            select(AuditEventRow.id).where(
                AuditEventRow.event_session_id == state.event_session_id,
                AuditEventRow.sequence_number == state.current_sequence_number,
            )
        )
        session.add(row)
        session.flush()

    @staticmethod
    def now():
        return datetime.now(UTC)
