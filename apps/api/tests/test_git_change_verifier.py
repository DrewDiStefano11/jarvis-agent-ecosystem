"""Real native change inventories in isolated owned repositories."""

import os
import subprocess
from hashlib import sha256
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.errors import DomainError
from app.models.git_changes import GitChangeObservation
from app.self_build.change_verifier import ChangeVerifier, index_entries, path_nodes

pytest_plugins = ["tests.test_workspace_creation_service"]


@pytest.fixture
def verified(creation):
    _, actor, _, service, workspace_id, request, _ = creation
    record = service.create(actor, workspace_id, request)
    policy = service.workspace.policies.get("jarvis")
    with service.repository.sessions() as session:
        row = service.repository.lookup(session, workspace_id)
        _, nonce, _ = service.intent(row)

    def guard():
        with service.repository.leases._write() as session:
            service.fence(actor, workspace_id, request, session)

    verifier = ChangeVerifier(service.git.tool(), guard)
    target = Path(policy.worktree_root) / workspace_id
    return verifier, policy, record, nonce, target, service, actor


def test_complete_changes_and_deterministic_preview_preserve_indexes(verified):
    verifier, policy, record, nonce, target, _, _ = verified
    primary_index = (Path(policy.primary_root) / ".git/index").read_bytes()
    dedicated = Path(policy.primary_root) / ".git/worktrees" / record.workspace_id / "index"
    original_index = dedicated.read_bytes()
    clean = verifier.inspect(policy, record, nonce)
    assert clean.index_clean and clean.worktree_clean
    (target / "source.py").write_bytes(b"value = 2\n")
    (target / "new.txt").write_bytes(b"added\n")
    result = verifier.inspect(policy, record, nonce)
    assert [(item.path, item.kind) for item in result.changes] == [
        ("new.txt", "added"),
        ("source.py", "modified"),
    ]
    assert result.index_clean and not result.worktree_clean and result.untracked_file_count == 1
    assert result.changes[1].before.sha256 != result.changes[1].after.sha256
    assert result.changes[1].before.sha256 == sha256(b"value = 1\n").hexdigest()
    assert result.changes[1].after.sha256 == sha256(b"value = 2\n").hexdigest()
    native_blob = (
        subprocess.run(
            [
                str(verifier.driver.observer.executable),
                "-C",
                str(target),
                "hash-object",
                "--no-filters",
                "--stdin",
            ],
            input=b"value = 2\n",
            check=True,
            capture_output=True,
        )
        .stdout.decode()
        .strip()
    )
    assert result.changes[1].after.blob_sha == native_blob
    assert verifier.inspect(policy, record, nonce) == result
    assert primary_index == (Path(policy.primary_root) / ".git/index").read_bytes()
    assert dedicated.read_bytes() == original_index
    (target / "source.py").unlink()
    deleted = verifier.inspect(policy, record, nonce)
    assert deleted.changes[1].kind == "deleted" and deleted.changes[1].after is None
    assert deleted.change_fingerprint != result.change_fingerprint
    assert str(target) not in result.model_dump_json() and nonce not in result.model_dump_json()


@pytest.mark.parametrize("count", [4096, 4097])
def test_source_file_limit_excludes_late_native_and_backup_artifacts(verified, monkeypatch, count):
    from app.models.git_changes import NativeArtifact
    from app.tool_execution.filesystem import windows_native_path

    verifier, policy, record, nonce, target, _, _ = verified
    target = Path(windows_native_path(target))
    for index in range(count - 1):
        (target / f"limit-{index:04d}.txt").write_bytes(b"")
    backup = ".jarvis-edit-" + "a" * 64 + ".before"
    (target / backup).write_bytes(b"")
    artifact = NativeArtifact(
        path=backup,
        kind="source_backup",
        sha256=sha256(b"").hexdigest(),
        byte_count=0,
        provenance_digest=record.ownership_digest,
        device=(target / backup).stat().st_dev,
        inode=(target / backup).stat().st_ino,
    )
    native_walk = os.walk

    def source_first(*args, **kwargs):
        for directory, children, files in native_walk(*args, **kwargs):
            files.sort(key=lambda name: name in {".git", ".jarvis-workspace.json", backup})
            yield directory, children, files

    monkeypatch.setattr(os, "walk", source_first)
    if count > 4096:
        with pytest.raises(DomainError) as failure:
            verifier.inspect(policy, record, nonce, native_artifacts=(artifact,))
        assert failure.value.code == "SELF_BUILD_CHANGE_LIMIT"
    else:
        result = verifier.inspect(policy, record, nonce, native_artifacts=(artifact,))
        assert len(result.current_inventory) == count
        assert len(result.internal_inventory) == 3
        assert result.untracked_file_count == count - 1


def test_ignored_files_are_included_and_hostile_filters_are_not_run(verified):
    verifier, policy, record, nonce, target, _, _ = verified
    common = Path(policy.primary_root) / ".git"
    (common / "info/exclude").write_text("ignored.txt\n")
    (target / "ignored.txt").write_bytes(b"complete inventory\n")
    (target / ".gitattributes").write_bytes(b"*.txt filter=hostile\n")
    with (common / "config").open("a") as config:
        config.write('\n[filter "hostile"]\n clean = impossible-filter-command\n required = true\n')
    result = verifier.inspect(policy, record, nonce)
    assert {item.path for item in result.changes} == {"ignored.txt", ".gitattributes"}


