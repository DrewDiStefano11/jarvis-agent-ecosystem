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


# Source mutation native regressions use only these disposable Git fixtures.
def editing_fixture(checkout, operation="modify", filename="hello.txt"):
    from types import SimpleNamespace

    from app.models.development_validation import CodeState
    from app.models.source_change import (
        PlanReferences,
        SourceChange,
        SourceChangeIntent,
        SourceIdentity,
    )
    from app.source_change.planner import observation_identity, plan_source_changes
    from app.source_edit_planning.projection import project_source_state
    from app.source_mutation.driver import SourceMutationDriver, content, observe

    _, policy, observer, creation, owner, _ = checkout
    driver = CheckoutDriver(observer, lambda: None)
    driver.create(policy, creation, owner)
    materialize(driver, policy, creation, owner, acknowledge=lambda *args: None)
    target = Path(policy.worktree_root) / creation.workspace_id
    before = content(b"hello\n", "100644") if operation != "add" else None
    after = content("edited \U0001f600\r\n".encode(), "100644") if operation != "delete" else None
    change = SourceChange(
        operation=operation,
        path=filename,
        before=before.model_dump(exclude={"text"}) if before else None,
        after=after,
    )
    source = SourceIdentity(
        repository_identity=policy.repository_identity,
        head_sha=creation.workspace.base_sha,
        base_sha=creation.workspace.base_sha,
        observation_digest="0" * 64,
    )
    with ExitStack() as stack:
        root = open_directory(stack, target, internal=True)
        observation, _ = observe(
            stack, root, SimpleNamespace(source=source, changes=(change,)), {filename: "100644"}
        )
    plan = plan_source_changes(
        SourceChangeIntent(
            source=observation_identity(observation),
            references=PlanReferences(
                original_plan_hash=creation.workspace.plan_hash,
                authorization_reference="fixture-approval",
            ),
            changes=(change,),
        ),
        observation,
    )
    original = CodeState(
        repository_identity=source.repository_identity,
        head_sha=source.head_sha,
        base_sha=source.base_sha,
        changes=(),
    )
    expected, _ = project_source_state(plan, original)
    journal = dict(completed=0, pending=None, stage=None)

    def begin(index):
        journal["pending"] = index

    def claim(index, value):
        assert journal["pending"] == index
        journal["stage"] = value

    def acknowledge(count, prefix):
        journal.update(completed=count, pending=None, stage=None)

    def finalize(result, guard):
        guard()
        return result

    def apply():
        return SourceMutationDriver(driver).apply(
            policy,
            creation,
            owner,
            plan,
            observation,
            "fixture-operation",
            original_code_state=original,
            expected_code_state=expected,
            completed=journal["completed"],
            pending=journal["pending"],
            staging_identity=journal["stage"],
            claim_stage=claim,
            begin=begin,
            acknowledge=acknowledge,
            finalize=finalize,
        )

    from app.tool_execution.filesystem import windows_native_path

    return Path(windows_native_path(target)), plan, journal, apply


@pytest.mark.parametrize("operation", ["add", "modify", "delete"])
def test_source_mutation_native_add_modify_delete_and_exact_replay(checkout, operation):
    filename = "new.txt" if operation == "add" else "hello.txt"
    target, plan, journal, apply = editing_fixture(checkout, operation, filename)
    before_primary = (checkout[0] / "hello.txt").read_bytes()
    before_index = (checkout[0] / ".git/index").read_bytes()
    result = apply()
    assert journal["completed"] == 1 and apply() == result
    if operation == "delete":
        assert not (target / filename).exists()
    else:
        assert (target / filename).read_bytes() == plan.changes[0].after.text.encode()
    assert (checkout[0] / "hello.txt").read_bytes() == before_primary
    assert (checkout[0] / ".git/index").read_bytes() == before_index


