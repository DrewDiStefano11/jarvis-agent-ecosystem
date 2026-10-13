"""Prove wire compatibility and stale-ref refusal with isolated native Git."""

import os
import shutil
import subprocess
from dataclasses import dataclass
from hashlib import sha1

import pytest

from app.core.errors import DomainError
from app.github_publication.git_protocol import (
    MAX_RESPONSE_BYTES,
    ZERO,
    advertisement,
    encode_objects,
    packet,
    packets,
    receive_request,
    receive_result,
)
from app.github_publication.transport import RepositoryTarget


@dataclass(frozen=True)
class Object:
    kind: str
    payload: bytes

    @property
    def sha(self):
        return sha1(
            f"{self.kind} {len(self.payload)}\0".encode() + self.payload, usedforsecurity=False
        ).hexdigest()


TARGET = RepositoryTarget(123, "example", "repository", "main", "codex/")
REFERENCE = "refs/heads/codex/job"


def commit(tree, parent=None, message=b"change\n"):
    payload = b"tree " + tree.encode() + b"\n"
    if parent:
        payload += b"parent " + parent.encode() + b"\n"
    payload += b"author Test <test@example.com> 100 +0000\n"
    payload += b"committer Test <test@example.com> 100 +0000\n\n" + message
    return Object("commit", payload)


def synthetic_advertisement(*, refs=None, capabilities=b"report-status object-format=sha1"):
    refs = refs or {"refs/heads/main": "a" * 40}
    lines = []
    for index, (name, value) in enumerate(refs.items()):
        data = f"{value} {name}".encode()
        if index == 0:
            data += b"\0" + capabilities
        lines.append(packet(data + b"\n"))
    return packet(b"# service=git-receive-pack\n") + b"0000" + b"".join(lines) + b"0000"


@pytest.fixture
def repository(tmp_path):
    executable = shutil.which("git")
    if executable is None:
        pytest.skip("native Git unavailable")
    path = tmp_path / "remote.git"
    environment = dict(
        os.environ,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_TERMINAL_PROMPT="0",
        GIT_CONFIG_COUNT="0",
    )
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY"):
        environment.pop(name, None)

    def git(*args, data=None, check=True):
        completed = subprocess.run(
            [executable, "-c", "core.hooksPath=" + str(tmp_path / "no-hooks"), *args],
            input=data,
            capture_output=True,
            env=environment,
            timeout=30,
            check=False,
        )
        if check:
            assert completed.returncode == 0, completed.stderr.decode(errors="replace")
        return completed.stdout

    git("init", "--bare", "--object-format=sha1", str(path))
    tree = Object("tree", b"")
    parent = commit(tree.sha, message=b"parent\n")
    for item in (tree, parent):
        assert (
            git(
                "--git-dir=" + str(path),
                "hash-object",
                "-w",
                "--stdin",
                "-t",
                item.kind,
                data=item.payload,
            )
            .decode()
            .strip()
            == item.sha
        )
    git("--git-dir=" + str(path), "update-ref", "refs/heads/main", parent.sha)

    def advertised():
        return (
            packet(b"# service=git-receive-pack\n")
            + b"0000"
            + git("receive-pack", "--stateless-rpc", "--advertise-refs", str(path))
        )

    return path, git, advertised, tree, parent


