"""Reject changed observation and missing acknowledgements before native resolution."""

from types import SimpleNamespace as NS

import pytest

from app.core.errors import DomainError
from app.github_publication import source_link
from app.github_publication.lifecycle import fold
from app.self_build.policy import digest
from tests.test_github_publication_readiness import observation
from tests.test_publication_lifecycle import history


@pytest.mark.parametrize(
    "tamper", ["head", "digest", "repository", "pr", "incomplete", "checkpoint"]
)
def test_untrusted_observation_or_unacknowledged_journal_cannot_link_source(monkeypatch, tamper):
    measured = observation()
    observation_digest = digest(measured.model_dump(mode="json"))
    if tamper == "repository":
        measured = measured.model_copy(update={"repository_id": 999})
    elif tamper == "pr":
        measured = measured.model_copy(update={"pr_number": 999})
    elif tamper == "incomplete":
        measured = measured.model_copy(update={"complete": False})
    # The protected reader must refuse these inputs before touching native source.
    # Use a fixture snapshot rather than pretending foreign entries are valid history.
    snapshot = fold(history()).model_copy(update={"observation": measured})
    monkeypatch.setattr(
        source_link,
        "PublicationJournal",
        lambda service: NS(
            read=lambda *args: snapshot,
            checkpoint=lambda *args: (
                (None, "checkpoint", {}) if tamper == "checkpoint" else (object(), "checkpoint", {})
            ),
        ),
    )

    def forbidden(*args):
        pytest.fail("invalid observation must refuse before native source resolution")

    monkeypatch.setattr(source_link, "NativeCommitJournal", forbidden)
    expected_head = "f" * 40 if tamper == "head" else snapshot.plan.commit_sha
    expected_digest = "0" * 64 if tamper == "digest" else observation_digest
    with pytest.raises(DomainError) as error:
        source_link.read_source_link(
            NS(native=object()),
            object(),
            snapshot.plan.workspace_id,
            snapshot.operation_id,
            expected_head,
            expected_digest,
        )
    assert error.value.code in {
        "GITHUB_CORRECTION_OBSERVATION_CHANGED",
        "GITHUB_CORRECTION_CHECKPOINT_REQUIRED",
    }
