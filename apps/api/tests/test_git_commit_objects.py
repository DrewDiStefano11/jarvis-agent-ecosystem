"""Compiled private object payloads cross-checked with independent native Git."""

import os
import subprocess

import pytest

from app.self_build.commit_objects import compile_objects
from tests.test_git_commit_preparation import make

pytest_plugins = ["tests.test_git_commit_preparation"]


def test_compiled_objects_are_exact_native_git_payloads(proposal):
    observation, _, _, _, _, root, observer, _ = proposal
    plan = make(proposal)
    program = compile_objects(plan, observation, {"hello.txt": b"changed\n"})
    assert program.tree_sha == plan.prospective_tree_sha
    assert program.commit_sha == plan.prospective_commit_sha
    assert [item.kind for item in program.objects] == ["blob", "tree", "commit"]
    for item in program.objects:
        measured = (
            subprocess.run(
                [str(observer.executable), "hash-object", "-w", "-t", item.kind, "--stdin"],
                cwd=root,
                input=item.payload,
                capture_output=True,
                check=True,
            )
            .stdout.strip()
            .decode()
        )
        assert measured == item.sha
        content = subprocess.run(
            [str(observer.executable), "cat-file", item.kind, measured],
            cwd=root,
            capture_output=True,
            check=True,
        ).stdout
        assert content == item.payload
    assert compile_objects(plan, observation, {"hello.txt": b"changed\n"}) == program
    assert "changed" not in repr(program)
    assert "changed" not in repr(program.objects[0])
    compiled_index = root / "compiled-index"
    compiled_index.write_bytes(program.index_payload)
    env = os.environ | {"GIT_INDEX_FILE": str(compiled_index), "GIT_OPTIONAL_LOCKS": "0"}
    indexed = subprocess.run(
        [str(observer.executable), "ls-files", "--stage", "-z"],
        cwd=root,
        env=env,
        capture_output=True,
        check=True,
    ).stdout
    assert indexed == b"100644 " + program.objects[0].sha.encode() + b" 0\thello.txt\0"
    tree = (
        subprocess.run(
            [str(observer.executable), "write-tree"],
            cwd=root,
            env=env,
            capture_output=True,
            check=True,
        )
        .stdout.strip()
        .decode()
    )
    assert tree == program.tree_sha


@pytest.mark.parametrize(
    "content",
    [
        {},
        {"hello.txt": b"forged\n"},
        {"hello.txt": b"changed\n", "unselected.py": b"extra\n"},
        {"hello.txt": "changed\n"},
    ],
)
def test_compiler_rejects_unmeasured_or_unselected_bytes(proposal, content):
    with pytest.raises(ValueError):
        compile_objects(make(proposal), proposal[0], content)


def test_compiler_refuses_stale_observation_or_exact_object_mismatch(proposal):
    from app.self_build.policy import digest

    plan = make(proposal)
    observation = proposal[0]
    with pytest.raises(ValueError):
        compile_objects(
            plan.model_copy(update={"observation_fingerprint": "a" * 64}),
            observation,
            {"hello.txt": b"changed\n"},
        )
    payload = plan.model_dump(mode="json", exclude={"plan_hash"})
    payload["observation_fingerprint"] = "a" * 64
    altered = type(plan)(**payload, plan_hash=digest(payload))
    with pytest.raises(ValueError):
        compile_objects(altered, observation, {"hello.txt": b"changed\n"})
    payload = plan.model_dump(mode="json", exclude={"plan_hash"})
    payload["preparation_policy_digest"] = "b" * 64
    stale = type(plan)(**payload, plan_hash=digest(payload))
    with pytest.raises(ValueError):
        compile_objects(stale, observation, {"hello.txt": b"changed\n"})


def test_compiler_enforces_native_object_payload_bound(proposal, monkeypatch):
    monkeypatch.setattr("app.self_build.commit_objects.MAX_BYTES", 1)
    with pytest.raises(ValueError):
        compile_objects(make(proposal), proposal[0], {"hello.txt": b"changed\n"})


def test_compiler_refuses_original_blob_identity_collision(proposal, monkeypatch):
    # Force the native object-ID calculation to return an existing original blob
    # ID. Even a supplied coherent selected hash must not replace that original
    # identity with different bytes elsewhere in the candidate tree.
    from app.self_build.commit_objects import GitObject

    original = proposal[0].original_inventory[0].identity.blob_sha
    monkeypatch.setattr(GitObject, "sha", property(lambda self: original))
    with pytest.raises(ValueError, match="unchanged original object"):
        compile_objects(make(proposal), proposal[0], {"hello.txt": b"changed\n"})


def test_nested_binary_additions_and_deletion_match_native_index_tree(proposal):
    from hashlib import sha256

    from app.models.git_changes import FileChange, FileIdentity
    from app.self_build.commit_planner import object_sha
    from app.self_build.policy import digest
    from tests.test_git_commit_preparation import rehash_observation

    observation, _, _, _, metadata, root, observer, native = proposal
    content = {"a.c": b"file\n", "a/x": b"\0binary\xff", "nested/x": b"\0binary\xff"}
    changes = [
        FileChange(
            path="hello.txt",
            kind="deleted",
            tracked=True,
            before=observation.original_inventory[0].identity,
            after=None,
        )
    ]
    inventory = []
    for path, raw in sorted(content.items()):
        blob = native("hash-object", "-w", "--no-filters", "--stdin", content=raw).decode()
        value = FileIdentity(
            mode="100644", blob_sha=blob, sha256=sha256(raw).hexdigest(), byte_count=len(raw)
        )
        changes.append(FileChange(path=path, kind="added", tracked=False, before=None, after=value))
        inventory.append(dict(path=path, identity=value.model_dump(mode="json")))
        native("update-index", "--add", "--cacheinfo", "100644," + blob + "," + path)
    native("update-index", "--force-remove", "--", "hello.txt")
    tree = native("write-tree").decode()
    changes.sort(key=lambda item: item.path)
    observation = rehash_observation(
        observation,
        dict(
            current_inventory=inventory,
            changes=[item.model_dump(mode="json") for item in changes],
            untracked_file_count=3,
            inventory_digest=digest([[item["path"], item["identity"]] for item in inventory]),
        ),
    )
    payload = make(proposal).model_dump(mode="json", exclude={"plan_hash"})
    raw_commit = metadata.payload(tree, observation.head_sha)
    payload.update(
        files=[item.model_dump(mode="json") for item in changes],
        observation_fingerprint=observation.change_fingerprint,
        prospective_tree_sha=tree,
        prospective_commit_sha=object_sha("commit", raw_commit),
        commit_payload_sha256=sha256(raw_commit).hexdigest(),
    )
    plan = type(make(proposal))(**payload, plan_hash=digest(payload))
    program = compile_objects(plan, observation, content)
    assert program.tree_sha == tree
    assert sum(item.kind == "blob" for item in program.objects) == 2
    assert sum(item.kind == "tree" for item in program.objects) == 2
    assert program.objects[-1].payload == raw_commit
    index = root / "compiled-nested-index"
    index.write_bytes(program.index_payload)
    env = os.environ | {"GIT_INDEX_FILE": str(index), "GIT_OPTIONAL_LOCKS": "0"}
    result = (
        subprocess.run(
            [str(observer.executable), "write-tree"],
            cwd=root,
            env=env,
            capture_output=True,
            check=True,
        )
        .stdout.strip()
        .decode()
    )
    assert result == program.tree_sha
