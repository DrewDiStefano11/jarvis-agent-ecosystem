"""Read-only quality, provenance, and security audit for the Jarvis agent/skill catalog.

This audit is strictly read-only and never grants authority, approves entries,
activates identities, or alters database records.
"""

import re
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.catalog.taxonomy import CAPABILITIES
from app.db.models import (
    AgentCapabilityAssignmentRow,
    AgentPermissionAssignmentRow,
    AgentRoleAssignmentRow,
    CatalogActivationRow,
    CatalogEntryRow,
    CatalogRevisionRow,
    CatalogSourceRow,
    IdentityAgentRow,
)
from app.models.catalog import (
    ActivationStateBreakdown,
    ActiveIdentityFinding,
    CapabilityFinding,
    CatalogAuditReport,
    CatalogAuditSummaryCounts,
    CategorizedFindings,
    DuplicateFinding,
    ProvenanceFinding,
    RevisionFinding,
    SecurityWarningFinding,
    StateBreakdown,
)


class CatalogAuditService:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    def audit(self) -> CatalogAuditReport:
        with self.session_factory() as session:
            now = datetime.now(UTC)

            # 1. Total counts
            total_entries = session.scalar(select(func.count(CatalogEntryRow.id))) or 0
            total_sources = session.scalar(select(func.count(CatalogSourceRow.id))) or 0
            total_revisions = session.scalar(select(func.count(CatalogRevisionRow.id))) or 0

            kind_counts = dict(
                session.execute(
                    select(CatalogEntryRow.kind, func.count(CatalogEntryRow.id)).group_by(
                        CatalogEntryRow.kind
                    )
                ).all()
            )
            agents_count = kind_counts.get("agent", 0)
            skills_count = kind_counts.get("skill", 0)
            discoveries_count = kind_counts.get("discovery", 0)

            summary_counts = CatalogAuditSummaryCounts(
                total_entries=total_entries,
                agents=agents_count,
                skills=skills_count,
                discoveries=discoveries_count,
                sources=total_sources,
                revisions=total_revisions,
            )

            # 2. State Breakdown
            # Review status breakdown from current revisions
            review_status_rows = session.execute(
                select(CatalogRevisionRow.review_status, func.count(CatalogRevisionRow.id))
                .join(CatalogEntryRow, CatalogEntryRow.current_revision_id == CatalogRevisionRow.id)
                .group_by(CatalogRevisionRow.review_status)
            ).all()
            review_status = {status: count for status, count in review_status_rows}

            # Trust status: all external untrusted
            trust_status = {"external_untrusted": total_entries} if total_entries > 0 else {}

            # Activation status breakdown
            enabled_entries = (
                session.scalar(
                    select(func.count(CatalogEntryRow.id)).where(CatalogEntryRow.enabled.is_(True))
                )
                or 0
            )
            disabled_entries = (
                session.scalar(
                    select(func.count(CatalogEntryRow.id)).where(CatalogEntryRow.enabled.is_(False))
                )
                or 0
            )

            activated_entries = (
                session.scalar(select(func.count(CatalogActivationRow.entry_id))) or 0
            )

            active_and_enabled = (
                session.scalar(
                    select(func.count(CatalogActivationRow.entry_id))
                    .join(IdentityAgentRow, IdentityAgentRow.id == CatalogActivationRow.identity_id)
                    .where(
                        IdentityAgentRow.lifecycle_state == "active",
                        IdentityAgentRow.is_enabled.is_(True),
                    )
                )
                or 0
            )

            inactive_or_suspended = (
                session.scalar(
                    select(func.count(CatalogActivationRow.entry_id))
                    .join(IdentityAgentRow, IdentityAgentRow.id == CatalogActivationRow.identity_id)
                    .where(
                        (IdentityAgentRow.lifecycle_state != "active")
                        | (IdentityAgentRow.is_enabled.is_(False))
                    )
                )
                or 0
            )

            updates_available = (
                session.scalar(
                    select(func.count(CatalogActivationRow.entry_id))
                    .join(CatalogEntryRow, CatalogEntryRow.id == CatalogActivationRow.entry_id)
                    .where(CatalogActivationRow.revision_id != CatalogEntryRow.current_revision_id)
                )
                or 0
            )

            activation_status = ActivationStateBreakdown(
                activated_entries=activated_entries,
                active_and_enabled_identities=active_and_enabled,
                inactive_or_suspended_identities=inactive_or_suspended,
                updates_available=updates_available,
                enabled_entries=enabled_entries,
                disabled_entries=disabled_entries,
            )

            state_breakdown = StateBreakdown(
                review_status=review_status,
                trust_status=trust_status,
                activation_status=activation_status,
            )

            # 3. Provenance Findings
            provenance_findings: list[ProvenanceFinding] = []

            # Check sources
            sources = session.scalars(select(CatalogSourceRow)).all()
            for source in sources:
                if not re.match(r"^[0-9a-f]{40}$", source.commit):
                    provenance_findings.append(
                        ProvenanceFinding(
                            source_id=source.id,
                            repository=source.repository,
                            commit=source.commit,
                            issue="INVALID_COMMIT_SHA",
                            details="Source commit SHA is not a 40-character hex string.",
                        )
                    )
                if source.license != "MIT":
                    provenance_findings.append(
                        ProvenanceFinding(
                            source_id=source.id,
                            repository=source.repository,
                            commit=source.commit,
                            issue="NON_MIT_LICENSE",
                            details=f"Source license is '{source.license}', expected 'MIT'.",
                        )
                    )
                # Check entry count discrepancy
                rev_count = (
                    session.scalar(
                        select(func.count(func.distinct(CatalogRevisionRow.entry_id))).where(
                            CatalogRevisionRow.source_id == source.id
                        )
                    )
                    or 0
                )
                if rev_count != source.imported_count:
                    provenance_findings.append(
                        ProvenanceFinding(
                            source_id=source.id,
                            repository=source.repository,
                            commit=source.commit,
                            issue="IMPORTED_COUNT_DISCREPANCY",
                            details=f"Source records {source.imported_count} imported items, but {rev_count} distinct entry revisions exist.",
                        )
                    )

            # Check revisions with missing source rows
            revisions_without_source = (
                session.execute(
                    select(CatalogRevisionRow)
                    .outerjoin(
                        CatalogSourceRow, CatalogRevisionRow.source_id == CatalogSourceRow.id
                    )
                    .where(CatalogSourceRow.id.is_(None))
                )
                .scalars()
                .all()
            )
            for rev in revisions_without_source:
                provenance_findings.append(
                    ProvenanceFinding(
                        source_id=rev.source_id,
                        repository="unknown",
                        commit="unknown",
                        issue="MISSING_SOURCE_ROW",
                        details=f"Revision {rev.id} references non-existent source {rev.source_id}.",
                    )
                )

            # 4. Duplicate Findings
            duplicate_findings: list[DuplicateFinding] = []
            entries = session.scalars(select(CatalogEntryRow)).all()
            entry_map = {e.id: e for e in entries}

            for entry in entries:
                if entry.duplicate_of:
                    canonical = entry_map.get(entry.duplicate_of)
                    duplicate_findings.append(
                        DuplicateFinding(
                            entry_id=entry.id,
                            stable_key=entry.stable_key,
                            kind=entry.kind,
                            duplicate_of=entry.duplicate_of,
                            canonical_stable_key=canonical.stable_key if canonical else None,
                            duplicate_key=entry.duplicate_key,
                        )
                    )

            # 5. Capability Findings & Security Warning Findings & Revision Findings
            capability_findings: list[CapabilityFinding] = []
            security_warning_findings: list[SecurityWarningFinding] = []
            revision_findings: list[RevisionFinding] = []

            revisions_by_entry: dict[str, list[CatalogRevisionRow]] = {}
            all_revisions = session.scalars(select(CatalogRevisionRow)).all()
            for rev in all_revisions:
                revisions_by_entry.setdefault(rev.entry_id, []).append(rev)

            activations_by_entry = {
                act.entry_id: act for act in session.scalars(select(CatalogActivationRow)).all()
            }

            for entry in entries:
                revs = revisions_by_entry.get(entry.id, [])
                current_rev = next((r for r in revs if r.id == entry.current_revision_id), None)
                activation = activations_by_entry.get(entry.id)

                if current_rev:
                    normalized = current_rev.normalized or {}
                    unmapped = normalized.get("unmapped_tags", [])
                    caps = normalized.get("capabilities", [])
                    invalid_caps = [c for c in caps if c not in CAPABILITIES]
                    missing_caps = bool(entry.kind == "agent" and not caps)

                    if unmapped or invalid_caps or missing_caps:
                        capability_findings.append(
                            CapabilityFinding(
                                entry_id=entry.id,
                                stable_key=entry.stable_key,
                                kind=entry.kind,
                                unmapped_tags=unmapped,
                                invalid_capabilities=invalid_caps,
                                missing_capabilities=missing_caps,
                            )
                        )

                    warnings = normalized.get("warnings", [])
                    if warnings:
                        security_warning_findings.append(
                            SecurityWarningFinding(
                                entry_id=entry.id,
                                stable_key=entry.stable_key,
                                kind=entry.kind,
                                warnings=warnings,
                            )
                        )

                superseded_count = max(0, len(revs) - 1)
                update_avail = bool(
                    activation and current_rev and activation.revision_id != current_rev.id
                )
                if superseded_count > 0 or update_avail or (activation and current_rev):
                    revision_findings.append(
                        RevisionFinding(
                            entry_id=entry.id,
                            stable_key=entry.stable_key,
                            kind=entry.kind,
                            current_revision_id=entry.current_revision_id or "none",
                            active_revision_id=activation.revision_id if activation else None,
                            update_available=update_avail,
                            superseded_revisions_count=superseded_count,
                        )
                    )

            # 8. Active Identity Findings & Recorded Authorization State
            active_identity_findings: list[ActiveIdentityFinding] = []

            activations_with_identities = session.execute(
                select(CatalogActivationRow, CatalogEntryRow, IdentityAgentRow)
                .join(CatalogEntryRow, CatalogEntryRow.id == CatalogActivationRow.entry_id)
                .join(IdentityAgentRow, IdentityAgentRow.id == CatalogActivationRow.identity_id)
            ).all()

            for _act, entry, identity in activations_with_identities:
                cap_count = (
                    session.scalar(
                        select(func.count(AgentCapabilityAssignmentRow.id)).where(
                            AgentCapabilityAssignmentRow.agent_id == identity.id,
                            AgentCapabilityAssignmentRow.revoked_at.is_(None),
                        )
                    )
                    or 0
                )

                perm_count = (
                    session.scalar(
                        select(func.count(AgentPermissionAssignmentRow.id)).where(
                            AgentPermissionAssignmentRow.agent_id == identity.id,
                            AgentPermissionAssignmentRow.revoked_at.is_(None),
                        )
                    )
                    or 0
                )

                role_count = (
                    session.scalar(
                        select(func.count(AgentRoleAssignmentRow.id)).where(
                            AgentRoleAssignmentRow.agent_id == identity.id,
                            AgentRoleAssignmentRow.revoked_at.is_(None),
                        )
                    )
                    or 0
                )

                unsafe_reasons = []
                if identity.is_system_agent:
                    unsafe_reasons.append("Catalog identity is marked as a system agent.")
                if identity.agent_type != "specialist":
                    unsafe_reasons.append(
                        f"Catalog identity has agent_type '{identity.agent_type}', expected 'specialist'."
                    )
                if identity.rank_id is not None:
                    unsafe_reasons.append(
                        f"Catalog identity has assigned rank_id '{identity.rank_id}'."
                    )
                if perm_count > 0:
                    unsafe_reasons.append(
                        f"Catalog identity has {perm_count} active permission assignment(s)."
                    )
                if role_count > 0:
                    unsafe_reasons.append(
                        f"Catalog identity has {role_count} active role assignment(s)."
                    )

                active_identity_findings.append(
                    ActiveIdentityFinding(
                        entry_id=entry.id,
                        stable_key=entry.stable_key,
                        identity_id=identity.id,
                        display_name=identity.display_name,
                        role=identity.display_name,
                        lifecycle_state=identity.lifecycle_state,
                        operational_status=identity.operational_status,
                        is_enabled=identity.is_enabled,
                        is_system_agent=identity.is_system_agent,
                        agent_type=identity.agent_type,
                        rank_id=identity.rank_id,
                        capability_count=cap_count,
                        permission_count=perm_count,
                        role_assignment_count=role_count,
                        confirmed_unsafe=bool(unsafe_reasons),
                        unsafe_reasons=unsafe_reasons,
                    )
                )

            # 9. Categorize Findings
            confirmed_unsafe: list[str] = []
            informational_warnings: list[str] = []

            for pf in provenance_findings:
                if pf.issue in ("MISSING_SOURCE_ROW", "INVALID_COMMIT_SHA"):
                    confirmed_unsafe.append(f"Provenance issue [{pf.issue}]: {pf.details}")
                else:
                    informational_warnings.append(f"Provenance note [{pf.issue}]: {pf.details}")

            for aif in active_identity_findings:
                if aif.confirmed_unsafe:
                    for reason in aif.unsafe_reasons:
                        confirmed_unsafe.append(
                            f"Active identity '{aif.stable_key}' ({aif.identity_id}): {reason}"
                        )

            for df in duplicate_findings:
                informational_warnings.append(
                    f"Duplicate entry '{df.stable_key}' is a variant of canonical entry '{df.duplicate_of}' ({df.canonical_stable_key or 'unknown'})."
                )

            for cf in capability_findings:
                if cf.unmapped_tags:
                    informational_warnings.append(
                        f"Entry '{cf.stable_key}' has unmapped tags: {', '.join(cf.unmapped_tags)}."
                    )
                if cf.invalid_capabilities:
                    informational_warnings.append(
                        f"Entry '{cf.stable_key}' has invalid capabilities: {', '.join(cf.invalid_capabilities)}."
                    )
                if cf.missing_capabilities:
                    informational_warnings.append(
                        f"Agent entry '{cf.stable_key}' has no mapped capabilities."
                    )

            for swf in security_warning_findings:
                informational_warnings.append(
                    f"Entry '{swf.stable_key}' contains security warnings: {'; '.join(swf.warnings)}."
                )

            for rf in revision_findings:
                if rf.update_available:
                    informational_warnings.append(
                        f"Active identity for entry '{rf.stable_key}' has a newer revision available."
                    )

            unreviewed_count = review_status.get("unreviewed", 0)
            if unreviewed_count > 0:
                informational_warnings.append(
                    f"{unreviewed_count} catalog entry revision(s) are unreviewed."
                )

            # 10. Advisory Recommendations
            recommendations: list[str] = []
            if confirmed_unsafe:
                recommendations.append(
                    f"CRITICAL: Resolve {len(confirmed_unsafe)} confirmed unsafe condition(s) before proceeding."
                )
            else:
                recommendations.append(
                    "Security: No confirmed unsafe identity authorization or privilege escalation states detected."
                )

            if unreviewed_count > 0:
                recommendations.append(
                    f"Review: Perform operator review on {unreviewed_count} unreviewed catalog entry revision(s)."
                )

            if updates_available > 0:
                recommendations.append(
                    f"Maintenance: Promote {updates_available} active identity/identities to their latest reviewed revision."
                )

            if duplicate_findings:
                recommendations.append(
                    f"Catalog hygiene: {len(duplicate_findings)} duplicate variant(s) detected. Ensure canonical entries are favored."
                )

            if capability_findings:
                recommendations.append(
                    f"Taxonomy: Inspect {len(capability_findings)} entry/entries with unmapped or missing capability tags."
                )

            if security_warning_findings:
                recommendations.append(
                    f"Audit: Review {len(security_warning_findings)} imported definition(s) containing parser security warnings."
                )

            categorized = CategorizedFindings(
                confirmed_unsafe=confirmed_unsafe,
                informational_warnings=informational_warnings,
            )

            return CatalogAuditReport(
                audit_timestamp=now,
                summary_counts=summary_counts,
                state_breakdown=state_breakdown,
                provenance_findings=provenance_findings,
                duplicate_findings=duplicate_findings,
                capability_findings=capability_findings,
                security_warning_findings=security_warning_findings,
                revision_findings=revision_findings,
                active_identity_findings=active_identity_findings,
                categorized_findings=categorized,
                recommendations=recommendations,
            )


