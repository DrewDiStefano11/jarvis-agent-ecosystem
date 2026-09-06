"""Import never invokes identity code. Only operator review + activation can."""

import json

import yaml
from sqlalchemy import select

from app.catalog.normalize import PARSER_VERSION, digest, normalize
from app.catalog.repository import CatalogRepository
from app.catalog.taxonomy import CAPABILITIES
from app.core.errors import DomainError
from app.db.models import (
    AgentCapabilityAssignmentRow,
    CatalogActivationRow,
    CatalogEntryRow,
    CatalogRevisionRow,
    CatalogSourceRow,
    IdentityCapabilityRow,
    SystemStateRow,
)
from app.identity.service import IdentityService, now, uid
from app.models.catalog import ImportReport, SourceSnapshot
from app.models.identity import CreateAgentRequest


class CatalogService:
    def __init__(self, sessions):
        self.repository = CatalogRepository(sessions)
        self.identity = IdentityService(sessions)

    def import_snapshot(self, snapshot: SourceSnapshot, dry_run=True):
        snapshot = SourceSnapshot.model_validate(snapshot.model_dump())
        report = ImportReport(discovered=len(snapshot.definitions), dry_run=dry_run)
        parsed = []
        seen_paths = set()
        for raw in sorted(snapshot.definitions, key=lambda item: item.path):
            try:
                if raw.path in seen_paths:
                    raise ValueError("Duplicate source path")
                seen_paths.add(raw.path)
                value = normalize(raw, snapshot.provider)
                parsed.append((raw, value))
                report.valid += 1
                field = {"agent": "agents", "skill": "skills", "discovery": "discoveries"}[raw.kind]
                setattr(report, field, getattr(report, field) + 1)
                if value.warnings and len(report.warnings) < 100:
                    report.warnings.append(f"{raw.path}: {', '.join(value.warnings)}")
            except (ValueError, TypeError, RecursionError, yaml.YAMLError):
                report.invalid += 1
                if len(report.warnings) < 100:
                    report.warnings.append(f"{raw.path}: invalid definition")
        skill_keys: dict[str, list[str]] = {}
        for raw, value in parsed:
            if raw.kind == "skill":
                skill_keys.setdefault(raw.path.split("/skills/")[0], []).append(value.stable_key)
        for raw, value in parsed:
            if raw.kind == "agent":
                value.skill_references = sorted(skill_keys.get(raw.path.split("/agents/")[0], []))[
                    :100
                ]
        snapshot_hash = digest(
            json.dumps(
                sorted((raw.kind, raw.path, digest(raw.text)) for raw in snapshot.definitions)
            )
        )
        source_id = "source-" + digest(snapshot.provider + snapshot.commit)[:40]
        boundary = self.repository.sessions() if dry_run else self.repository.write()
        with boundary as session:
            old_source = session.get(CatalogSourceRow, source_id)
            if old_source and (
                old_source.repository != snapshot.repository
                or old_source.license_text != snapshot.license_text
                or old_source.license != snapshot.license
                or old_source.snapshot_hash != snapshot_hash
            ):
                raise DomainError(
                    "CATALOG_SOURCE_CONFLICT", "Pinned source provenance cannot change.", 409
                )
            if not dry_run and not old_source:
                session.add(
                    CatalogSourceRow(
                        id=source_id,
                        provider=snapshot.provider,
                        repository=snapshot.repository,
                        commit=snapshot.commit,
                        license=snapshot.license,
                        license_text=snapshot.license_text,
                        snapshot_hash=snapshot_hash,
                        imported_count=report.valid,
                    )
                )
                session.flush()
            # Import may inspect up to 10k definitions, but never a full prompt from
            # the existing catalog. Batched indexed lookups avoid per-entry queries.
            existing, duplicates = {}, {}
            for start in range(0, len(parsed), 400):
                batch = parsed[start : start + 400]
                keys = [v.stable_key for _, v in batch]
                existing.update(
                    (row.stable_key, row)
                    for row in session.scalars(
                        select(CatalogEntryRow).where(CatalogEntryRow.stable_key.in_(keys))
                    )
                )
                fingerprints = [v.duplicate_key for _, v in batch]
                for row in session.scalars(
                    select(CatalogEntryRow)
                    .where(
                        CatalogEntryRow.duplicate_key.in_(fingerprints),
                        CatalogEntryRow.duplicate_of.is_(None),
                    )
                    .order_by(CatalogEntryRow.stable_key)
                ):
                    duplicates.setdefault(row.duplicate_key, row.id)
            revision_ids = [
                "revision-" + digest(v.stable_key + source_id + PARSER_VERSION)[:40]
                for _, v in parsed
            ]
            revisions = {}
            for start in range(0, len(revision_ids), 400):
                revisions.update(
                    session.execute(
                        select(CatalogRevisionRow.id, CatalogRevisionRow.source_hash).where(
                            CatalogRevisionRow.id.in_(revision_ids[start : start + 400])
                        )
                    ).all()
                )
            for (raw, value), revision_id in zip(parsed, revision_ids, strict=True):
                row = existing.get(value.stable_key)
                fingerprint = digest(raw.text)
                if revision_id in revisions:
                    if revisions[revision_id] != fingerprint:
                        raise DomainError(
                            "CATALOG_SOURCE_CONFLICT",
                            "A committed source definition changed bytes.",
                            409,
                        )
                    report.unchanged += 1
                    report.duplicates += int(bool(row.duplicate_of))
                    continue
                if row is None:
                    row = CatalogEntryRow(
                        id="catalog-" + digest(value.stable_key)[:40],
                        stable_key=value.stable_key,
                        kind=raw.kind,
                        duplicate_key=value.duplicate_key,
                        duplicate_of=duplicates.get(value.duplicate_key),
                        enabled=False,
                    )
                    report.new += 1
                    duplicates.setdefault(value.duplicate_key, row.id)
                    if not dry_run:
                        session.add(row)
                        session.flush()
                else:
                    report.changed += 1
                report.duplicates += int(bool(row.duplicate_of))
                if dry_run:
                    continue
                session.add(
                    CatalogRevisionRow(
                        id=revision_id,
                        entry_id=row.id,
                        source_id=source_id,
                        source_path=raw.path,
                        source_hash=fingerprint,
                        parser_version=PARSER_VERSION,
                        normalized=value.model_dump(),
                        original_definition=raw.text,
                        review_status="unreviewed",
                    )
                )
                session.flush()
                row.current_revision_id = revision_id
                # Existing activation and enabled state are deliberately preserved.
            if not dry_run and (report.new or report.changed or not old_source):
                self.repository.event(session, "import.completed", source_id, report.model_dump())
        return report

    def review(self, entry_id, request):
        with self.repository.write() as session:
            entry, revision = self._revision(session, entry_id, request.revision_id)
            status = "approved" if request.approved else "rejected"
            if status == "approved":
                source = session.get(CatalogSourceRow, revision.source_id)
                if source.license != "MIT" or entry.kind == "discovery":
                    raise DomainError(
                        "CATALOG_LICENSE_UNAVAILABLE",
                        "Only licensed definitions can be approved; discovery links require independent import.",
                        409,
                    )
            if revision.review_status != status or entry.enabled != request.approved:
                revision.review_status = status
                entry.enabled = request.approved
                if not request.approved:
                    self._disable(session, entry)
                self.repository.event(
                    session,
                    "revision.reviewed",
                    entry_id,
                    {"revision_id": revision.id, "status": status, "reason": request.reason},
                )
        return self.repository.detail(entry_id)

    def _revision(self, session, entry_id, revision_id):
        entry = self.repository.entry(session, entry_id)
        revision = session.get(CatalogRevisionRow, revision_id)
        if (
            not revision
            or revision.entry_id != entry_id
            or entry.current_revision_id != revision_id
        ):
            raise DomainError(
                "CATALOG_REVISION_CONFLICT",
                "Review and activation require the exact current revision.",
                409,
            )
        return entry, revision

    def activate(self, entry_id, request):
        with self.repository.write() as session:
            entry, revision = self._revision(session, entry_id, request.revision_id)
            source = session.get(CatalogSourceRow, revision.source_id)
            if (
                entry.kind != "agent"
                or not entry.enabled
                or revision.review_status != "approved"
                or source.license != "MIT"
                or entry.duplicate_of
            ):
                raise DomainError(
                    "CATALOG_ACTIVATION_DENIED",
                    "An enabled, reviewed, licensed canonical agent is required.",
                    409,
                )
            system = session.get(SystemStateRow, 1)
            if system and system.emergency_stop:
                raise DomainError(
                    "EMERGENCY_STOP_ACTIVE", "Activation is unavailable during emergency stop.", 409
                )
            link = session.get(CatalogActivationRow, entry_id)
            value = revision.normalized
            collision = session.scalar(
                select(CatalogActivationRow.entry_id)
                .join(CatalogRevisionRow, CatalogRevisionRow.id == CatalogActivationRow.revision_id)
                .where(
                    CatalogActivationRow.entry_id != entry_id,
                    CatalogRevisionRow.normalized["role"].as_string() == value["role"],
                )
                .limit(1)
            )
            if collision:
                raise DomainError(
                    "CATALOG_DUPLICATE_IDENTITY",
                    "This normalized role already has a catalog identity. Review its variants instead.",
                    409,
                )
            if link:
                identity = self.identity._agent(session, link.identity_id)
                if identity.lifecycle_state != "active" or not identity.is_enabled:
                    raise DomainError(
                        "CATALOG_IDENTITY_INACTIVE",
                        "Restore this identity through existing lifecycle controls before activation.",
                        409,
                    )
                if link.revision_id == revision.id:
                    return self.repository.summary(entry, revision, source, link, identity)
                # Promotion updates catalog-owned capability assignments only.
                for assignment in session.scalars(
                    select(AgentCapabilityAssignmentRow).where(
                        AgentCapabilityAssignmentRow.agent_id == identity.id,
                        AgentCapabilityAssignmentRow.source == "catalog",
                        AgentCapabilityAssignmentRow.revoked_at.is_(None),
                    )
                ):
                    assignment.revoked_at = now()
                link.revision_id = revision.id
                link.activated_at = now()
            else:
                identity = self.identity.create_agent_in_session(
                    session,
                    CreateAgentRequest(
                        stable_key="catalog." + digest(entry_id)[:32],
                        display_name=value["display_name"],
                        description="Reviewed external specialist; provenance is in the Jarvis catalog.",
                        agent_type="specialist",
                        is_system_agent=False,
                    ),
                )
                self.identity.transition_in_session(session, identity.id, "active")
                link = CatalogActivationRow(
                    entry_id=entry_id, revision_id=revision.id, identity_id=identity.id
                )
                session.add(link)
            for key in value["capabilities"]:
                if key not in CAPABILITIES:
                    raise DomainError(
                        "CATALOG_CAPABILITY_INVALID", "Unknown normalized capability.", 409
                    )
                capability = session.scalar(
                    select(IdentityCapabilityRow).where(IdentityCapabilityRow.stable_key == key)
                )
                if not capability:
                    capability = IdentityCapabilityRow(
                        id=uid("capability"),
                        stable_key=key,
                        display_name=key,
                        description="Deterministic catalog taxonomy",
                        category=key.split(".")[0],
                    )
                    session.add(capability)
                    session.flush()
                if not capability.is_enabled:
                    raise DomainError(
                        "CAPABILITY_DISABLED", "A required capability is disabled.", 409
                    )
                session.add(
                    AgentCapabilityAssignmentRow(
                        id=uid("acap"),
                        agent_id=identity.id,
                        capability_id=capability.id,
                        source="catalog",
                    )
                )
            self.repository.event(
                session,
                "agent.activated",
                entry_id,
                {"revision_id": revision.id, "identity_id": identity.id, "permissions_granted": 0},
            )
        return self.repository.detail(entry_id)

    def _disable(self, session, entry):
        entry.enabled = False
        link = session.get(CatalogActivationRow, entry.id)
        if link:
            identity = self.identity._agent(session, link.identity_id)
            if identity.lifecycle_state == "active":
                self.identity.transition_in_session(session, identity.id, "suspended")

    def deactivate(self, entry_id):
        with self.repository.write() as session:
            entry = self.repository.entry(session, entry_id)
            if entry.enabled:
                self._disable(session, entry)
                self.repository.event(session, "agent.deactivated", entry_id, {"enabled": False})
        return self.repository.detail(entry_id)
