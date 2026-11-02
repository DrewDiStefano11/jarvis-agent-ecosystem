"""Strong snapshot includes ordinary directories and the complete Git namespace."""

import os
from hashlib import sha256

import pytest

from app.core.errors import DomainError
from app.models.git_commit_metadata import CommitMetadataSnapshot
from app.self_build.commit_metadata import measure_metadata
from app.self_build.policy import RepositoryPolicy, digest


@pytest.fixture
def metadata_repository(tmp_path):
    primary, worktrees = tmp_path / "primary", tmp_path / "worktrees"
    primary.mkdir()
    worktrees.mkdir()
    common = primary / ".git"
    common.mkdir()
    (common / "refs").mkdir()
    (common / "refs/heads").mkdir()
    (common / "refs/heads/main").write_bytes(b"a" * 40 + b"\n")
    (common / "HEAD").write_bytes(b"ref: refs/heads/main\n")
    return RepositoryPolicy(
        repository_identity="github.com/example/repository",
        primary_root=str(primary),
        worktree_root=str(worktrees),
    ), common


@pytest.mark.skipif(os.name != "nt", reason="native Windows metadata pins")
def test_metadata_snapshot_is_strong_complete_and_deterministic(metadata_repository):
    policy, common = metadata_repository
    first = measure_metadata(policy, lambda: None)
    assert first == measure_metadata(policy, lambda: None)
    assert [node.path for node in first.nodes] == ["HEAD", "refs", "refs/heads", "refs/heads/main"]
    assert first.nodes[0].sha256 == sha256((common / "HEAD").read_bytes()).hexdigest()
    original = (common / "HEAD").stat()
    (common / "HEAD").write_bytes(b"ref: refs/heads/else\n")
    os.utime(common / "HEAD", ns=(original.st_atime_ns, original.st_mtime_ns))
    assert first.snapshot_digest != measure_metadata(policy, lambda: None).snapshot_digest
    assert "ref: refs" not in first.model_dump_json()


@pytest.mark.skipif(os.name != "nt", reason="native Windows metadata pins")
def test_metadata_scan_bounds_authority_work_and_rechecks_before_return(
    metadata_repository, monkeypatch
):
    policy, common = metadata_repository
    for index in range(40):
        (common / f"object-{index}").write_bytes(b"content")
    monkeypatch.setattr("app.self_build.commit_metadata.monotonic", lambda: 1.0, raising=False)
    calls = []

    def authority():
        calls.append(True)

    measured = measure_metadata(policy, authority)
    assert len(measured.nodes) == 44
    # Frozen time means there is no reason to repeat database-backed authority
    # work for every file/chunk. Opening and final publication remain mandatory.
    assert len(calls) == 2

    calls.clear()

    def revoked():
        calls.append(True)
        if len(calls) == 2:
            raise DomainError("AUTHORITY_REVOKED", "revoked", 403)

    with pytest.raises(DomainError, match="revoked"):
        measure_metadata(policy, revoked)


@pytest.mark.skipif(os.name != "nt", reason="native Windows metadata pins")
@pytest.mark.parametrize("foreign", ["alias", "hardlink", "transient"])
def test_metadata_snapshot_refuses_foreign_names_and_shared_files(
    metadata_repository, foreign, monkeypatch
):
    policy, common = metadata_repository
    if foreign == "alias":
        (common / "HEAD~1").write_bytes(b"alias")
    elif foreign == "hardlink":
        os.link(common / "HEAD", common / "foreign")
    read = os.read
    changed = []

    def read_with_transient_name(*args):
        if foreign == "transient" and not changed:
            changed.append(True)
            (common / "foreign").write_bytes(b"foreign")
            (common / "foreign").unlink()
        return read(*args)

    monkeypatch.setattr(os, "read", read_with_transient_name)
    with pytest.raises((DomainError, ValueError)):
        measure_metadata(policy, lambda: None)


@pytest.mark.skipif(os.name != "nt", reason="native Windows metadata pins")
def test_metadata_scan_rechecks_revocation_while_reading(metadata_repository, monkeypatch):
    clock = [0.0]
    calls = []
    read = os.read
    monkeypatch.setattr("app.self_build.commit_metadata.monotonic", lambda: clock[0])

    def slow_read(*args):
        clock[0] += 0.2
        return read(*args)

    def authority():
        calls.append(clock[0])
        if clock[0] >= 0.2:
            raise DomainError("AUTHORITY_REVOKED", "revoked during scan", 403)

    monkeypatch.setattr(os, "read", slow_read)
    with pytest.raises(DomainError, match="revoked during scan"):
        measure_metadata(metadata_repository[0], authority)
    assert calls == [0.0, 0.2]


def test_rehashed_metadata_snapshot_cannot_hide_parent_or_file_identity(metadata_repository):
    root = metadata_repository[1].stat()
    payload = dict(
        schema_version="1.0",
        root_device=root.st_dev,
        root_inode=root.st_ino,
        nodes=[
            dict(
                path="refs/heads/main",
                kind="file",
                device=1,
                inode=1,
                byte_count=1,
                sha256="a" * 64,
            ),
        ],
    )
    with pytest.raises(ValueError):
        CommitMetadataSnapshot(**payload, snapshot_digest=digest(payload))