def format_human_summary(report: CatalogAuditReport) -> str:
    lines = [
        "=== JARVIS AGENT & SKILL CATALOG AUDIT REPORT ===",
        f"Timestamp: {report.audit_timestamp.isoformat()}",
        "",
        "--- SUMMARY COUNTS ---",
        f"Total Entries:   {report.summary_counts.total_entries}",
        f"  Agents:        {report.summary_counts.agents}",
        f"  Skills:        {report.summary_counts.skills}",
        f"  Discoveries:   {report.summary_counts.discoveries}",
        f"Total Sources:   {report.summary_counts.sources}",
        f"Total Revisions: {report.summary_counts.revisions}",
        "",
        "--- STATE BREAKDOWN ---",
        "Review States:",
    ]
    for k, v in report.state_breakdown.review_status.items():
        lines.append(f"  {k}: {v}")
    lines.append("Trust States:")
    for k, v in report.state_breakdown.trust_status.items():
        lines.append(f"  {k}: {v}")

    act = report.state_breakdown.activation_status
    lines.extend(
        [
            "Activation & Identity States:",
            f"  Activated Entries:                {act.activated_entries}",
            f"  Active & Enabled Identities:      {act.active_and_enabled_identities}",
            f"  Inactive / Suspended Identities:  {act.inactive_or_suspended_identities}",
            f"  Updates Available:               {act.updates_available}",
            f"  Enabled Entries:                 {act.enabled_entries}",
            f"  Disabled Entries:                {act.disabled_entries}",
            "",
            "--- CATEGORIZED FINDINGS ---",
            f"Confirmed Unsafe Conditions ({len(report.categorized_findings.confirmed_unsafe)}):",
        ]
    )
    if report.categorized_findings.confirmed_unsafe:
        for item in report.categorized_findings.confirmed_unsafe:
            lines.append(f"  [UNSAFE] {item}")
    else:
        lines.append("  None")

    lines.append(
        f"Informational Warnings ({len(report.categorized_findings.informational_warnings)}):"
    )
    if report.categorized_findings.informational_warnings:
        for item in report.categorized_findings.informational_warnings[:50]:
            lines.append(f"  [WARN] {item}")
        if len(report.categorized_findings.informational_warnings) > 50:
            lines.append(
                f"  ... and {len(report.categorized_findings.informational_warnings) - 50} more informational warnings."
            )
    else:
        lines.append("  None")

    lines.extend(["", "--- RECOMMENDATIONS ---"])
    for rec in report.recommendations:
        lines.append(f"• {rec}")

    return "\n".join(lines)
