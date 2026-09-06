"""Bounded catalog reads and shared durable command boundary."""

from contextlib import contextmanager

from sqlalchemy import func, select, text
from sqlalchemy.orm import defer

from app.core.errors import DomainError
from app.db.models import (
    CatalogActivationRow,
    CatalogEntryRow,
    CatalogRevisionRow,
    CatalogSourceRow,
    IdentityAgentRow,
    OutboxEventRow,
    SystemStateRow,
)
from app.identity.service import IdentityService, now, uid
from app.models.catalog import CatalogDetail, CatalogPage, CatalogSourceView, CatalogSummary
from app.models.domain import EventEnvelope
from app.services.unit_of_work import UnitOfWork


class CatalogRepository:
    def __init__(self, sessions):
        self.sessions = sessions

    @contextmanager
    def write(self):
        with UnitOfWork(self.sessions) as uow:
            session = uow.session
            if session.bind.dialect.name == "sqlite":
                session.execute(text("BEGIN IMMEDIATE"))
            session.scalar(select(SystemStateRow).where(SystemStateRow.id == 1).with_for_update())
            yield session

    @staticmethod
    def event(session, action, target, changes):
        IdentityService._audit(session, f"catalog.{action}", "catalog", target, changes=changes)
        system = session.get(SystemStateRow, 1)
        if system is None:
            raise DomainError(
                "CATALOG_SYSTEM_UNAVAILABLE", "Initialize the Jarvis control plane first.", 409
            )
        system.current_sequence_number += 1
        envelope = EventEnvelope(
            eventId=uid("catalog-event"),
            eventType=f"catalog.{action}",
            timestamp=now(),
            sequenceNumber=system.current_sequence_number,
            eventSessionId=system.event_session_id,
            correlationId=target,
            source="catalog",
            payload=changes,
        ).model_dump(mode="json")
        session.add(
            OutboxEventRow(
                id=envelope["eventId"],
                event_type=envelope["eventType"],
                envelope=envelope,
                correlation_id=target,
                event_session_id=system.event_session_id,
                sequence_number=system.current_sequence_number,
                created_at=now(),
            )
        )

    @staticmethod
    def entry(session, entry_id):
        row = session.get(CatalogEntryRow, entry_id)
        if row is None:
            raise DomainError("CATALOG_NOT_FOUND", "Catalog entry was not found.", 404)
        return row

    @staticmethod
    def summary(entry, rev, source, activation, identity):
        value = rev.normalized
        return CatalogSummary(
            id=entry.id,
            kind=entry.kind,
            stable_key=entry.stable_key,
            display_name=value["display_name"],
            description=value["description"],
            role=value["role"],
            capabilities=value["capabilities"],
            source_repository=source.repository,
            source_commit=source.commit,
            source_path=rev.source_path,
            source_license=source.license,
            revision_id=rev.id,
            review_status=rev.review_status,
            enabled=entry.enabled,
            duplicate_of=entry.duplicate_of,
            identity_id=activation.identity_id if activation else None,
            active_revision_id=activation.revision_id if activation else None,
            update_available=bool(
                activation and activation.revision_id != entry.current_revision_id
            ),
            operational_status=identity.operational_status if identity else None,
            lifecycle_state=identity.lifecycle_state if identity else None,
            runtime_enabled=identity.is_enabled if identity else None,
            warnings=value["warnings"],
        )

    @staticmethod
    def query(active_revision=False):
        return (
            select(
                CatalogEntryRow,
                CatalogRevisionRow,
                CatalogSourceRow,
                CatalogActivationRow,
                IdentityAgentRow,
            )
            .outerjoin(CatalogActivationRow, CatalogEntryRow.id == CatalogActivationRow.entry_id)
            .join(
                CatalogRevisionRow,
                (
                    CatalogActivationRow.revision_id
                    if active_revision
                    else CatalogEntryRow.current_revision_id
                )
                == CatalogRevisionRow.id,
            )
            .join(CatalogSourceRow, CatalogRevisionRow.source_id == CatalogSourceRow.id)
            .outerjoin(IdentityAgentRow, CatalogActivationRow.identity_id == IdentityAgentRow.id)
        )

    def page(self, kind, offset=0, limit=50, active_only=False):
        if not 0 <= offset or not 1 <= limit <= 100:
            raise DomainError(
                "CATALOG_PAGE_INVALID", "Limit must be 1–100 and offset nonnegative.", 422
            )
        with self.sessions() as session:
            query = self.query(active_only).where(CatalogEntryRow.kind == kind)
            if active_only:
                query = query.where(
                    CatalogEntryRow.enabled,
                    IdentityAgentRow.is_enabled,
                    IdentityAgentRow.lifecycle_state == "active",
                )
            rows = session.execute(
                query.options(
                    defer(CatalogRevisionRow.original_definition),
                    defer(CatalogSourceRow.license_text),
                )
                .order_by(CatalogEntryRow.stable_key)
                .offset(offset)
                .limit(limit)
            )
            items = [self.summary(*row) for row in rows]
            total = session.scalar(select(func.count()).select_from(query.subquery()))
            return CatalogPage(items=items, total=total, offset=offset, limit=limit)

    def detail(self, entry_id):
        with self.sessions() as session:
            row = session.execute(self.query().where(CatalogEntryRow.id == entry_id)).first()
            if not row:
                raise DomainError("CATALOG_NOT_FOUND", "Catalog entry was not found.", 404)
            _, revision, source, _, _ = row
            revisions = list(
                session.scalars(
                    select(CatalogRevisionRow.id)
                    .where(CatalogRevisionRow.entry_id == entry_id)
                    .order_by(CatalogRevisionRow.imported_at.desc(), CatalogRevisionRow.id)
                    .limit(100)
                )
            )
            return CatalogDetail(
                **self.summary(*row).model_dump(),
                normalized=revision.normalized,
                original_definition=revision.original_definition,
                source_hash=revision.source_hash,
                parser_version=revision.parser_version,
                imported_at=revision.imported_at,
                license_text=source.license_text,
                revisions=revisions,
            )

    def sources(self, offset=0, limit=50):
        with self.sessions() as session:
            return [
                CatalogSourceView.model_validate(row)
                for row in session.scalars(
                    select(CatalogSourceRow)
                    .options(defer(CatalogSourceRow.license_text))
                    .order_by(CatalogSourceRow.imported_at.desc(), CatalogSourceRow.id)
                    .offset(offset)
                    .limit(limit)
                )
            ]
