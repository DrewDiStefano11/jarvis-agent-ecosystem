import copy
import json

import httpx
import pytest
from pydantic import SecretStr

from app.core.errors import DomainError
from app.github_publication.collector import collect
from app.github_publication.readiness import assess
from app.github_publication.transport import GitHubReader, RepositoryTarget

HEAD, OLD = "a" * 40, "b" * 40
TARGET = RepositoryTarget(
    1, "operator", "repository", "main", "codex/jarvis/", mandatory_checks=("backend",)
)
PR = dict(
    number=12,
    base=dict(repo=dict(id=1), ref="main", sha=OLD),
    head=dict(repo=dict(id=1), ref="codex/jarvis/task-12", sha=HEAD),
    user=dict(id=10),
    state="open",
    merged=False,
    draft=False,
    mergeable=True,
    mergeable_state="clean",
)
CHECK = dict(
    id=1, app=dict(id=42), name="backend", head_sha=HEAD, status="completed", conclusion="success"
)
REVIEW = dict(
    id=1,
    user=dict(id=11, login="reviewer"),
    commit_id=HEAD,
    state="APPROVED",
    submitted_at="2026-10-13T00:00:00Z",
)
PROTECTION = dict(
    required_status_checks=dict(checks=[dict(context="backend", app_id=42)], contexts=["backend"]),
    required_pull_request_reviews=dict(required_approving_review_count=1),
)


class Broker:
    def resolve(self):
        return SecretStr("fixture-only-never-real-acceptance")


def client(
    *,
    checks=None,
    reviews=None,
    statuses=None,
    threads=None,
    protection=None,
    rules=None,
    final=None,
    graph=None,
    live_base=OLD,
    reviewer_permission="write",
    protected=True,
):
    pr_calls = 0

    def handle(request):
        nonlocal pr_calls
        assert request.url.host == "api.github.com"
        path = request.url.path
        if path == "/repos/operator/repository":
            payload = dict(id=1, full_name="operator/repository")
        elif path.endswith("/pulls/12"):
            pr_calls += 1
            payload = final if final is not None and pr_calls > 1 else PR
        elif path.endswith("/protection"):
            if protection == "denied":
                return httpx.Response(403, json=dict(message="not readable"))
            if protection == "absent":
                return httpx.Response(404, json=dict(message="not available"))
            payload = protection if protection is not None else PROTECTION
        elif path == "/repos/operator/repository/branches/main":
            payload = dict(name="main", commit=dict(sha=live_base), protected=protected)
        elif "/rules/branches/" in path:
            payload = rules or []
        elif path.endswith("/check-runs"):
            payload = dict(check_runs=checks if checks is not None else [CHECK])
        elif path.endswith("/statuses"):
            payload = statuses or []
        elif path.endswith("/reviews"):
            payload = reviews if reviews is not None else [REVIEW]
        elif path.endswith("/collaborators/reviewer/permission"):
            payload = dict(permission=reviewer_permission, user=dict(id=11, login="reviewer"))
        elif path == "/graphql":
            variables = json.loads(request.content)["variables"]
            assert variables["owner"] == "operator" and variables["number"] == 12
            payload = (
                graph
                if graph is not None
                else dict(
                    data=dict(
                        repository=dict(
                            databaseId=1,
                            pullRequest=dict(
                                headRefOid=HEAD,
                                baseRefName="main",
                                reviewThreads=dict(
                                    pageInfo=dict(hasNextPage=False, endCursor=None),
                                    nodes=threads or [],
                                ),
                            ),
                        )
                    )
                )
            )
        else:
            pytest.fail(f"unexpected path: {path}")
        return httpx.Response(200, json=payload)

    return GitHubReader(TARGET, Broker(), transport=httpx.MockTransport(handle))


def observed(*, publisher_ids=(10,), **changes):
    reader = client(**changes)
    try:
        return collect(reader, 12, OLD, publisher_ids=publisher_ids)
    finally:
        reader.close()


def test_real_adapter_shape_binds_pr_provider_and_current_source():
    observation = observed()
    assert observation.head_sha == HEAD
    assert observation.repository_id == 1
    assert observation.required_checks[0].app_id == 42
    assert observation.complete
    assert assess(observation).ready_for_manual_merge


def test_latest_attempt_failure_supersedes_earlier_success_regardless_of_order():
    failed = {**CHECK, "id": 2, "conclusion": "failure"}
    for checks in ([CHECK, failed], [failed, CHECK]):
        observation = observed(checks=checks)
        assert len(observation.checks) == 1
        assert observation.checks[0].state == "failed"
        assert not assess(observation).ready_for_manual_merge


def test_final_head_drift_is_never_ready():
    final = copy.deepcopy(PR)
    final["head"]["sha"] = OLD
    assert "head_changed_during_collection" in assess(observed(final=final)).blockers