def test_source_mutation_preserves_original_and_recovers_after_rename_crash(checkout, monkeypatch):
    from app.source_mutation import driver as mutation_driver

    target, plan, journal, apply = editing_fixture(checkout)
    native = mutation_driver.rename_pinned
    calls = []

    def interrupted(fd, leaf):
        native(fd, leaf)
        calls.append(leaf)
        if leaf.endswith(".before"):
            raise RuntimeError("crash after original move")

    monkeypatch.setattr(mutation_driver, "rename_pinned", interrupted)
    with pytest.raises(RuntimeError, match="original move"):
        apply()
    assert journal["pending"] == 0 and journal["stage"] is not None
    assert not (target / "hello.txt").exists()
    monkeypatch.setattr(mutation_driver, "rename_pinned", native)
    apply()
    assert (target / "hello.txt").read_bytes() == plan.changes[0].after.text.encode()
    assert next(target.glob("*.before")).read_bytes() == b"hello\n"


def test_source_mutation_recovers_effect_before_file_checkpoint(checkout, monkeypatch):
    from app.source_mutation import driver as mutation_driver

    target, _, journal, apply = editing_fixture(checkout)
    native = mutation_driver.rename_pinned

    def crash_after(fd, leaf):
        native(fd, leaf)
        if leaf == "hello.txt":
            raise RuntimeError("crash after publication")

    monkeypatch.setattr(mutation_driver, "rename_pinned", crash_after)
    with pytest.raises(RuntimeError, match="publication"):
        apply()
    inode = (target / "hello.txt").stat().st_ino
    monkeypatch.setattr(mutation_driver, "rename_pinned", native)
    apply()
    assert journal["completed"] == 1 and (target / "hello.txt").stat().st_ino == inode


def test_source_mutation_pending_add_refuses_unclaimed_published_bytes(checkout, monkeypatch):
    from app.source_mutation.driver import SourceMutationDriver

    target, plan, journal, apply = editing_fixture(checkout, "add", "new.txt")
    native = SourceMutationDriver._file

    def crash_before_stage(*args, **kwargs):
        raise RuntimeError("crash before stage claim")

    monkeypatch.setattr(SourceMutationDriver, "_file", crash_before_stage)
    with pytest.raises(RuntimeError, match="stage claim"):
        apply()
    assert journal["pending"] == 0 and journal["stage"] is None
    destination = target / "new.txt"
    destination.write_bytes(plan.changes[0].after.text.encode())
    foreign_inode = destination.stat().st_ino
    monkeypatch.setattr(SourceMutationDriver, "_file", native)
    with pytest.raises(DomainError) as failure:
        apply()
    assert failure.value.code == "SOURCE_MUTATION_STAGE_CONFLICT"
    assert journal["completed"] == 0
    assert destination.stat().st_ino == foreign_inode


@pytest.mark.parametrize("operation", ["add", "modify"])
def test_source_mutation_pending_publication_refuses_same_bytes_foreign_inode(
    checkout, monkeypatch, operation
):
    from app.source_mutation import driver as mutation_driver

    filename = "new.txt" if operation == "add" else "hello.txt"
    target, plan, journal, apply = editing_fixture(checkout, operation, filename)
    native = mutation_driver.rename_pinned

    def crash_after_publication(fd, leaf):
        native(fd, leaf)
        if leaf == filename:
            raise RuntimeError("crash after publication")

    monkeypatch.setattr(mutation_driver, "rename_pinned", crash_after_publication)
    with pytest.raises(RuntimeError, match="publication"):
        apply()
    destination = target / filename
    owned_inode = destination.stat().st_ino
    replacement = target.parent / "foreign-replacement.txt"
    replacement.write_bytes(plan.changes[0].after.text.encode())
    replacement.replace(destination)
    foreign_inode = destination.stat().st_ino
    assert foreign_inode != owned_inode
    monkeypatch.setattr(mutation_driver, "rename_pinned", native)
    with pytest.raises(DomainError) as failure:
        apply()
    assert failure.value.code == "SOURCE_MUTATION_STAGE_CONFLICT"
    assert journal["completed"] == 0 and journal["stage"][1] == owned_inode
    assert destination.stat().st_ino == foreign_inode
    assert destination.read_bytes() == plan.changes[0].after.text.encode()


