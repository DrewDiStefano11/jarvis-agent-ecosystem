"""Collect bounded authenticated PR evidence, fenced by repository and remote head."""

from datetime import UTC, datetime
from hashlib import sha256

from pydantic import ValidationError

from app.core.errors import DomainError
from app.github_publication.transport import GitHubReader
from app.models.github_publication import PullRequestObservation

CONCLUSIONS = {
    "success": "passed",
    "failure": "failed",
    "timed_out": "failed",
    "startup_failure": "failed",
    "action_required": "failed",
    "cancelled": "cancelled",
    "skipped": "skipped",
    "neutral": "skipped",
    "stale": "stale",
}
REVIEW_STATES = {
    "APPROVED": "approved",
    "CHANGES_REQUESTED": "changes_requested",
    "COMMENTED": "commented",
    "DISMISSED": "dismissed",
    "PENDING": "pending",
}


def required_policy(protection, rules):
    """Unknown policy requirements block readiness instead of silently disappearing."""
    if not isinstance(protection, dict) or any(
        protection.get(field) is not None and not isinstance(protection[field], dict)
        for field in (
            "required_status_checks",
            "required_pull_request_reviews",
            "required_signatures",
            "required_linear_history",
        )
    ):
        raise ValueError("Invalid protection shape")
    required, blockers, approvals = {}, [], 0

    def check(context, provider):
        # -1 in legacy protection means any provider; zero is not an App ID.
        provider = None if provider in {None, -1} else provider
        required[(context, provider)] = dict(context=context, app_id=provider)

    statuses = protection.get("required_status_checks") or {}
    checks = statuses.get("checks", [])
    for item in checks:
        check(item["context"], item.get("app_id"))
    bound = {item["context"] for item in checks}
    for context in statuses.get("contexts", []):
        if context not in bound:
            check(context, None)
    reviews = protection.get("required_pull_request_reviews") or {}
    approvals = max(approvals, reviews.get("required_approving_review_count", 0))
    if reviews.get("require_code_owner_reviews"):
        blockers.append("code_owner_review_unconfirmed")
    if reviews.get("require_last_push_approval"):
        blockers.append("last_push_review_unconfirmed")
    for rule in rules:
        kind, parameters = rule["type"], rule.get("parameters") or {}
        if kind == "required_status_checks":
            for item in parameters["required_status_checks"]:
                check(item["context"], item.get("integration_id"))
        elif kind == "pull_request":
            approvals = max(approvals, parameters["required_approving_review_count"])
            if parameters.get("require_code_owner_review"):
                blockers.append("code_owner_review_unconfirmed")
            if parameters.get("require_last_push_approval"):
                blockers.append("last_push_review_unconfirmed")
        elif kind in {"deletion", "creation", "non_fast_forward"}:
            # These constrain ref mutation, not merge evidence.
            continue
        else:
            blockers.append("additional_repository_rule_unconfirmed:" + kind)
    if protection.get("required_signatures", {}).get("enabled"):
        blockers.append("commit_signature_unconfirmed")
    if protection.get("required_linear_history", {}).get("enabled"):
        blockers.append("linear_history_unconfirmed")
    return tuple(required.values()), approvals, tuple(sorted(set(blockers)))