def test_native_receive_pack_creates_exact_commit_and_preserves_main(repository):
    path, git, advertised, _, parent = repository
    blob = Object("blob", b"a" * 4096)
    tree = Object("tree", b"100644 change.txt\0" + bytes.fromhex(blob.sha))
    child = commit(tree.sha, parent.sha)
    request = receive_request(
        TARGET, "codex/job", parent.sha, child.sha, advertised(), (blob, tree, child)
    )
    assert request.old_sha == ZERO
    assert "PACK" not in repr(request)
    result = git("receive-pack", "--stateless-rpc", str(path), data=request.body)
    receive_result(result, REFERENCE)
    assert git("--git-dir=" + str(path), "rev-parse", REFERENCE).decode().strip() == child.sha
    assert (
        git("--git-dir=" + str(path), "rev-parse", "refs/heads/main").decode().strip() == parent.sha
    )
    for item in (blob, tree, child):
        assert git("--git-dir=" + str(path), "cat-file", item.kind, item.sha) == item.payload
    # A completed push must be reconciled rather than retransmitted as an update.
    with pytest.raises(DomainError) as error:
        receive_request(
            TARGET, "codex/job", parent.sha, child.sha, advertised(), (blob, tree, child)
        )
    assert error.value.code == "GITHUB_REMOTE_REFERENCE_CHANGED"


def test_native_server_rejects_concurrent_reference_change_without_overwrite(repository):
    path, git, advertised, tree, parent = repository
    git("--git-dir=" + str(path), "update-ref", REFERENCE, parent.sha)
    child, foreign = commit(tree.sha, parent.sha), commit(tree.sha, parent.sha, b"foreign\n")
    request = receive_request(TARGET, "codex/job", parent.sha, child.sha, advertised(), (child,))
    git(
        "--git-dir=" + str(path),
        "hash-object",
        "-w",
        "--stdin",
        "-t",
        "commit",
        data=foreign.payload,
    )
    git("--git-dir=" + str(path), "update-ref", REFERENCE, foreign.sha, parent.sha)
    result = git("receive-pack", "--stateless-rpc", str(path), data=request.body, check=False)
    with pytest.raises(DomainError) as error:
        receive_result(result, REFERENCE)
    assert error.value.code == "GITHUB_PUSH_REJECTED"
    assert "foreign" not in str(error.value)
    assert git("--git-dir=" + str(path), "rev-parse", REFERENCE).decode().strip() == foreign.sha


@pytest.mark.parametrize(
    "raw",
    [b"0001", b"0002", b"0004", b"0009abc", b"zzzz", b"0000x", b"x" * (MAX_RESPONSE_BYTES + 1)],
    ids=("delim-1", "delim-2", "empty-data", "truncated", "nonhex", "trailing", "over-limit"),
)
def test_truncated_delimited_or_oversized_provider_stream_refuses(raw):
    with pytest.raises(DomainError):
        packets(raw)


@pytest.mark.parametrize(
    "capabilities",
    [
        b"report-status object-format=sha256",
        b"side-band-64k",
        b"report-status report-status",
        b"report-status\nsecret",
    ],
)
def test_unsupported_or_ambiguous_capabilities_refuse_before_effect(capabilities):
    with pytest.raises(DomainError):
        advertisement(synthetic_advertisement(capabilities=capabilities))


def test_branch_scope_parent_and_duplicate_objects_cannot_expand_push():
    tree = Object("tree", b"")
    child = commit(tree.sha, "a" * 40)
    for branch in ("main", "other/job", "codex/../main"):
        with pytest.raises(DomainError):
            receive_request(
                TARGET, branch, "a" * 40, child.sha, synthetic_advertisement(), (tree, child)
            )
    with pytest.raises(DomainError):
        receive_request(
            TARGET, "codex/job", "b" * 40, child.sha, synthetic_advertisement(), (tree, child)
        )
    with pytest.raises(DomainError):
        encode_objects((tree, tree))


def test_remote_failures_never_echo_hook_or_provider_diagnostics():
    raw = (
        packet(b"unpack ok\n")
        + packet(("ng " + REFERENCE + " secret-token-text\n").encode())
        + b"0000"
    )
    with pytest.raises(DomainError) as error:
        receive_result(raw, REFERENCE)
    assert "secret-token-text" not in str(error.value)
    with pytest.raises(DomainError):
        receive_result(
            packet(b"unpack ok\n") + packet(b"ok refs/heads/main\n") + b"0000", REFERENCE
        )
    with pytest.raises(DomainError):
        receive_result(packet(b"unpack ok\n") + b"00000000", REFERENCE)