def test_live_base_drift_blocks_even_when_pr_metadata_has_approved_base():
    assert "base_advanced" in assess(observed(live_base=HEAD)).blockers


def test_stale_pr_base_metadata_does_not_replace_live_branch_identity():
    final = copy.deepcopy(PR)
    final["base"]["sha"] = HEAD
    assert assess(observed(final=final)).ready_for_manual_merge


@pytest.mark.parametrize("mutation", ["base", "repo", "head_repo", "branch", "number"])
def test_cross_pr_or_repository_and_branch_mismatch_refuses(mutation):
    final = copy.deepcopy(PR)
    if mutation == "base":
        final["base"]["ref"] = "foreign-base"
    elif mutation == "repo":
        final["base"]["repo"]["id"] = 2
    elif mutation == "head_repo":
        final["head"]["repo"]["id"] = 2
    elif mutation == "branch":
        final["head"]["ref"] = "main"
    else:
        final["number"] = 13
    with pytest.raises(DomainError):
        observed(final=final)


def test_missing_protection_never_means_no_required_checks():
    result = assess(observed(protection="denied"))
    assert "protection_unknown" in result.blockers
    assert not result.ready_for_manual_merge


def test_confirmed_unprotected_branch_still_requires_operator_checks_and_review():
    result = assess(observed(protection="absent", protected=False, checks=[], reviews=[]))
    assert "protection_unknown" not in result.blockers
    assert "required_check_unconfirmed:backend" in result.blockers
    assert "independent_review_missing" in result.blockers


def test_protection_404_without_positive_absence_is_unknown():
    assert "protection_unknown" in assess(observed(protection="absent", protected=True)).blockers


def test_ruleset_requirements_are_combined_with_legacy_protection():
    rules = [
        dict(
            type="required_status_checks",
            parameters=dict(required_status_checks=[dict(context="frontend", integration_id=42)]),
        ),
        dict(type="pull_request", parameters=dict(required_approving_review_count=2)),
        dict(
            type="required_deployments", parameters=dict(required_deployment_environments=["prod"])
        ),
    ]
    result = assess(observed(rules=rules))
    assert "required_check_unconfirmed:frontend" in result.blockers
    assert "independent_review_missing" in result.blockers
    assert "additional_repository_rule_unconfirmed:required_deployments" in result.blockers


def test_author_approval_is_excluded_by_authenticated_pr_author_identity():
    assert (
        "independent_review_missing"
        in assess(observed(reviews=[{**REVIEW, "user": dict(id=10)}])).blockers
    )


def test_outsider_approval_does_not_satisfy_required_independent_review():
    assert "independent_review_missing" in assess(observed(reviewer_permission="read")).blockers


def test_graphql_partial_errors_refuse_instead_of_accepting_partial_threads():
    with pytest.raises(DomainError) as caught:
        observed(graph=dict(errors=[dict(message="private or untrusted")], data={}))
    assert caught.value.code == "GITHUB_REVIEW_EVIDENCE_INVALID"
    assert "untrusted" not in str(caught.value)


def test_thread_findings_retain_original_source_and_resolution():
    thread = dict(
        id="provider-thread-id",
        isResolved=False,
        comments=dict(
            nodes=[
                dict(
                    databaseId=55,
                    originalCommit=dict(oid=OLD),
                    pullRequestReview=dict(databaseId=1),
                )
            ]
        ),
    )
    observation = observed(threads=[thread])
    assert observation.findings[0].head_sha == OLD
    assert observation.findings[0].review_id == "review-1"
    assert any(item.startswith("unresolved_finding:") for item in assess(observation).blockers)
    thread["isResolved"] = True
    assert assess(observed(threads=[thread])).ready_for_manual_merge


def test_unknown_check_conclusion_is_not_success():
    assert not assess(
        observed(checks=[{**CHECK, "conclusion": "new-provider-conclusion"}])
    ).ready_for_manual_merge


def test_publisher_identity_is_required_before_any_provider_read():
    reader = client()
    try:
        with pytest.raises(TypeError, match="publisher_ids"):
            collect(reader, 12, OLD)
    finally:
        reader.close()


@pytest.mark.parametrize(
    "identities", [(), None, [], (True,), (0,), (-1,), ("11",), (11, 11), tuple(range(1, 258))]
)
def test_invalid_publisher_authority_refuses_before_provider_read(identities):
    class Unreadable:
        def repository(self):
            pytest.fail("Invalid authority must refuse before provider access")

    with pytest.raises(DomainError) as caught:
        collect(Unreadable(), 12, OLD, publisher_ids=identities)
    assert caught.value.code == "GITHUB_PUBLISHER_AUTHORITY_REQUIRED"


def test_distinct_publisher_approval_cannot_count_as_independent_review():
    observation = observed(publisher_ids=(10, 11))
    assert not observation.reviews[0].independent
    assert "independent_review_missing" in assess(observation).blockers
    assert assess(observed(publisher_ids=(10, 12))).ready_for_manual_merge
