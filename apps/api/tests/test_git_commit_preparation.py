"""Prospective objects cross-checked against real Git in isolated repositories."""

import subprocess
from hashlib import sha256

import pytest
from pydantic import ValidationError

from app.core.errors import DomainError
from app.models.git_changes import FileChange, FileIdentity, GitChangeObservation, InventoryFile
from app.models.git_commit import CommitLineage, CommitMetadata, CommitPerson, CommitPreparationPlan
from app.models.source_change import (
    FileIdentity as SourceFileIdentity,
)
from app.models.source_change import (
    PlanReferences,
    SourceChange,
    SourceChangeIntent,
    SourceContent,
    SourceEntry,
    SourceObservation,
    Topology,
)
from app.self_build.change_verifier import VERIFICATION_POLICY_DIGEST
from app.self_build.commit_planner import commit_payload, propose_commit, tree_sha
from app.self_build.policy import digest
from app.source_change.planner import observation_identity, plan_source_changes

pytest_plugins = ["tests.test_self_build_git_observer"]


def source_content(text):
    raw = text.encode()
    return SourceContent(
        text=text, digest=sha256(raw).hexdigest(), byte_count=len(raw), mode="100644"
    )


@pytest.fixture
def proposal(repository):
    root, policy, observer, head, git = repository

    def native(*args, content=None):
        return subprocess.run(
            [str(observer.executable), *args],
            cwd=root,
            input=content,
            check=True,
            capture_output=True,
        ).stdout.strip()

    # Read exact bytes without strip for the Git object identity.
    before_bytes = subprocess.run(
        [str(observer.executable), "cat-file", "blob", "HEAD:hello.txt"],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    after_bytes = b"changed\n"

    def file(content):
        return FileIdentity(
            mode="100644",
            blob_sha=native("hash-object", "--no-filters", "--stdin", content=content).decode(),
            sha256=sha256(content).hexdigest(),
            byte_count=len(content),
        )

    before, after = file(before_bytes), file(after_bytes)
    changes = [
        FileChange(path="hello.txt", kind="modified", before=before, after=after, tracked=True)
    ]
    payload = dict(
        schema_version="1.0",
        workspace_id="jarvis-" + "a" * 32,
        creation_operation_id="creation-1",
        creation_plan_hash="b" * 64,
        ownership_digest="c" * 64,
        repository_identity=policy.repository_identity,
        branch="codex/jarvis-" + "a" * 32,
        original_base_sha=head,
        head_sha=head,
        original_tree_sha=git("rev-parse", "HEAD^{tree}"),
        tool_sha256=observer.executable_hash,
        verification_policy_digest=VERIFICATION_POLICY_DIGEST,
        mode_source="git_index_windows",
        index_clean=True,
        worktree_clean=False,
        tracked_file_count=1,
        untracked_file_count=0,
        inventory_digest=digest([["hello.txt", after.model_dump(mode="json")]]),
        index_digest=digest([[before.mode, before.blob_sha, "hello.txt"]]),
        original_inventory=[dict(path="hello.txt", identity=before.model_dump(mode="json"))],
        current_inventory=[dict(path="hello.txt", identity=after.model_dump(mode="json"))],
        index_inventory=[dict(path="hello.txt", mode=before.mode, blob_sha=before.blob_sha)],
        changes=[item.model_dump(mode="json") for item in changes],
    )
    fingerprint = digest(payload)
    observed = GitChangeObservation(
        **payload,
        change_fingerprint=fingerprint,
        commit_preview_fingerprint=digest(
            ["git.commit.preview/1.0", fingerprint, head, payload["changes"]]
        ),
    )
    content = source_content(before_bytes.decode())
    original = SourceObservation(
        repository_identity=policy.repository_identity,
        head_sha=head,
        base_sha=head,
        root=Topology(kind="directory"),
        entries=(SourceEntry(path="hello.txt", kind="file", content=content),),
    )
    references = PlanReferences(
        original_plan_hash="d" * 64, authorization_reference="original-approval"
    )
    intent = SourceChangeIntent(
        source=observation_identity(original),
        references=references,
        changes=(
            SourceChange(
                operation="modify",
                path="hello.txt",
                before=SourceFileIdentity(**content.model_dump(exclude={"text"})),
                after=source_content("changed\n"),
            ),
        ),
    )
    source_plan = plan_source_changes(intent, original)
    lineage = CommitLineage(
        actor_id="actor",
        runtime_run_id="run",
        attempt_id="attempt",
        worker_id="worker",
        source_edit_record_id="edit-1",
        source_edit_checkpoint_id="checkpoint-1",
        source_edit_plan_hash=source_plan.plan_hash,
        original_references=references,
    )
    person = CommitPerson(
        name="Reviewed author",
        email="author@example.invalid",
        timestamp=1791817701,
        timezone="-0400",
    )
    metadata = CommitMetadata(author=person, committer=person, message="Exact reviewed commit\n")
    return observed, source_plan, original, lineage, metadata, root, observer, native


def make(proposal, **changes):
    observed, source_plan, original, lineage, metadata, *_ = proposal
    values = dict(
        observation=observed,
        source_plan=source_plan,
        original_observation=original,
        lineage=lineage,
        metadata=metadata,
        selection=("hello.txt",),
    )
    return propose_commit(**(values | changes))


def test_added_deleted_and_subset_selection_match_native_trees(proposal):
    observed, source_plan, original, lineage, metadata, _, _, native = proposal
    added = source_content("new file\n")
    added_blob = native(
        "hash-object", "--no-filters", "--stdin", content=added.text.encode()
    ).decode()
    after = FileIdentity(
        mode=added.mode, blob_sha=added_blob, sha256=added.digest, byte_count=added.byte_count
    )
    before = observed.original_inventory[0].identity
    changes = [
        FileChange(path="hello.txt", kind="deleted", before=before, after=None, tracked=True),
        FileChange(path="new.txt", kind="added", before=None, after=after, tracked=False),
    ]
    payload = dict(
        current_inventory=[dict(path="new.txt", identity=after.model_dump(mode="json"))],
        changes=[change.model_dump(mode="json") for change in changes],
        untracked_file_count=1,
        inventory_digest=digest([["new.txt", after.model_dump(mode="json")]]),
    )
    observed = rehash_observation(observed, payload)
    original = original.model_copy(
        update={"entries": original.entries + (SourceEntry(path="new.txt", kind="absent"),)}
    )
    source_plan = plan_source_changes(
        SourceChangeIntent(
            source=observation_identity(original),
            references=source_plan.references,
            changes=(
                SourceChange(
                    operation="delete",
                    path="hello.txt",
                    before=SourceFileIdentity(
                        digest=before.sha256, byte_count=before.byte_count, mode=before.mode
                    ),
                    after=None,
                ),
                SourceChange(operation="add", path="new.txt", before=None, after=added),
            ),
        ),
        original,
    )
    lineage = lineage.model_copy(update={"source_edit_plan_hash": source_plan.plan_hash})
    arguments = dict(
        observation=observed,
        source_plan=source_plan,
        original_observation=original,
        lineage=lineage,
        metadata=metadata,
    )
    selected = propose_commit(**arguments, selection=("new.txt",))
    all_files = propose_commit(**arguments, selection=("new.txt", "hello.txt"))
    assert [change.path for change in selected.files] == ["new.txt"]
    assert [change.path for change in all_files.files] == ["hello.txt", "new.txt"]
    blob = native(
        "hash-object", "-w", "--no-filters", "--stdin", content=added.text.encode()
    ).decode()
    native("update-index", "--add", "--cacheinfo", "100644," + blob + ",new.txt")
    assert native("write-tree").decode() == selected.prospective_tree_sha
    native("update-index", "--force-remove", "--", "hello.txt")
    assert native("write-tree").decode() == all_files.prospective_tree_sha


def test_unrelated_changes_remain_unselected_but_invalidate_plan_hash(proposal):
    observed, _, _, _, _, _, _, native = proposal
    raw = b"unrelated\n"
    identity = FileIdentity(
        mode="100644",
        blob_sha=native("hash-object", "--no-filters", "--stdin", content=raw).decode(),
        sha256=sha256(raw).hexdigest(),
        byte_count=len(raw),
    )
    payload = dict(
        current_inventory=[item.model_dump(mode="json") for item in observed.current_inventory]
        + [dict(path="zz.txt", identity=identity.model_dump(mode="json"))],
        changes=[item.model_dump(mode="json") for item in observed.changes]
        + [
            FileChange(
                path="zz.txt", kind="added", before=None, after=identity, tracked=False
            ).model_dump(mode="json")
        ],
        untracked_file_count=1,
    )
    payload["inventory_digest"] = digest(
        [[item["path"], item["identity"]] for item in payload["current_inventory"]]
    )
    changed = rehash_observation(observed, payload)
    original_plan = make(proposal)
    changed_plan = make(proposal, observation=changed)
    assert original_plan.prospective_commit_sha == changed_plan.prospective_commit_sha
    assert original_plan.plan_hash != changed_plan.plan_hash
    with pytest.raises(DomainError):
        make(proposal, observation=changed, selection=("zz.txt",))


def test_exact_tree_commit_and_plan_match_native_git_without_planner_effects(proposal):
    observed, _, _, lineage, metadata, root, observer, native = proposal
    index_before = (root / ".git/index").read_bytes()
    refs_before = native("show-ref")
    result = make(proposal)
    assert not result.commit_creation_supported and result.state == "proposed"
    assert result.lineage == lineage and result.files == observed.changes
    assert make(proposal) == result
    assert (root / ".git/index").read_bytes() == index_before and native("show-ref") == refs_before
    assert (root / "hello.txt").read_bytes() != b"changed\n"
    blob = native("hash-object", "-w", "--no-filters", "--stdin", content=b"changed\n").decode()
    native("update-index", "--cacheinfo", "100644," + blob + ",hello.txt")
    assert native("write-tree").decode() == result.prospective_tree_sha
    raw = commit_payload(result.prospective_tree_sha, result.parent_sha, metadata)
    assert (
        native("hash-object", "-t", "commit", "--stdin", content=raw).decode()
        == result.prospective_commit_sha
    )
    assert sha256(raw).hexdigest() == result.commit_payload_sha256
    assert CommitPreparationPlan.model_validate(result.model_dump(mode="json")) == result


@pytest.mark.parametrize(
    "paths",
    [
        ["a.txt", "folder.txt", "folder/a.txt", "folder/z.txt", "folder0.txt"],
        ["nested/deep/data.bin", "nested/a.txt", "é.txt"],
        [],
    ],
)
def test_nested_binary_and_directory_ordered_trees_match_git(repository, paths):
    root, _, observer, _, _ = repository
    (root / "hello.txt").unlink()
    subprocess.run(
        [str(observer.executable), "rm", "--cached", "--", "hello.txt"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    entries = []
    for number, path in enumerate(paths):
        content = bytes([number, 255, 0, 10])
        destination = root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        blob = (
            subprocess.run(
                [str(observer.executable), "hash-object", "-w", "--no-filters", "--stdin"],
                cwd=root,
                input=content,
                check=True,
                capture_output=True,
            )
            .stdout.decode()
            .strip()
        )
        mode = "100755" if number == 0 else "100644"
        subprocess.run(
            [
                str(observer.executable),
                "update-index",
                "--add",
                "--cacheinfo",
                mode + "," + blob + "," + path,
            ],
            cwd=root,
            check=True,
            capture_output=True,
        )
        entries.append(
            InventoryFile(
                path=path,
                identity=FileIdentity(
                    mode=mode,
                    blob_sha=blob,
                    sha256=sha256(content).hexdigest(),
                    byte_count=len(content),
                ),
            )
        )
    expected = (
        subprocess.run(
            [str(observer.executable), "write-tree"], cwd=root, check=True, capture_output=True
        )
        .stdout.decode()
        .strip()
    )
    assert tree_sha(entries) == expected and tree_sha(tuple(reversed(entries))) == expected


@pytest.mark.parametrize(
    "selection",
    [(), ("hello.txt", "hello.txt"), ("missing.txt",), ("../outside",), (".jarvis-owner-x",)],
)
def test_unapproved_or_ambiguous_selection_refused(proposal, selection):
    with pytest.raises(DomainError):
        make(proposal, selection=selection)


@pytest.mark.parametrize("boundary", ["lineage", "source_plan", "observation", "metadata"])
def test_tampered_models_are_revalidated_without_effects(proposal, boundary):
    observed, source_plan, _, lineage, metadata, *_ = proposal
    changes = {}
    if boundary == "lineage":
        changes["lineage"] = lineage.model_copy(update={"source_edit_plan_hash": "0" * 64})
    elif boundary == "source_plan":
        changes["source_plan"] = source_plan.model_copy(update={"plan_hash": "0" * 64})
    elif boundary == "observation":
        changes["observation"] = observed.model_copy(update={"index_clean": False})
    else:
        changes["metadata"] = metadata.model_copy(update={"message": "injected\0message\n"})
    with pytest.raises(DomainError):
        make(proposal, **changes)


def rehash_observation(observed, changes):
    payload = (
        observed.model_dump(
            mode="json", exclude={"change_fingerprint", "commit_preview_fingerprint"}
        )
        | changes
    )
    fingerprint = digest(payload)
    return GitChangeObservation(
        **payload,
        change_fingerprint=fingerprint,
        commit_preview_fingerprint=digest(
            ["git.commit.preview/1.0", fingerprint, payload["head_sha"], payload["changes"]]
        ),
    )


@pytest.mark.parametrize("boundary", ["base_tree", "policy", "source_after"])
def test_coherently_rehashed_stale_evidence_still_refuses(proposal, boundary):
    observed = proposal[0]
    if boundary == "base_tree":
        observed = rehash_observation(observed, {"original_tree_sha": "0" * 40})
    elif boundary == "policy":
        observed = rehash_observation(observed, {"verification_policy_digest": "0" * 64})
    else:
        payload = observed.model_dump(mode="json")
        payload["current_inventory"][0]["identity"]["sha256"] = "0" * 64
        payload["changes"][0]["after"]["sha256"] = "0" * 64
        payload["inventory_digest"] = digest(
            [["hello.txt", payload["current_inventory"][0]["identity"]]]
        )
        observed = rehash_observation(
            observed,
            {
                key: value
                for key, value in payload.items()
                if key not in {"change_fingerprint", "commit_preview_fingerprint"}
            },
        )
    with pytest.raises(DomainError):
        make(proposal, observation=observed)


def test_message_or_time_changes_invalidate_exact_commit_plan(proposal):
    result = make(proposal)
    metadata = proposal[4]
    changed = make(
        proposal, metadata=metadata.model_copy(update={"message": "Different message\n"})
    )
    assert (
        changed.plan_hash != result.plan_hash
        and changed.prospective_commit_sha != result.prospective_commit_sha
    )
    assert changed.prospective_tree_sha == result.prospective_tree_sha
    with pytest.raises(ValidationError):
        CommitPreparationPlan.model_validate(
            {**result.model_dump(), "commit_creation_supported": True}
        )


@pytest.mark.parametrize(
    "values",
    [
        {"name": "bad\nidentity"},
        {"email": "name@example.invalid\nhelper"},
        {"timezone": "+1460"},
        {"timezone": "+1500"},
    ],
)
def test_commit_identity_cannot_inject_native_headers(values):
    with pytest.raises(ValidationError):
        CommitPerson(
            **(
                dict(
                    name="Author",
                    email="author@example.invalid",
                    timestamp=1791817701,
                    timezone="+0000",
                )
                | values
            )
        )
