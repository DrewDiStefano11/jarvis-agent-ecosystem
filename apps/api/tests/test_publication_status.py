"""Dated historical observations cannot confer current manual-merge readiness."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS

import pytest

from app.core.errors import DomainError
from app.github_publication import status as reporting
from app.github_publication.lifecycle import fold
from app.github_publication.transport import GitHubRetry
from app.self_build import commit_journal
from tests.test_github_publication_readiness import observation
from tests.test_publication_lifecycle import entry, history


@pytest.mark.parametrize(
    ("age", "position", "validation", "remote", "expected"),
    [
        (0, "exact_commit", True, "passed", "completed"),
        (301, "exact_commit", True, "passed", "review_pending"),
        (-60, "exact_commit", True, "passed", "review_pending"),
        (0, "parent", True, "passed", "review_pending"),
        (0, "exact_commit", False, "passed", "validation_pending"),
        (0, "exact_commit", True, "head_changed", "review_pending"),
        (0, "exact_commit", True, "check_failed", "review_pending"),
        (0, "exact_commit", True, "review_changed", "review_pending"),
        (0, "exact_commit", True, "profile_revoked", "blocked"),
        (0, "exact_commit", True, "retry", "review_pending"),
        (0, "exact_commit", True, "validation_revoked", "validation_pending"),
    ],
)
def test_status_rechecks_head_age_and_validation(
    monkeypatch, age, position, validation, remote, expected
):
    entries = history()
    entries.append(
        entry(
            "observation",
            entries[-1],
            observation=observation(
                observed_at=datetime.now(UTC) - timedelta(seconds=age)
            ).model_dump(mode="json"),
        )
    )
    snapshot = fold(entries)
    proposal = NS(
        parent_sha=snapshot.plan.parent_sha,
        prospective_commit_sha=snapshot.plan.commit_sha,
        branch=snapshot.plan.branch,
    )
    native_history = NS(
        current=NS(
            state="checkpointed",
            plan=NS(
                plan_hash=snapshot.plan.native_plan_hash,
                preparation=NS(plan=NS(proposal=proposal)),
            ),
        )
    )
    monkeypatch.setattr(
        commit_journal,
        "NativeCommitJournal",
        lambda native: NS(history=lambda *args: native_history),
    )
    monkeypatch.setattr(
        reporting, "PublicationJournal", lambda service: NS(read=lambda *args: snapshot)
    )

    def require(*args):
        if not validation:
            raise DomainError("GITHUB_VALIDATION_PENDING", "Evidence is unavailable.", 409)

    native = NS(recovery=lambda *args: NS(position=NS(branch=position)))
    service = NS(validation=NS(require=require))
    remote_calls = []

    def current(*args):
        remote_calls.append(remote)
        if remote == "profile_revoked":
            raise DomainError("GITHUB_PUBLICATION_PROFILE_CHANGED", "Profile changed.", 409)
        if remote == "retry":
            raise GitHubRetry(60)
        if remote == "validation_revoked":
            raise DomainError("GITHUB_VALIDATION_PENDING", "Evidence expired.", 409)
        changes = dict(observed_at=datetime.now(UTC))
        if remote == "head_changed":
            changes.update(head_sha="f" * 40, head_sha_after_collection="f" * 40)
        elif remote == "check_failed":
            changes["checks"] = [
                dict(
                    evidence_id="check-new",
                    context="backend",
                    app_id=42,
                    head_sha=snapshot.plan.commit_sha,
                    state="failed",
                )
            ]
        elif remote == "review_changed":
            changes["reviews"] = [
                dict(
                    evidence_id="review-new",
                    reviewer_id=2,
                    head_sha=snapshot.plan.commit_sha,
                    state="changes_requested",
                    submitted_at=datetime.now(UTC),
                    independent=True,
                )
            ]
        return observation(**changes)

    monkeypatch.setattr(reporting, "read_current", current)
    result = reporting.status(
        native,
        service,
        object(),
        snapshot.plan.workspace_id,
        snapshot.plan.native_command_id,
        snapshot.operation_id,
    )
    assert result.state == expected
    assert result.ready_for_manual_merge == (expected == "completed")
    assert result.automatic_merge_allowed is False
    assert len(remote_calls) == (1 if age == 0 and position == "exact_commit" and validation else 0)
    if remote == "retry":
        assert result.retry_not_before is not None
        assert result.error_code == "GITHUB_RETRY_REQUIRED"
