"""Bounded provider triage never grants editing, retry or dispatch authority."""

from datetime import timedelta
from types import SimpleNamespace as NS

import pytest

from app.github_publication.correction_triage import classify
from tests.test_github_publication_readiness import HEAD, NOW, OLD, observation


def triage(measured, now=NOW, publisher_ids=(10,)):
    return classify(
        NS(
            observation=measured,
            commit_sha=HEAD,
            task_id="task",
            workspace_id="workspace",
            publication_operation_id="publication",
            link_hash="a" * 64,
            observation_digest="b" * 64,
        ),
        publisher_ids,
        now=now,
    )


@pytest.mark.parametrize("state", ["failed", "cancelled", "skipped", "unknown", "stale"])
def test_check_failure_never_invents_a_product_correction_or_retry(state):
    value = triage(
        observation(
            checks=[
                dict(
                    evidence_id="check-1",
                    context="backend",
                    app_id=42,
                    head_sha=HEAD,
                    state=state,
                )
            ]
        )
    )
    assert value.disposition == "investigation_required"
    assert value.evidence_ids == ("check-1",)
    assert not value.dispatch_authorized and not value.mutation_authority


@pytest.mark.parametrize("state", ["queued", "running"])
def test_waiting_checks_do_not_create_source_changes(state):
    measured = observation(
        checks=[
            dict(
                evidence_id="check-1",
                context="backend",
                app_id=42,
                head_sha=HEAD,
                state=state,
            )
        ]
    )
    assert triage(measured).disposition == "checks_pending"


def review(identifier, state="changes_requested", head=HEAD, time=NOW):
    return dict(
        evidence_id=identifier,
        reviewer_id=2,
        head_sha=head,
        state=state,
        submitted_at=time,
        independent=True,
    )


def test_current_independent_review_produces_bounded_unapproved_candidate():
    value = triage(observation(reviews=[review("review-1")]))
    assert value.disposition == "review_correction_required"
    assert value.evidence_ids == ("review-1",)
    assert not value.dispatch_authorized


def test_superseded_change_request_is_not_replayed():
    value = triage(
        observation(
            reviews=[
                review("review-1"),
                review("review-2", state="approved", time=NOW + timedelta(seconds=1)),
            ]
        )
    )
    assert value.disposition == "no_actionable_evidence"
    assert not value.evidence_ids


def test_old_unresolved_review_is_preserved_and_requires_reconfirmation():
    value = triage(observation(reviews=[review("review-1", head=OLD)]))
    assert value.disposition == "blocked"
    assert value.evidence_ids == ("review-1",)
    assert "review_evidence_requires_reconfirmation" in value.blockers


@pytest.mark.parametrize("age", [301, -1])
def test_stale_or_future_observation_cannot_become_correction_request(age):
    assert triage(observation(), NOW + timedelta(seconds=age)).disposition == "blocked"


def test_overflow_is_explicit_and_never_silently_truncates_findings():
    findings = [dict(evidence_id=f"finding-{i}", head_sha=HEAD, resolved=False) for i in range(33)]
    value = triage(observation(findings=findings))
    assert value.disposition == "blocked"
    assert value.evidence_count == 33
    assert value.evidence_ids == ()
    assert "evidence_limit_exceeded" in value.blockers


@pytest.mark.parametrize("publisher_ids", [(10,), (42,), (999,)])
def test_required_check_app_authority_is_independent_of_publisher_users(publisher_ids):
    measured = observation(
        checks=[
            dict(evidence_id="check-1", context="backend", app_id=42, head_sha=HEAD, state="failed")
        ]
    )
    assert triage(measured, publisher_ids=publisher_ids).disposition == "investigation_required"


def test_unbound_required_check_cannot_invent_trusted_app_authority():
    measured = observation(
        required_checks=[dict(context="backend", app_id=None)],
        checks=[
            dict(evidence_id="check-1", context="backend", app_id=42, head_sha=HEAD, state="failed")
        ],
    )
    assert "check_publisher_unverified" in triage(measured).blockers


def test_unknown_publisher_failure_refuses_automatic_correction():
    value = triage(
        observation(
            checks=[
                dict(
                    evidence_id="check-1",
                    context="backend",
                    app_id=999,
                    head_sha=HEAD,
                    state="failed",
                )
            ]
        ),
        publisher_ids=(999,),
    )
    assert value.disposition == "blocked"
    assert "check_publisher_unverified" in value.blockers


def test_ambiguous_latest_review_order_refuses_candidate():
    value = triage(observation(reviews=[review("review-1"), review("review-2", state="approved")]))
    assert value.disposition == "blocked"
    assert "review_order_ambiguous" in value.blockers


def test_missing_required_check_remains_operator_visible():
    value = triage(observation(checks=[]))
    assert value.disposition == "blocked"
    assert "required_check_evidence_unconfirmed" in value.blockers