def collect(reader: GitHubReader, number: int, approved_base_sha: str, *, publisher_ids):
    """Caller must supply base and publisher identities from persisted authority.

    The result is observation, not permission to publish or merge. No model/caller
    evidence is accepted as an authenticated GitHub response.
    """
    # This set is resolved by the trusted caller from persisted publication
    # authority, never inferred from reviews or supplied by a model. An empty or
    # malformed set cannot establish reviewer independence. Require a bounded
    # concrete tuple rather than accepting an unbounded iterator.
    if (
        not isinstance(publisher_ids, tuple)
        or not 1 <= len(publisher_ids) <= 256
        or any(type(identity) is not int or identity <= 0 for identity in publisher_ids)
        or len(set(publisher_ids)) != len(publisher_ids)
    ):
        raise DomainError(
            "GITHUB_PUBLISHER_AUTHORITY_REQUIRED",
            "Complete persisted numeric publisher identities are required.",
            409,
        )
    reader.repository()

    def verify(pr):
        if (
            pr["number"] != number
            or pr["base"]["repo"]["id"] != reader.target.repository_id
            or pr["head"]["repo"]["id"] != reader.target.repository_id
            or pr["base"]["ref"] != reader.target.base_branch
        ):
            raise ValueError
        reader.target.branch(pr["head"]["ref"])
        return pr["head"]["sha"]

    try:
        initial = reader.pull_request(number)
        head = verify(initial)
        protection_known = True
        try:
            protection = reader.protection()
        except DomainError as error:
            if error.code not in {"GITHUB_RESOURCE_UNAVAILABLE", "GITHUB_ACCESS_DENIED"}:
                raise
            # A 404 alone proves nothing. Only authenticated branch metadata
            # positively reporting no protection can disambiguate absence.
            # Active rules and operator checks still apply independently.
            protection = {}
            protection_known = (
                error.code == "GITHUB_RESOURCE_UNAVAILABLE"
                and reader.base_details()["protected"] is False
            )
        rules = reader.rules()
        required, approvals, policy_blockers = required_policy(protection, rules)
        approvals = max(approvals, reader.target.minimum_approvals)
        required = required + tuple(
            dict(context=context, app_id=None)
            for context in reader.target.mandatory_checks
            if context not in {item["context"] for item in required}
        )

        current_checks = {}
        for item in reader.checks(head):
            provider = item["app"]["id"]
            key = (item["name"], provider)
            state = (
                "queued"
                if item["status"] in {"queued", "waiting", "pending", "requested"}
                else "running"
                if item["status"] == "in_progress"
                else CONCLUSIONS.get(item.get("conclusion"), "unknown")
                if item["status"] == "completed"
                else "unknown"
            )
            candidate = dict(
                evidence_id=f"check-{item['id']}",
                context=item["name"],
                app_id=provider,
                head_sha=item["head_sha"],
                state=state,
            )
            previous = current_checks.get(key)
            if previous is None or item["id"] > previous[0]:
                current_checks[key] = (item["id"], candidate)
        latest_status = {}
        for item in reader.statuses(head):
            key = item["context"]
            candidate = dict(
                evidence_id=f"status-{item['id']}",
                context=key,
                app_id=None,
                head_sha=head,
                state={
                    "success": "passed",
                    "failure": "failed",
                    "error": "failed",
                    "pending": "queued",
                }.get(item["state"], "unknown"),
            )
            previous = latest_status.get(key)
            if previous is None or item["id"] > previous[0]:
                latest_status[key] = (item["id"], candidate)
        reviews = []
        reviewer_permissions = {}
        excluded = set(publisher_ids) | {initial["user"]["id"]}
        for item in reader.reviews(number):
            if item["state"] == "PENDING":
                # An unsubmitted review has no authoritative timestamp or verdict.
                continue
            reviewer_id = item["user"]["id"]
            if reviewer_id not in excluded and reviewer_id not in reviewer_permissions:
                if len(reviewer_permissions) >= 256:
                    raise ValueError
                reviewer_permissions[reviewer_id] = reader.eligible_reviewer(
                    item["user"]["login"], reviewer_id
                )
            reviews.append(
                dict(
                    evidence_id=f"review-{item['id']}",
                    reviewer_id=item["user"]["id"],
                    head_sha=item["commit_id"],
                    state=REVIEW_STATES[item["state"]],
                    submitted_at=item["submitted_at"],
                    independent=reviewer_id not in excluded
                    and reviewer_permissions.get(reviewer_id, False),
                )
            )
        thread_head, threads = reader.review_threads(number)
        findings = []
        for thread in threads:
            comment = thread["comments"]["nodes"][0]
            review = comment["pullRequestReview"]
            findings.append(
                dict(
                    evidence_id="thread-" + sha256(thread["id"].encode()).hexdigest(),
                    head_sha=comment["originalCommit"]["oid"],
                    resolved=thread["isResolved"],
                    review_id=f"review-{review['databaseId']}" if review else None,
                )
            )
        final = reader.pull_request(number)
        final_head = verify(final)
        reader.repository()
        return PullRequestObservation(
            repository_id=reader.target.repository_id,
            pr_number=number,
            head_sha=head,
            head_sha_after_collection=final_head,
            # PR.base.sha can lag the live base branch. Resolve the branch itself
            # after collection; cached PR metadata cannot prove base freshness.
            base_sha=reader.base_ref(),
            approved_base_sha=approved_base_sha,
            observed_at=datetime.now(UTC),
            state="merged" if final["merged"] else final["state"],
            draft=final["draft"],
            mergeable=final["mergeable"],
            merge_state=final.get("mergeable_state")
            if final.get("mergeable_state")
            in {"clean", "blocked", "dirty", "behind", "unstable", "draft"}
            else "unknown",
            complete=thread_head == head,
            protection_known=protection_known,
            policy_blockers=policy_blockers,
            required_approvals=approvals,
            required_checks=required,
            checks=tuple(entry[1] for entry in current_checks.values())
            + tuple(entry[1] for entry in latest_status.values()),
            reviews=tuple(reviews),
            findings=tuple(findings),
        )
    except (AttributeError, KeyError, TypeError, IndexError, ValueError, ValidationError):
        raise DomainError(
            "GITHUB_OBSERVATION_INVALID",
            "GitHub observation failed provenance or completeness checks.",
            502,
        ) from None
