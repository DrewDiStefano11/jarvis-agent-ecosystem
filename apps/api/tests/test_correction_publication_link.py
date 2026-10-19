"""Direct-child reference checks use interface fixtures, never lineage authority."""

from contextlib import nullcontext
from types import SimpleNamespace as NS

import pytest

from app.core.errors import DomainError
from app.github_publication import correction_link as linking


@pytest.mark.parametrize(
    "tamper", [None, "sibling", "base", "owner", "checkpoint", "selection", "observation"]
)
def test_direct_child_link_preserves_original_destination_and_refuses_foreign_state(
    monkeypatch, tamper
):
    parent, base, child = "a" * 40, "b" * 40, "c" * 40
    root = NS(
        task_id="task",
        commit_sha=parent,
        link_hash="1" * 64,
        publication_digest="2" * 64,
        original_code_state=NS(repository_identity="github.com/example/repo"),
    )
    published = NS(
        digest=root.publication_digest,
        pr_number=7,
        plan=NS(actor_id="actor", branch="codex/original", approved_base_sha=base),
    )
    row = NS(task_id="task", actor_id="actor", runtime_run_id="child-run")
    proposal = NS(
        parent_sha=parent,
        branch="codex/child",
        files=(NS(path="file.py"),),
        prospective_commit_sha=child,
        prospective_tree_sha="d" * 40,
        lineage=NS(actor_id="actor", runtime_run_id="child-run"),
    )
    record = NS(
        state="checkpointed",
        workspace_id="child",
        operation_id="native-child",
        checkpoint_id="checkpoint-child",
        plan=NS(
            plan_hash="3" * 64, snapshot_digest="4" * 64, preparation=NS(plan=NS(proposal=proposal))
        ),
    )
    source = NS(
        head_sha=parent,
        original_base_sha=parent,
        repository_identity=root.original_code_state.repository_identity,
        changes=(NS(path="file.py"),),
    )
    if tamper == "sibling":
        proposal.parent_sha = base
    elif tamper == "base":
        source.original_base_sha = base
    elif tamper == "owner":
        row.actor_id = "foreign"
    elif tamper == "selection":
        proposal.files = ()
    reads = []

    def read(*args):
        reads.append(True)
        if tamper == "observation" and len(reads) > 1:
            return NS(digest="f" * 64)
        return published

    monkeypatch.setattr(linking, "read_source_link", lambda *args: root)
    monkeypatch.setattr(linking, "PublicationJournal", lambda *args: NS(read=read))
    monkeypatch.setattr(
        linking,
        "NativeCommitJournal",
        lambda *args: NS(history=lambda *args: NS(current=record, source_snapshot=source)),
    )
    monkeypatch.setattr(
        linking,
        "checkpoint",
        lambda *args: (
            None if tamper == "checkpoint" else object(),
            "checkpoint-child",
            "sha256:" + "5" * 64,
            {},
        ),
    )
    service = NS(
        native=NS(repository=NS(sessions=lambda: nullcontext(object()), lookup=lambda *args: row))
    )
    args = (
        service,
        object(),
        "child",
        "native-command",
        "original",
        "publication",
        parent,
        "6" * 64,
    )
    if tamper is not None:
        with pytest.raises(DomainError):
            linking.read_correction_link(*args)
        return
    linked = linking.read_correction_link(*args)
    assert linked.remote_branch == "codex/original"
    assert linked.local_branch == "codex/child"
    assert linked.expected_remote_sha == linked.source_base_sha == parent
    assert linked.approved_pr_base_sha == base
    assert linked.commit_sha == child and linked.pr_number == 7
    assert linked.historical_only
    assert not linked.publication_authority and not linked.lineage_admission_verified
