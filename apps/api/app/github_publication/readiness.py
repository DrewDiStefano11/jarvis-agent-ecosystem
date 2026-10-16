"""Conservative exact-head evaluation of authenticated collector observations."""

from app.models.github_publication import PullRequestObservation, PullRequestReadiness


def assess(observation: PullRequestObservation) -> PullRequestReadiness:
    blockers = []
    blockers.extend(observation.policy_blockers)
    if observation.state != "open":
        blockers.append("pr_not_open")
    if observation.draft:
        blockers.append("pr_is_draft")
    if not observation.complete:
        blockers.append("observation_incomplete")
    if not observation.protection_known:
        blockers.append("protection_unknown")
    if observation.head_sha_after_collection != observation.head_sha:
        blockers.append("head_changed_during_collection")
    if observation.base_sha != observation.approved_base_sha:
        blockers.append("base_advanced")
    if observation.mergeable is not True:
        blockers.append("mergeability_unconfirmed")
    if observation.merge_state != "clean":
        blockers.append("merge_policy_unconfirmed")

    stale = sorted(
        {
            entry.evidence_id
            for collection in (observation.checks, observation.reviews, observation.findings)
            for entry in collection
            if entry.head_sha != observation.head_sha
        }
    )
    for requirement in observation.required_checks:
        candidates = [
            check
            for check in observation.checks
            if check.head_sha == observation.head_sha
            and check.context == requirement.context
            and (requirement.app_id is None or check.app_id == requirement.app_id)
        ]
        # The collector must select the latest provider attempt. Multiple current
        # candidates remain ambiguous; ordering in an API response is not authority.
        if len(candidates) != 1:
            blockers.append(f"required_check_unconfirmed:{requirement.context}")
        elif candidates[0].state != "passed":
            blockers.append(f"required_check_{candidates[0].state}:{requirement.context}")

    latest = {}
    for review in observation.reviews:
        if review.state in {"pending", "commented"}:
            continue
        previous = latest.get(review.reviewer_id)
        if previous is None or review.submitted_at > previous.submitted_at:
            latest[review.reviewer_id] = review
        elif review.submitted_at == previous.submitted_at and review != previous:
            blockers.append("review_order_ambiguous")
    approvals = 0
    for review in latest.values():
        # Earlier change requests remain blockers until superseded or dismissed;
        # older approvals never count for the current source.
        if review.state == "changes_requested":
            blockers.append(f"changes_requested:{review.evidence_id}")
        if (
            review.state == "approved"
            and review.independent
            and review.head_sha == observation.head_sha
        ):
            approvals += 1
    if approvals < observation.required_approvals:
        blockers.append("independent_review_missing")
    for finding in observation.findings:
        # Old unresolved findings cannot disappear merely because the head moved.
        if not finding.resolved:
            blockers.append(f"unresolved_finding:{finding.evidence_id}")

    return PullRequestReadiness(
        repository_id=observation.repository_id,
        pr_number=observation.pr_number,
        head_sha=observation.head_sha,
        ready_for_manual_merge=not blockers,
        blockers=tuple(sorted(set(blockers))),
        stale_evidence_ids=tuple(stale),
    )
