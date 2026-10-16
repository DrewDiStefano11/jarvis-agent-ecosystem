from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.github_publication.readiness import assess
from app.models.github_publication import PullRequestObservation

HEAD = "a" * 40
OLD = "b" * 40
NOW = datetime(2026, 10, 13, tzinfo=UTC)


def observation(**changes):
    payload = dict(
        repository_id=1,
        pr_number=12,
        head_sha=HEAD,
        head_sha_after_collection=HEAD,
        base_sha=OLD,
        approved_base_sha=OLD,
        observed_at=NOW,
        state="open",
        draft=False,
        mergeable=True,
        merge_state="clean",
        complete=True,
        protection_known=True,
        required_approvals=1,
        required_checks=[dict(context="backend", app_id=42)],
        checks=[
            dict(evidence_id="check-1", context="backend", app_id=42, head_sha=HEAD, state="passed")
        ],
        reviews=[
            dict(
                evidence_id="review-1",
                reviewer_id=2,
                head_sha=HEAD,
                state="approved",
                submitted_at=NOW,
                independent=True,
            )
        ],
        findings=[],
    )
    payload.update(changes)
    return PullRequestObservation.model_validate(payload)


def test_conflict_free_pr_can_still_be_blocked_by_merge_policy():
    result = assess(observation(mergeable=True, merge_state="blocked"))
    assert not result.ready_for_manual_merge
    assert "merge_policy_unconfirmed" in result.blockers


def test_complete_current_observation_can_be_ready_only_for_manual_merge():
    result = assess(observation())
    assert result.ready_for_manual_merge
    assert result.head_sha == HEAD
    assert result.automatic_merge_allowed is False


@pytest.mark.parametrize(
    "state", ["queued", "running", "failed", "cancelled", "skipped", "stale", "unknown"]
)
def test_nonpassing_check_never_counts_as_success(state):
    original = observation()
    candidate = original.model_dump(mode="json")
    candidate["checks"][0]["state"] = state
    result = assess(PullRequestObservation.model_validate(candidate))
    assert not result.ready_for_manual_merge
    assert f"required_check_{state}:backend" in result.blockers


def test_changed_head_invalidates_old_success_and_review():
    original = observation().model_dump(mode="json")
    original["checks"][0]["head_sha"] = OLD
    original["reviews"][0]["head_sha"] = OLD
    result = assess(PullRequestObservation.model_validate(original))
    assert not result.ready_for_manual_merge
    assert "required_check_unconfirmed:backend" in result.blockers
    assert "independent_review_missing" in result.blockers
    assert result.stale_evidence_ids == ("check-1", "review-1")


@pytest.mark.parametrize(
    "changes,blocker",
    [
        ({"draft": True}, "pr_is_draft"),
        ({"state": "merged"}, "pr_not_open"),
        ({"state": "closed"}, "pr_not_open"),
        ({"complete": False}, "observation_incomplete"),
        ({"protection_known": False}, "protection_unknown"),
        ({"mergeable": None}, "mergeability_unconfirmed"),
        ({"mergeable": False}, "mergeability_unconfirmed"),
        ({"base_sha": HEAD}, "base_advanced"),
        ({"head_sha_after_collection": OLD}, "head_changed_during_collection"),
    ],
)
def test_incomplete_or_changed_remote_state_blocks_readiness(changes, blocker):
    result = assess(observation(**changes))
    assert not result.ready_for_manual_merge
    assert blocker in result.blockers


def test_wrong_app_cannot_satisfy_protected_check():
    original = observation().model_dump(mode="json")
    original["checks"][0]["app_id"] = 43
    assert (
        "required_check_unconfirmed:backend"
        in assess(PullRequestObservation.model_validate(original)).blockers
    )


def test_multiple_current_attempts_are_ambiguous_not_first_success_wins():
    original = observation().model_dump(mode="json")
    original["checks"].append(
        {**original["checks"][0], "evidence_id": "check-2", "state": "failed"}
    )
    assert (
        "required_check_unconfirmed:backend"
        in assess(PullRequestObservation.model_validate(original)).blockers
    )


def test_author_approval_does_not_count_as_independent():
    original = observation().model_dump(mode="json")
    original["reviews"][0]["independent"] = False
    assert (
        "independent_review_missing"
        in assess(PullRequestObservation.model_validate(original)).blockers
    )


def test_comment_does_not_erase_change_request_and_old_finding_remains_visible():
    original = observation().model_dump(mode="json")
    original["reviews"][0].update(state="changes_requested", head_sha=OLD)
    original["reviews"].append(
        {
            **original["reviews"][0],
            "evidence_id": "review-2",
            "state": "commented",
            "submitted_at": NOW + timedelta(seconds=1),
        }
    )
    original["findings"] = [dict(evidence_id="finding-1", head_sha=OLD, resolved=False)]
    result = assess(PullRequestObservation.model_validate(original))
    assert "changes_requested:review-1" in result.blockers
    assert "unresolved_finding:finding-1" in result.blockers


def test_latest_dismissal_invalidates_approval():
    original = observation().model_dump(mode="json")
    original["reviews"].append(
        {
            **original["reviews"][0],
            "evidence_id": "review-2",
            "state": "dismissed",
            "submitted_at": NOW + timedelta(seconds=1),
        }
    )
    assert (
        "independent_review_missing"
        in assess(PullRequestObservation.model_validate(original)).blockers
    )


def test_conflicting_same_time_reviews_are_not_order_dependent():
    original = observation().model_dump(mode="json")
    original["reviews"].append(
        {**original["reviews"][0], "evidence_id": "review-2", "state": "changes_requested"}
    )
    for reviews in (original["reviews"], list(reversed(original["reviews"]))):
        assert "review_order_ambiguous" in assess(observation(reviews=reviews)).blockers


def test_duplicate_identity_and_naive_timestamp_refuse():
    original = observation().model_dump(mode="json")
    original["checks"].append(original["checks"][0])
    with pytest.raises(ValidationError):
        PullRequestObservation.model_validate(original)
    with pytest.raises(ValidationError):
        observation(observed_at=NOW.replace(tzinfo=None))
