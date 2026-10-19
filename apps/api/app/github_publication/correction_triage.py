"""Conservative classification of protected provider evidence; no source dispatch."""

from datetime import UTC, datetime

from app.github_publication.readiness import assess
from app.models.publication_correction import PublicationCorrectionTriage
from app.self_build.policy import digest


def classify(source_link, publisher_ids, *, now=None):
    now = datetime.now(UTC) if now is None else now
    observed = source_link.observation
    readiness = assess(observed)
    blockers = []
    age = (now - observed.observed_at).total_seconds()
    if age < 0 or age > 300:
        blockers.append("observation_not_current")
    if not observed.complete or not observed.protection_known:
        blockers.append("observation_policy_incomplete")
    if observed.policy_blockers:
        blockers.append("publication_policy_requires_attention")
    if any(value.startswith("required_check_unconfirmed:") for value in readiness.blockers):
        blockers.append("required_check_evidence_unconfirmed")
    identities = [
        item.evidence_id
        for values in (observed.checks, observed.reviews, observed.findings)
        for item in values
    ]
    if len(identities) != len(set(identities)):
        blockers.append("evidence_identity_ambiguous")
    if observed.state != "open":
        blockers.append("pr_not_open")
    if observed.base_sha != observed.approved_base_sha:
        blockers.append("base_advanced")
    if "review_order_ambiguous" in readiness.blockers:
        blockers.append("review_order_ambiguous")

    reviews = {
        value.split(":", 1)[1]
        for value in readiness.blockers
        if value.startswith("changes_requested:")
    }
    review_refs = {
        review.evidence_id
        for review in observed.reviews
        if review.evidence_id in reviews
        and review.independent
        and review.reviewer_id not in publisher_ids
        and review.head_sha == source_link.commit_sha
    }
    independent_reviews = {
        review.evidence_id
        for review in observed.reviews
        if review.independent
        and review.reviewer_id not in publisher_ids
        and review.head_sha == source_link.commit_sha
        and review.state not in {"pending", "dismissed"}
    }
    findings = {
        item.evidence_id
        for item in observed.findings
        if not item.resolved
        and item.head_sha == source_link.commit_sha
        and item.review_id in independent_reviews
    }
    stale_actionable = reviews - review_refs | {
        item.evidence_id
        for item in observed.findings
        if not item.resolved and item.evidence_id not in findings
    }
    if stale_actionable:
        blockers.append("review_evidence_requires_reconfirmation")
    actionable = review_refs | findings
    finding_refs = {item.evidence_id for item in observed.findings if not item.resolved}
    failed = set()
    waiting = set()
    for check in observed.checks:
        if check.head_sha != source_link.commit_sha:
            continue
        if check.state in {"failed", "cancelled", "skipped", "unknown", "stale"}:
            failed.add(check.evidence_id)
        elif check.state in {"queued", "running"}:
            waiting.add(check.evidence_id)
        if check.evidence_id in failed | waiting:
            # Publisher IDs are GitHub users, not check-run GitHub App IDs.
            # Only an explicitly app-bound protected requirement establishes
            # the provider for this exact context.
            if not any(
                requirement.context == check.context
                and requirement.app_id is not None
                and requirement.app_id == check.app_id
                for requirement in observed.required_checks
            ):
                blockers.append("check_publisher_unverified")
    references = actionable | finding_refs | reviews | failed | waiting
    if len(references) > 32:
        blockers.append("evidence_limit_exceeded")
    # Keep the original full protected observation as the source of overflow;
    # do not silently truncate it into an authorized correction request.
    disposition = (
        "blocked"
        if blockers
        else "investigation_required"
        if failed
        else "review_correction_required"
        if actionable
        else "checks_pending"
        if waiting
        else "no_actionable_evidence"
    )
    payload = dict(
        schema_version="1.0",
        task_id=source_link.task_id,
        workspace_id=source_link.workspace_id,
        publication_operation_id=source_link.publication_operation_id,
        source_link_hash=source_link.link_hash,
        observation_digest=source_link.observation_digest,
        expected_head_sha=source_link.commit_sha,
        disposition=disposition,
        evidence_ids=sorted(references) if len(references) <= 32 else [],
        evidence_count=len(references),
        blockers=sorted(set(blockers)),
        dispatch_authorized=False,
        mutation_authority=False,
    )
    return PublicationCorrectionTriage(**payload, triage_hash=digest(payload))
