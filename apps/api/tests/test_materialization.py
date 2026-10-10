"""Real native raw materialization, count/byte limits and conflict-preserving replay."""

import subprocess
import sys
from contextlib import ExitStack
from hashlib import sha256
from pathlib import Path

import pytest

from app.core.errors import DomainError
from app.self_build.checkout_driver import CheckoutDriver
from app.self_build.materialization import inventory, materialize, write_file
from app.self_build.policy import digest
from app.tool_execution.filesystem import open_directory
from tests.test_checkout_driver import checkout as checkout
from tests.test_self_build_git_observer import repository as repository


def test_raw_materialization_and_dedicated_index_replay_preserve_primary(checkout):
    root, policy, observer, plan, owner, _ = checkout
    driver = CheckoutDriver(observer, lambda: None)
    driver.create(policy, plan, owner)
    (root / "hello.txt").write_text("user edit\n")
    index = (root / ".git/index").read_bytes()
    acknowledgements = []
    result = materialize(
        driver, policy, plan, owner, acknowledge=lambda *args: acknowledgements.append(args)
    )
    target = Path(policy.worktree_root) / plan.workspace_id
    assert (target / "hello.txt").read_bytes() == b"hello\n"
    assert (target / ".jarvis-workspace.json").is_file()
    assert result["completed_file_count"] == 1 and acknowledgements[0][0] == 1
    assert materialize(driver, policy, plan, owner, acknowledge=lambda *args: None) == result
    assert (root / "hello.txt").read_text() == "user edit\n"
    assert (root / ".git/index").read_bytes() == index


def test_file_acknowledgement_crash_replays_verified_complete_file(checkout):
    _, policy, observer, plan, owner, _ = checkout
    driver = CheckoutDriver(observer, lambda: None)
    driver.create(policy, plan, owner)

    def crash(*args):
        raise RuntimeError("crash after file effect before checkpoint")

    with pytest.raises(RuntimeError, match="before checkpoint"):
        materialize(driver, policy, plan, owner, acknowledge=crash)
    target = Path(policy.worktree_root) / plan.workspace_id
    before = (target / "hello.txt").read_bytes()
    result = materialize(driver, policy, plan, owner, acknowledge=lambda *args: None)
    assert result["completed_file_count"] == 1 and (target / "hello.txt").read_bytes() == before


def test_foreign_existing_source_is_preserved_without_overwrite(checkout):
    _, policy, observer, plan, owner, _ = checkout
    driver = CheckoutDriver(observer, lambda: None)
    driver.create(policy, plan, owner)
    target = Path(policy.worktree_root) / plan.workspace_id
    (target / "hello.txt").write_bytes(b"other!\n")
    with pytest.raises(DomainError):
        materialize(driver, policy, plan, owner, acknowledge=lambda *args: None)
    assert (target / "hello.txt").read_bytes() == b"other!\n"


def test_blob_substitution_is_rejected_before_source_write(checkout, monkeypatch):
    _, policy, observer, plan, owner, _ = checkout
    driver = CheckoutDriver(observer, lambda: None)
    driver.create(policy, plan, owner)
    native = driver.invoke

    def changed(policy, operation, plan, target, **kwargs):
        if operation == "blob":
            return b"wrong\n"
        return native(policy, operation, plan, target, **kwargs)

    monkeypatch.setattr(driver, "invoke", changed)
    with pytest.raises(DomainError) as failure:
        materialize(driver, policy, plan, owner, acknowledge=lambda *args: None)
    assert failure.value.code == "SELF_BUILD_MATERIALIZATION_UNSAFE"
    assert not (Path(policy.worktree_root) / plan.workspace_id / "hello.txt").exists()


def test_sized_inventory_rejects_file_overflow_before_materialization(checkout, monkeypatch):
    _, policy, observer, plan, owner, _ = checkout
    driver = CheckoutDriver(observer, lambda: None)
    driver.create(policy, plan, owner)
    native = driver.invoke

    def too_large(policy, operation, plan, target, **kwargs):
        result = native(policy, operation, plan, target, **kwargs)
        if operation == "inventory":
            header, path = result.split(b"\t", 1)
            mode, kind, blob, _ = header.split()
            return b" ".join([mode, kind, blob, b"8388609"]) + b"\t" + path
        return result

    monkeypatch.setattr(driver, "invoke", too_large)
    with pytest.raises(DomainError) as failure:
        materialize(driver, policy, plan, owner, acknowledge=lambda *args: None)
    assert failure.value.code == "SELF_BUILD_MATERIALIZATION_UNSAFE"
    assert not (Path(policy.worktree_root) / plan.workspace_id / "hello.txt").exists()


def test_corrupt_dedicated_index_cannot_acknowledge_materialization(checkout, monkeypatch):
    _, policy, observer, plan, owner, _ = checkout
    driver = CheckoutDriver(observer, lambda: None)
    driver.create(policy, plan, owner)
    native = driver.invoke

    def changed(policy, operation, plan, target, **kwargs):
        result = native(policy, operation, plan, target, **kwargs)
        return result.replace(b"100644", b"100755") if operation == "index_inventory" else result

    monkeypatch.setattr(driver, "invoke", changed)
    with pytest.raises(DomainError) as failure:
        materialize(driver, policy, plan, owner, acknowledge=lambda *args: None)
    assert failure.value.code == "SELF_BUILD_MATERIALIZATION_UNSAFE"