@pytest.mark.parametrize("artifact", ["stage", "backup", "target", "sibling"])
def test_source_mutation_never_adopts_foreign_artifacts(checkout, artifact):
    from app.source_mutation.driver import staging_names

    target, _, _, apply = editing_fixture(checkout)
    before, stage = staging_names("fixture-operation", 0)
    path = (
        target
        / {"stage": stage, "backup": before, "target": "hello.txt", "sibling": "foreign.txt"}[
            artifact
        ]
    )
    path.write_bytes(b"foreign bytes")
    with pytest.raises(DomainError):
        apply()
    assert path.read_bytes() == b"foreign bytes"


@pytest.mark.parametrize("prefix", [b"", b"edited"])
def test_source_mutation_does_not_append_to_unclaimed_prefix_stage(checkout, prefix):
    from app.source_mutation.driver import staging_names

    target, _, _, apply = editing_fixture(checkout)
    stage = target / staging_names("fixture-operation", 0)[1]
    stage.write_bytes(prefix)
    with pytest.raises(DomainError):
        apply()
    assert stage.read_bytes() == prefix and (target / "hello.txt").read_bytes() == b"hello\n"


def test_source_mutation_emoji_filename_uses_utf16_units(checkout):
    target, plan, _, apply = editing_fixture(checkout, "add", "\U0001f600\U0001f600.py")
    apply()
    assert (target / "\U0001f600\U0001f600.py").read_bytes() == plan.changes[0].after.text.encode()


def test_source_mutation_same_bytes_foreign_stage_replacement_is_not_published(
    checkout, monkeypatch
):
    from app.source_mutation import driver as mutation_driver

    target, plan, _, apply = editing_fixture(checkout)
    native = mutation_driver.measured
    substituted = []

    def swapped(path, mode):
        from app.tool_execution.filesystem import windows_native_path

        result = native(path, mode)
        if path.name.endswith(".after") and not substituted:
            path = Path(windows_native_path(path))
            path.unlink()
            path.write_bytes(plan.changes[0].after.text.encode())
            substituted.append(path)
        return result

    monkeypatch.setattr(mutation_driver, "measured", swapped)
    with pytest.raises(DomainError) as failure:
        apply()
    assert failure.value.code == "SOURCE_MUTATION_STAGE_CONFLICT"
    assert substituted[0].read_bytes() == plan.changes[0].after.text.encode()


@pytest.mark.parametrize("operation", ["add", "modify"])
@pytest.mark.parametrize("recover", [False, True])
def test_source_mutation_publication_pinned_through_file_acknowledgement(
    checkout, monkeypatch, operation, recover
):
    from app.source_mutation import driver as mutation_driver

    filename = "new.txt" if operation == "add" else "hello.txt"
    target, plan, journal, apply = editing_fixture(checkout, operation, filename)
    if recover:
        rename = mutation_driver.rename_pinned

        def crash(fd, leaf):
            rename(fd, leaf)
            if leaf == filename:
                raise RuntimeError("publication interruption")

        monkeypatch.setattr(mutation_driver, "rename_pinned", crash)
        with pytest.raises(RuntimeError, match="publication interruption"):
            apply()
        monkeypatch.setattr(mutation_driver, "rename_pinned", rename)

    native = mutation_driver.SourceMutationDriver.apply
    checked = []
    replacement = target.parent / "foreign-ack-replacement.txt"
    replacement.write_bytes(plan.changes[0].after.text.encode())

    def guarded(self, *args, **kwargs):
        acknowledge = kwargs["acknowledge"]

        def ack(count, prefix):
            destination = target / filename
            inode = destination.stat().st_ino
            assert journal["stage"][1] == inode
            with pytest.raises(PermissionError):
                replacement.replace(destination)
            with pytest.raises(PermissionError):
                destination.write_bytes(b"foreign")
            acknowledge(count, prefix)
            assert destination.stat().st_ino == inode
            checked.append(True)

        return native(self, *args, **(kwargs | {"acknowledge": ack}))

    monkeypatch.setattr(mutation_driver.SourceMutationDriver, "apply", guarded)
    apply()
    assert checked and journal["completed"] == 1
    assert (target / filename).read_bytes() == plan.changes[0].after.text.encode()


