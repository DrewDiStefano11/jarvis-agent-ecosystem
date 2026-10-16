"""Dated historical observations cannot confer current manual-merge readiness."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS

import pytest

from app.core.errors import DomainError
from app.github_publication import status as reporting
from app.github_publication.lifecycle import fold
from app.self_build import commit_journal
from tests.test_github_publication_readiness import observation
from tests.test_publication_lifecycle import entry, history


@pytest.mark.parametrize(
    ("age", "position", "validation", "expected"),
    [
        (0, "exact_commit", True, "completed"),
        (301, "exact_commit", True, "review_pending"),
        (-60, "exact_commit", True, "review_pending"),
        (0, "parent", True, "review_pending"),
        (0, "exact_commit", False, "validation_pending"),
    ],
)
def test_status_rechecks_head_age_and_validation(monkeypatch, age, position, validation, expected):
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
