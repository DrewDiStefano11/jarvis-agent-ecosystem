"""Read-only quality, provenance, and security audit for the Jarvis agent/skill catalog.

This audit is strictly read-only and never grants authority, approves entries,
activates identities, or alters database records.
"""

import re
from datetime import UTC, datetime
from hashlib import sha256

from sqlalchemy import func, or_, select

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
    ResourceAccessPolicyRow,
)
from app.identity.service import IdentityService
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

MAX_FINDINGS = 1000
MAX_METADATA_ITEMS = 100
PARSER_WARNINGS = {
    "authority_fields_ignored",
    "suspicious_instruction_text",
    "capability_review_required",
    "tool_requests_are_not_permissions",
}


def fingerprint(value) -> str:
    return "withheld:" + sha256(str(value).encode("utf-8")).hexdigest()[:16]


def reference(value) -> str:
    # Identifiers only: never include untrusted prose or credential-shaped metadata.
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:/-]{1,160}", value):
        return value
    return fingerprint(value)


def metadata_items(value) -> list:
    return value[:MAX_METADATA_ITEMS] if isinstance(value, list) else [value]


class CatalogAuditService:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    def audit(self) -> CatalogAuditReport:
        with self.session_factory() as session:
            now = datetime.now(UTC)
            metadata_truncated = False

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
                            source_id=reference(source.id),
                            repository=reference(source.repository),
                            commit=reference(source.commit),
                            issue="INVALID_COMMIT_SHA",
                            details="Source commit SHA is not a 40-character hex string.",
                        )
                    )
                if source.license != "MIT":
                    provenance_findings.append(
                        ProvenanceFinding(
                            source_id=reference(source.id),
                            repository=reference(source.repository),
                            commit=reference(source.commit),
                            issue="NON_MIT_LICENSE",
                            details="Source license differs from expected MIT license.",
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
                            source_id=reference(source.id),
                            repository=reference(source.repository),
                            commit=reference(source.commit),
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
                        source_id=reference(rev.source_id),
                        repository="unknown",
                        commit="unknown",
                        issue="MISSING_SOURCE_ROW",
                        details=f"Revision {reference(rev.id)} references non-existent source {reference(rev.source_id)}.",
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
                            entry_id=reference(entry.id),
                            stable_key=reference(entry.stable_key),
                            kind=entry.kind,
                            duplicate_of=reference(entry.duplicate_of),
                            canonical_stable_key=reference(canonical.stable_key)
                            if canonical
                            else None,
                            duplicate_key=reference(entry.duplicate_key),
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
                    normalized = (
                        current_rev.normalized if isinstance(current_rev.normalized, dict) else {}
                    )
                    metadata_truncated |= any(
                        isinstance(normalized.get(key), list)
                        and len(normalized[key]) > MAX_METADATA_ITEMS
                        for key in ("unmapped_tags", "capabilities", "warnings")
                    )
                    unmapped = [
                        fingerprint(value)
                        for value in metadata_items(normalized.get("unmapped_tags", []))
                    ]
                    caps = metadata_items(normalized.get("capabilities", []))
                    invalid_caps = [
                        fingerprint(c)
                        for c in caps
                        if not isinstance(c, str) or c not in CAPABILITIES
                    ]
                    missing_caps = bool(entry.kind == "agent" and not caps)

                    if unmapped or invalid_caps or missing_caps:
                        capability_findings.append(
                            CapabilityFinding(
                                entry_id=reference(entry.id),
                                stable_key=reference(entry.stable_key),
                                kind=entry.kind,
                                unmapped_tags=unmapped,
                                invalid_capabilities=invalid_caps,
                                missing_capabilities=missing_caps,
                            )
                        )

                    warnings = [
                        value
                        if isinstance(value, str) and value in PARSER_WARNINGS
                        else "unrecognized_warning"
                        for value in metadata_items(normalized.get("warnings", []))
                    ]
                    if warnings:
                        security_warning_findings.append(
                            SecurityWarningFinding(
                                entry_id=reference(entry.id),
                                stable_key=reference(entry.stable_key),
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
                            entry_id=reference(entry.id),
                            stable_key=reference(entry.stable_key),
                            kind=entry.kind,
                            current_revision_id=reference(entry.current_revision_id)
                            if entry.current_revision_id
                            else "none",
                            active_revision_id=reference(activation.revision_id)
                            if activation
                            else None,
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

            # Reuse production authorization, including all/agent/role/team/rank
            # policies, role scope, explicit denials, lifecycle and enablement.
            policy_resources = session.execute(
                select(
                    ResourceAccessPolicyRow.resource_type,
                    ResourceAccessPolicyRow.resource_id,
                    ResourceAccessPolicyRow.action,
                )
                .where(
                    ResourceAccessPolicyRow.effect == "allow",
                    ResourceAccessPolicyRow.revoked_at.is_(None),
                    ResourceAccessPolicyRow.starts_at <= now,
                    or_(
                        ResourceAccessPolicyRow.expires_at.is_(None),
                        ResourceAccessPolicyRow.expires_at > now,
                    ),
                )
                .distinct()
                .order_by(
                    ResourceAccessPolicyRow.resource_type,
                    ResourceAccessPolicyRow.resource_id,
                    ResourceAccessPolicyRow.action,
                )
            ).all()
            evaluator = IdentityService(self.session_factory)
            for _act, entry, identity in activations_with_identities:
                resource_count = 0
                for resource_type, resource_id, action in policy_resources:
                    decision = evaluator._check_resource_access(
                        identity.id, resource_type, resource_id, action, session=session
                    )
                    if decision.allowed and any(
                        grant.startswith("policy:") for grant in decision.matched_grants
                    ):
                        resource_count += 1
                cap_count = (
                    session.scalar(
                        select(func.count(AgentCapabilityAssignmentRow.id)).where(
                            AgentCapabilityAssignmentRow.agent_id == identity.id,
                            AgentCapabilityAssignmentRow.revoked_at.is_(None),
                            AgentCapabilityAssignmentRow.starts_at <= now,
                            or_(
                                AgentCapabilityAssignmentRow.expires_at.is_(None),
                                AgentCapabilityAssignmentRow.expires_at > now,
                            ),
                        )
                    )
                    or 0
                )

                perm_count = (
                    session.scalar(
                        select(func.count(AgentPermissionAssignmentRow.id)).where(
                            AgentPermissionAssignmentRow.agent_id == identity.id,
                            AgentPermissionAssignmentRow.revoked_at.is_(None),
                            AgentPermissionAssignmentRow.starts_at <= now,
                            or_(
                                AgentPermissionAssignmentRow.expires_at.is_(None),
                                AgentPermissionAssignmentRow.expires_at > now,
                            ),
                        )
                    )
                    or 0
                )

                role_count = (
                    session.scalar(
                        select(func.count(AgentRoleAssignmentRow.id)).where(
                            AgentRoleAssignmentRow.agent_id == identity.id,
                            AgentRoleAssignmentRow.revoked_at.is_(None),
                            AgentRoleAssignmentRow.starts_at <= now,
                            or_(
                                AgentRoleAssignmentRow.expires_at.is_(None),
                                AgentRoleAssignmentRow.expires_at > now,
                            ),
                        )
                    )
                    or 0
                )

                unsafe_reasons = []
                if resource_count:
                    unsafe_reasons.append(
                        f"Catalog identity has effective policy access to {resource_count} resource/action pair(s)."
                    )
                if identity.is_system_agent:
                    unsafe_reasons.append("Catalog identity is marked as a system agent.")
                if identity.agent_type != "specialist":
                    unsafe_reasons.append(
                        f"Catalog identity has agent_type '{reference(identity.agent_type)}', expected 'specialist'."
                    )
                if identity.rank_id is not None:
                    unsafe_reasons.append(
                        f"Catalog identity has assigned rank_id '{reference(identity.rank_id)}'."
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
                        entry_id=reference(entry.id),
                        stable_key=reference(entry.stable_key),
                        identity_id=reference(identity.id),
                        display_name="Catalog identity",
                        role="Catalog specialist",
                        lifecycle_state=identity.lifecycle_state,
                        operational_status=identity.operational_status,
                        is_enabled=identity.is_enabled,
                        is_system_agent=identity.is_system_agent,
                        agent_type=identity.agent_type,
                        rank_id=reference(identity.rank_id) if identity.rank_id else None,
                        capability_count=cap_count,
                        permission_count=perm_count,
                        role_assignment_count=role_count,
                        effective_resource_access_count=resource_count,
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
                confirmed_unsafe=confirmed_unsafe[:MAX_FINDINGS],
                informational_warnings=informational_warnings[:MAX_FINDINGS],
            )

            finding_groups = [
                provenance_findings,
                duplicate_findings,
                capability_findings,
                security_warning_findings,
                revision_findings,
                active_identity_findings,
                confirmed_unsafe,
                informational_warnings,
            ]
            output_truncated = metadata_truncated or any(
                len(group) > MAX_FINDINGS for group in finding_groups
            )
            return CatalogAuditReport(
                output_truncated=output_truncated,
                audit_timestamp=now,
                summary_counts=summary_counts,
                state_breakdown=state_breakdown,
                provenance_findings=provenance_findings[:MAX_FINDINGS],
                duplicate_findings=duplicate_findings[:MAX_FINDINGS],
                capability_findings=capability_findings[:MAX_FINDINGS],
                security_warning_findings=security_warning_findings[:MAX_FINDINGS],
                revision_findings=revision_findings[:MAX_FINDINGS],
                active_identity_findings=active_identity_findings[:MAX_FINDINGS],
                categorized_findings=categorized,
                recommendations=recommendations,
            )


def format_human_summary(report: CatalogAuditReport) -> str:
    lines = [
        "Output truncated; inspect catalog in smaller groups." if report.output_truncated else "",
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