def test_source_mutation_final_publication_holds_deny_write_pins(checkout, monkeypatch):
    from app.source_mutation.driver import SourceMutationDriver

    target, _, _, apply = editing_fixture(checkout)
    native = SourceMutationDriver.apply
    blocked = []

    def checked(self, *args, **kwargs):
        final = kwargs["finalize"]

        def guarded(result, guard):
            with pytest.raises(PermissionError):
                (target / "hello.txt").write_bytes(b"foreign")
            blocked.append(True)
            return final(result, guard)

        return native(self, *args, **(kwargs | {"finalize": guarded}))

    monkeypatch.setattr(SourceMutationDriver, "apply", checked)
    apply()
    assert blocked


def test_source_mutation_foreign_destination_race_never_clobbers(checkout, monkeypatch):
    from app.source_mutation import driver as mutation_driver
    from app.tool_execution.filesystem import windows_native_path

    target, _, _, apply = editing_fixture(checkout)
    native = mutation_driver.rename_pinned

    def raced(fd, leaf):
        native(fd, leaf)
        if leaf.endswith(".before"):
            Path(windows_native_path(target / "hello.txt")).write_bytes(b"foreign destination")

    monkeypatch.setattr(mutation_driver, "rename_pinned", raced)
    with pytest.raises(DomainError):
        apply()
    assert (target / "hello.txt").read_bytes() == b"foreign destination"
    assert next(target.glob("*.before")).read_bytes() == b"hello\n"


def test_source_mutation_verified_owned_partial_stage_resumes(checkout, monkeypatch):
    from app.source_mutation import driver as mutation_driver

    target, plan, journal, apply = editing_fixture(checkout)
    native = mutation_driver.os.write
    crashed = []

    def partial(fd, data):
        if journal["stage"] is not None and not crashed:
            native(fd, data[:3])
            crashed.append(True)
            raise RuntimeError("crash during owned staging")
        return native(fd, data)

    monkeypatch.setattr(mutation_driver.os, "write", partial)
    with pytest.raises(RuntimeError, match="owned staging"):
        apply()
    assert (target / "hello.txt").read_bytes() == b"hello\n"
    monkeypatch.setattr(mutation_driver.os, "write", native)
    apply()
    assert (target / "hello.txt").read_bytes() == plan.changes[0].after.text.encode()


def test_source_mutation_hardlinked_original_is_preserved(checkout):
    import os

    target, _, _, apply = editing_fixture(checkout)
    os.link(target / "hello.txt", target / "other.txt")
    with pytest.raises(DomainError):
        apply()
    assert (target / "hello.txt").read_bytes() == b"hello\n"
    assert (target / "other.txt").read_bytes() == b"hello\n"


def test_source_mutation_foreign_staged_index_is_preserved_and_refused(checkout):
    import subprocess

    target, _, _, apply = editing_fixture(checkout)
    path = target / "hello.txt"
    path.write_bytes(b"staged foreign bytes\n")
    subprocess.run(
        [str(checkout[2].executable), "-C", str(target), "add", "hello.txt"],
        check=True,
        capture_output=True,
    )
    path.write_bytes(b"hello\n")
    with pytest.raises(DomainError) as failure:
        apply()
    assert failure.value.code == "SOURCE_MUTATION_INDEX_CONFLICT"
    assert path.read_bytes() == b"hello\n"


@pytest.mark.parametrize("boundary", ["aggregate", "count"])
def test_source_mutation_projected_whole_workspace_budget_refuses_before_effects(boundary):
    from types import SimpleNamespace

    from app.source_mutation.driver import check_target_budget

    sizes = (
        {"untouched.txt": 67108864}
        if boundary == "aggregate"
        else {str(index): 0 for index in range(4096)}
    )
    changes = (SimpleNamespace(path="new.txt", after=SimpleNamespace(byte_count=1)),)
    with pytest.raises(DomainError) as failure:
        check_target_budget(sizes, changes, 67108864)
    assert failure.value.code == "SOURCE_MUTATION_INVENTORY_LIMIT"
    assert "new.txt" not in sizes


