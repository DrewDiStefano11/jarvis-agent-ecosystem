"""Materialized rows cannot roll back past their append-only native history."""

from sqlalchemy import select

from app.db.models import AuditEventRow


def latest_proof(session, event_type, identifier_key, identifier, revision_key, *, kind=None):
    payload = AuditEventRow.payload["payload"]
    query = select(AuditEventRow.id).where(
        AuditEventRow.event_type == event_type,
        payload[identifier_key].as_string() == identifier,
    )
    if kind is not None:
        query = query.where(payload["kind"].as_string() == kind)
    # Revisions remain monotonic across event sessions and clock adjustments.
    return session.scalar(
        query.order_by(payload[revision_key].as_integer().desc(), AuditEventRow.id.desc()).limit(1)
    )