def test_dirty_index_is_measured_without_staging_or_repair(verified):
    verifier, policy, record, nonce, target, _, _ = verified
    (target / "source.py").write_bytes(b"staged content\n")
    subprocess.run(
        [str(verifier.driver.observer.executable), "-C", str(target), "add", "--", "source.py"],
        check=True,
        capture_output=True,
    )
    result = verifier.inspect(policy, record, nonce)
    assert not result.index_clean and result.changes[0].kind == "modified"


@pytest.mark.parametrize(
    "path", [".env", ".ssh/key", "secret.db", ".jarvis-stage-x", ".git/foreign"]
)
def test_protected_or_internal_paths_refuse_complete_observation(verified, path):
    verifier, policy, record, nonce, target, _, _ = verified
    if path.startswith(".git/"):
        (target / ".git").unlink()
    location = target / path
    location.parent.mkdir(parents=True, exist_ok=True)
    location.write_bytes(b"unsafe")
    with pytest.raises(DomainError):
        verifier.inspect(policy, record, nonce)


def test_foreign_branch_and_marker_are_rejected(verified):
    verifier, policy, record, nonce, target, _, _ = verified
    metadata = Path(policy.primary_root) / ".git/worktrees" / record.workspace_id
    original_head = (metadata / "HEAD").read_bytes()
    (metadata / "HEAD").write_text("ref: refs/heads/main\n")
    with pytest.raises(DomainError):
        verifier.inspect(policy, record, nonce)
    (metadata / "HEAD").write_bytes(original_head)
    (target / ".jarvis-workspace.json").write_text("{}")
    with pytest.raises(DomainError):
        verifier.inspect(policy, record, nonce)


def test_missing_workspace_is_not_created(verified):
    verifier, policy, record, nonce, target, _, _ = verified
    target.rename(target.with_name("preserved-foreign"))
    with pytest.raises(DomainError):
        verifier.inspect(policy, record, nonce)
    assert not target.exists()


def test_hard_link_and_callback_writes_cannot_escape_pins(verified):
    verifier, policy, record, nonce, target, _, _ = verified
    os.link(target / "source.py", target / "alias.py")
    with pytest.raises(DomainError):
        verifier.inspect(policy, record, nonce)
    (target / "alias.py").unlink()

    def finalize(measured, guard):
        with pytest.raises(OSError):
            (target / "source.py").write_bytes(b"raced")
        guard()
        return measured

    assert verifier.inspect(policy, record, nonce, finalize=finalize).worktree_clean


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
def test_ambiguous_index_flags_are_refused(verified, flag):
    verifier, policy, record, nonce, target, _, _ = verified
    subprocess.run(
        [
            str(verifier.driver.observer.executable),
            "-C",
            str(target),
            "update-index",
            flag,
            "--",
            "source.py",
        ],
        check=True,
        capture_output=True,
    )
    with pytest.raises(DomainError) as failure:
        verifier.inspect(policy, record, nonce)
    assert failure.value.code == "SELF_BUILD_CHANGE_INDEX_INVALID"


@pytest.mark.parametrize("path", ["alias~1/file.py", "e\u0301.py", "folder/\u202efile.py"])
def test_unicode_and_short_name_aliases_are_refused(path):
    with pytest.raises(ValueError):
        path_nodes([(path, True)])


def test_fingerprint_contract_rejects_tampered_evidence(verified):
    verifier, policy, record, nonce, _, _, _ = verified
    result = verifier.inspect(policy, record, nonce)
    with pytest.raises(ValidationError):
        GitChangeObservation.model_validate({**result.model_dump(), "index_clean": False})


@pytest.mark.parametrize(
    "paths",
    [
        [("A/file.py", True), ("a/other.py", True)],
        [("a", True), ("a/file.py", True)],
        [("a.py", True), ("A.py", True)],
    ],
)
def test_component_collisions_are_rejected(paths):
    with pytest.raises(DomainError):
        path_nodes(paths)


@pytest.mark.parametrize(
    "raw", [b"100644 " + b"a" * 40 + b" 1\tsource.py\0", b"100644 " + b"a" * 40 + b" 0\tsource.py"]
)
def test_unmerged_or_truncated_index_refused(raw):
    with pytest.raises(DomainError):
        index_entries(raw)


def test_long_derived_backup_is_measured_under_native_pins(verified):
    from app.models.git_changes import NativeArtifact
    from app.tool_execution.filesystem import windows_native_path

    verifier, policy, record, nonce, target, _, _ = verified
    target = Path(windows_native_path(target))
    parent = "a" * 118 + "/" + "b" * 118
    path = parent + "/.jarvis-edit-" + "a" * 64 + ".before"
    backup = target / path
    backup.parent.mkdir(parents=True)
    backup.write_bytes(b"original")
    native = backup.stat()
    artifact = NativeArtifact(
        path=path,
        kind="source_backup",
        sha256=sha256(b"original").hexdigest(),
        byte_count=8,
        provenance_digest=record.ownership_digest,
        device=native.st_dev,
        inode=native.st_ino,
    )
    result = verifier.inspect(policy, record, nonce, native_artifacts=(artifact,))
    assert artifact in result.internal_inventory
    assert path not in {item.path for item in result.current_inventory}
    assert backup.stat().st_ino == native.st_ino
    unclaimed = NativeArtifact.model_validate(artifact.model_dump(exclude={"device", "inode"}))
    with pytest.raises(DomainError) as failure:
        verifier.inspect(policy, record, nonce, native_artifacts=(unclaimed,))
    assert failure.value.code == "SELF_BUILD_CHANGE_PROTECTED"