def test_source_mutation_projected_budget_measures_target_not_before_plus_after():
    from types import SimpleNamespace

    from app.source_mutation.driver import check_target_budget

    sizes = {"replace.txt": 10, "delete.txt": 5, "untouched.txt": 5}
    changes = (
        SimpleNamespace(path="replace.txt", after=SimpleNamespace(byte_count=5)),
        SimpleNamespace(path="delete.txt", after=None),
        SimpleNamespace(path="add.txt", after=SimpleNamespace(byte_count=10)),
    )
    check_target_budget(sizes, changes, 20)
    assert sizes == {"replace.txt": 10, "delete.txt": 5, "untouched.txt": 5}


def test_source_mutation_rejects_windows_component_length_in_utf16_units():
    from types import SimpleNamespace

    from app.source_mutation.driver import allowed_changes

    change = SimpleNamespace(path="\U0001f600" * 128 + ".txt", before=None, after=None)
    with pytest.raises(DomainError) as failure:
        allowed_changes(SimpleNamespace(changes=(change,)))
    assert failure.value.code == "SOURCE_MUTATION_PATH_DENIED"


def test_source_mutation_altered_creation_marker_is_preserved_and_refused(checkout):
    target, _, _, apply = editing_fixture(checkout)
    marker = target / ".jarvis-workspace.json"
    marker.write_bytes(b'{"allowedTools":["workspace.write"]}')
    with pytest.raises(DomainError) as failure:
        apply()
    assert failure.value.code == "SOURCE_MUTATION_MARKER_CONFLICT"
    assert marker.read_bytes() == b'{"allowedTools":["workspace.write"]}'
    assert (target / "hello.txt").read_bytes() == b"hello\n"


@pytest.mark.parametrize(
    "paths",
    [
        ("Foo/a", "foo/b"),
        ("Foo", "foo/bar"),
        ("foo/bar", "Foo"),
        ("Foo/a", "Foo/A"),
        ("foo/a", "foo/a"),
        ("foo/bar", "foo"),
    ],
)
def test_inventory_rejects_component_collisions_before_source_effects(paths):
    from types import SimpleNamespace

    entries = [("100644", "a" * 40, path) for path in paths]
    raw = b"".join(f"{mode} blob {blob} 1\t{path}\0".encode() for mode, blob, path in entries)
    driver = SimpleNamespace(invoke=lambda *args: raw)
    plan = SimpleNamespace(
        maximum_file_bytes=10,
        maximum_total_bytes=10,
        file_count=len(paths),
        inventory_digest=digest([list(e) for e in entries]),
    )
    with pytest.raises(DomainError) as failure:
        inventory(driver, None, plan, None)
    assert failure.value.code == "SELF_BUILD_MATERIALIZATION_UNSAFE"


def test_inventory_accepts_shared_parent_with_identical_spelling():
    from types import SimpleNamespace

    entries = [("100644", "a" * 40, path) for path in ("Foo/a", "Foo/b")]
    raw = b"".join(f"{mode} blob {blob} 1\t{path}\0".encode() for mode, blob, path in entries)
    plan = SimpleNamespace(
        maximum_file_bytes=10,
        maximum_total_bytes=10,
        file_count=2,
        inventory_digest=digest([list(e) for e in entries]),
    )
    assert [
        entry[2] for entry in inventory(SimpleNamespace(invoke=lambda *args: raw), None, plan, None)
    ] == ["Foo/a", "Foo/b"]


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
    stage = root / (".jarvis-stage-" + sha256(content).hexdigest())
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


def test_unapproved_empty_directory_is_preserved_without_ready_publication(checkout):
    _, policy, observer, plan, owner, _ = checkout
    driver = CheckoutDriver(observer, lambda: None)
    driver.create(policy, plan, owner)
    target = Path(policy.worktree_root) / plan.workspace_id
    (target / "node_modules").mkdir()
    published = []
    with pytest.raises(DomainError) as failure:
        materialize(
            driver,
            policy,
            plan,
            owner,
            acknowledge=lambda *args: None,
            finalize=lambda *args: published.append(args),
        )
    assert failure.value.code == "SELF_BUILD_MATERIALIZATION_CONFLICT"
    assert (target / "node_modules").is_dir() and not published
