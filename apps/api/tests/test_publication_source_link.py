"""Reject changed observation and missing acknowledgements before native resolution."""

from types import SimpleNamespace as NS

import pytest

from app.core.errors import DomainError
from app.github_publication import source_link
from app.github_publication.lifecycle import fold
from app.self_build.policy import digest
from tests.test_github_publication_readiness import observation
from tests.test_publication_lifecycle import history


@pytest.mark.parametrize("tamper", [None, "native_checkpoint", "source_plan", "selection"])
def test_source_link_requires_exact_native_and_mutation_provenance(monkeypatch, tamper):
    snapshot = fold(history()).model_copy(update={"observation": observation()})
    plan = snapshot.plan
    change = NS(
        path="source.py",
        before=NS(sha256="0" * 64, mode="100644"),
        after=NS(sha256="1" * 64, mode="100644"),
    )
    proposal = NS(
        parent_sha=plan.parent_sha,
        prospective_commit_sha=plan.commit_sha,
        prospective_tree_sha="c" * 40,
        files=(NS(path="source.py"),),
        lineage=NS(
            actor_id=plan.actor_id, runtime_run_id=plan.runtime_run_id, attempt_id=plan.attempt_id
        ),
    )
    proof = NS(
        proof_hash="a" * 64,
        mutation_operation_id="mutation-operation",
        mutation_checkpoint_id="mutation-checkpoint",
        editing_record_id="b" * 64,
        editing_plan_hash="c" * 64,
    )
    record = NS(
        state="checkpointed",
        workspace_id=plan.workspace_id,
        operation_id="native-operation",
        checkpoint_id="native-checkpoint",
        plan=NS(
            plan_hash=plan.native_plan_hash,
            snapshot_digest=plan.source_snapshot_digest,
            preparation=NS(plan=NS(proposal=proposal)),
            applied_source=proof,
        ),
    )
    original = NS(
        head_sha=plan.parent_sha,
        original_base_sha=plan.approved_base_sha,
        repository_identity=plan.repository_identity,
        changes=(change,),
    )
    if tamper == "source_plan":
        record.plan.plan_hash = "f" * 64
    elif tamper == "selection":
        proposal.files = ()
    monkeypatch.setattr(
        source_link,
        "PublicationJournal",
        lambda service: NS(
            read=lambda *args: snapshot,
            checkpoint=lambda *args: (object(), "publication-checkpoint", {}),
        ),
    )
    monkeypatch.setattr(
        source_link,
        "NativeCommitJournal",
        lambda native: NS(
            history=lambda *args: NS(current=record, source_snapshot=original),
        ),
    )
    monkeypatch.setattr(
        source_link,
        "checkpoint",
        lambda *args: (
            None if tamper == "native_checkpoint" else object(),
            "native-checkpoint",
            "sha256:" + "d" * 64,
            {},
        ),
    )
    args = (
        NS(native=object()),
        object(),
        plan.workspace_id,
        snapshot.operation_id,
        plan.commit_sha,
        digest(snapshot.observation.model_dump(mode="json")),
    )
    if tamper is not None:
        with pytest.raises(DomainError):
            source_link.read_source_link(*args)
        return
    # Interface-only fixture, not a real checkpoint or evidence importer.
    linked = source_link.read_source_link(*args)
    assert linked.mutation_checkpoint_id == proof.mutation_checkpoint_id
    assert linked.source_edit_record_id == proof.editing_record_id
    assert linked.source_edit_plan_hash == proof.editing_plan_hash
    assert linked.original_code_state.head_sha == plan.parent_sha
    assert linked.historical_only and not linked.mutation_authority
    assert not linked.validation_authority


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