@pytest.mark.parametrize("tamper", ["source", "unexpected", "marker"])
def test_final_readback_cannot_acknowledge_interleaved_source_changes(
    checkout, monkeypatch, tamper
):
    _, policy, observer, plan, owner, _ = checkout
    driver = CheckoutDriver(observer, lambda: None)
    driver.create(policy, plan, owner)
    native = driver.invoke

    blocked = []

    def changed(policy, operation, plan, target, **kwargs):
        result = native(policy, operation, plan, target, **kwargs)
        if operation == "index_inventory":
            if tamper == "unexpected":
                (target / "foreign.txt").write_text("preserve")
            else:
                path = target / ("hello.txt" if tamper == "source" else ".jarvis-workspace.json")
                with pytest.raises(PermissionError):
                    with path.open("r+b") as stream:
                        stream.write(b"modified")
                blocked.append(True)
        return result

    monkeypatch.setattr(driver, "invoke", changed)
    if tamper == "unexpected":
        with pytest.raises(DomainError) as failure:
            materialize(driver, policy, plan, owner, acknowledge=lambda *args: None)
        assert failure.value.code in {
            "SELF_BUILD_MATERIALIZATION_CONFLICT",
            "SELF_BUILD_GIT_STATE_CHANGED",
        }
    else:
        result = materialize(driver, policy, plan, owner, acknowledge=lambda *args: None)
        assert blocked and result["completed_file_count"] == 1


def test_raw_blobs_never_execute_configured_smudge_filter(checkout, tmp_path):
    root, policy, observer, plan, owner, git = checkout
    marker = tmp_path / "filter-executed.txt"
    script = tmp_path / "configured-filter.py"
    script.write_text(
        "from pathlib import Path; import sys; "
        + f"Path({str(marker)!r}).write_text('executed'); "
        + "sys.stdout.buffer.write(sys.stdin.buffer.read())"
    )
    (root / ".git/info/attributes").write_text("hello.txt filter=danger\n")
    git("config", "filter.danger.smudge", f'"{sys.executable}" "{script}"')
    git("config", "filter.danger.required", "true")
    driver = CheckoutDriver(observer, lambda: None)
    driver.create(policy, plan, owner)
    materialize(driver, policy, plan, owner, acknowledge=lambda *args: None)
    assert not marker.exists()
    assert (Path(policy.worktree_root) / plan.workspace_id / "hello.txt").read_bytes() == b"hello\n"


@pytest.mark.parametrize("corrupt", [False, True])
def test_private_staging_only_resumes_the_verified_content_prefix(tmp_path, corrupt):
    content = b"approved raw blob\n"
    root, private = tmp_path / "target", tmp_path / "private"
    root.mkdir()
    private.mkdir()
    stage = private / ("stage-" + sha256(content).hexdigest())
    stage.write_bytes(b"changed" if corrupt else content[:5])
    driver = CheckoutDriver(None, lambda: None)
    with ExitStack() as stack:
        target_directory = open_directory(stack, root, internal=True)
        staging_directory = open_directory(stack, private, internal=True)
        if corrupt:
            with pytest.raises(DomainError) as failure:
                write_file(driver, target_directory, staging_directory, "file.txt", content)
            assert failure.value.code == "SELF_BUILD_MATERIALIZATION_CONFLICT"
            assert stage.read_bytes() == b"changed" and not (root / "file.txt").exists()
        else:
            write_file(driver, target_directory, staging_directory, "file.txt", content)
            assert (root / "file.txt").read_bytes() == content and not stage.exists()


def test_sized_inventory_accepts_approved_tree_near_unsized_output_limit(checkout):
    root, policy, observer, plan, _, git = checkout
    blob = git("rev-parse", "HEAD:hello.txt")
    names = [f"file-{number:04d}-" + "x" * 189 for number in range(4096)]
    records = b"".join(f"100644 blob {blob}\t{name}\0".encode() for name in names)
    assert len(records) < 1048576
    tree = (
        subprocess.run(
            [str(observer.executable), "mktree", "-z"],
            cwd=root,
            input=records,
            capture_output=True,
            check=True,
        )
        .stdout.decode()
        .strip()
    )
    base = git("commit-tree", tree, "-p", plan.workspace.base_sha, "-m", "bounded inventory")
    observed = observer.inspect(policy, base)
    assert observed["file_count"] == 4096
    plan = plan.model_copy(
        update={
            "workspace": plan.workspace.model_copy(update={"base_sha": base}),
            "base_tree_sha": observed["base_tree_sha"],
            "inventory_digest": observed["inventory_digest"],
            "file_count": observed["file_count"],
        }
    )
    plan = plan.model_copy(
        update={
            "plan_hash": digest(plan.model_dump(mode="json", exclude={"plan_hash"})),
        }
    )
    driver = CheckoutDriver(observer, lambda: None)
    raw = driver.invoke(policy, "inventory", plan, root)
    assert len(raw) > 1048576
    entries = inventory(driver, policy, plan, root)
    assert len(entries) == 4096 and sum(entry[3] for entry in entries) == 24576
