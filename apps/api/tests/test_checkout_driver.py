"""Real isolated native registration, primary preservation and uncertain replay."""

from pathlib import Path

import pytest

from app.core.errors import DomainError
from app.self_build.checkout_driver import CheckoutDriver
from app.self_build.policy import digest
from tests.test_self_build_git_observer import repository as repository
from tests.test_workspace_creation_contract import plan as shape


@pytest.fixture
def checkout(repository):
    root, policy, observer, base, git = repository
    observation = observer.inspect(policy, base)
    skeleton = shape()
    workspace = skeleton.workspace.model_copy(
        update={"policy_digest": digest(policy.model_dump()), "base_sha": base}
    )
    plan = skeleton.model_copy(
        update={
            "workspace": workspace,
            "base_tree_sha": observation["base_tree_sha"],
            "inventory_digest": observation["inventory_digest"],
            "file_count": observation["file_count"],
            "tool_sha256": observer.executable_hash,
            "tool_identity_digest": digest([str(observer.executable), observer.executable_hash]),
        }
    )
    plan = plan.model_copy(
        update={"plan_hash": digest(plan.model_dump(mode="json", exclude={"plan_hash"}))}
    )
    owner = dict(
        workspaceId=plan.workspace_id,
        operationId="creation-1",
        planHash=plan.plan_hash,
        nonce="c" * 64,
    )
    return root, policy, observer, plan, owner, git


def test_real_no_checkout_registration_replays_and_preserves_primary(checkout):
    root, policy, observer, plan, owner, git = checkout
    (root / "hello.txt").write_text("user edit\n")
    index = (root / ".git/index").read_bytes()
    driver = CheckoutDriver(observer, lambda: None)
    result = driver.create(policy, plan, owner)
    target = Path(policy.worktree_root) / plan.workspace_id
    assert result["base_sha"] == plan.workspace.base_sha
    assert result["branch"] == plan.workspace.branch
    assert set(item.name for item in target.iterdir()) == {".git"}
    assert (root / "hello.txt").read_text() == "user edit\n"
    assert (root / ".git/index").read_bytes() == index
    assert git("rev-parse", "HEAD") == plan.workspace.base_sha
    assert driver.create(policy, plan, owner) == result
    assert len(git("worktree", "list", "--porcelain").split("worktree ")) == 3


def test_foreign_existing_target_is_preserved_and_not_registered(checkout):
    _, policy, observer, plan, owner, git = checkout
    target = Path(policy.worktree_root) / plan.workspace_id
    target.mkdir()
    (target / "important.txt").write_text("another mission")
    with pytest.raises(DomainError) as failure:
        CheckoutDriver(observer, lambda: None).create(policy, plan, owner)
    assert failure.value.code == "SELF_BUILD_OWNERSHIP_CONFLICT"
    assert (target / "important.txt").read_text() == "another mission"
    assert plan.workspace.branch not in git("branch", "--list")


def test_changed_pointer_on_replay_requires_recovery_without_repair(checkout):
    _, policy, observer, plan, owner, _ = checkout
    driver = CheckoutDriver(observer, lambda: None)
    driver.create(policy, plan, owner)
    target = Path(policy.worktree_root) / plan.workspace_id
    pointer = target / ".git"
    with pointer.open("r+b") as stream:
        stream.write(b"gitdir: /foreign/worktree\n")
        stream.truncate()
    with pytest.raises(DomainError) as failure:
        driver.create(policy, plan, owner)
    assert failure.value.code == "SELF_BUILD_GIT_REGISTRATION_INVALID"
    assert pointer.read_text() == "gitdir: /foreign/worktree\n"


def test_existing_generated_branch_is_never_forced_or_replaced(checkout):
    _, policy, observer, plan, owner, git = checkout
    git("branch", plan.workspace.branch, plan.workspace.base_sha)
    with pytest.raises(DomainError) as failure:
        CheckoutDriver(observer, lambda: None).create(policy, plan, owner)
    assert failure.value.code == "SELF_BUILD_GIT_CREATION_UNCERTAIN"
    assert git("rev-parse", plan.workspace.branch) == plan.workspace.base_sha
    assert (
        Path(policy.worktree_root) / (".jarvis-owner-" + plan.workspace_id) / "owner.json"
    ).exists()


def test_live_revocation_at_mutation_boundary_never_creates_checkout(checkout, monkeypatch):
    _, policy, observer, plan, owner, git = checkout
    allowed = [True]

    def authority():
        if not allowed[0]:
            raise DomainError("EMERGENCY_STOP_ACTIVE", "operator stopped creation", 423)

    driver = CheckoutDriver(observer, authority)
    native = driver.invoke

    def revoked(*args):
        allowed[0] = False
        return native(*args)

    monkeypatch.setattr(driver, "invoke", revoked)
    with pytest.raises(DomainError) as failure:
        driver.create(policy, plan, owner)
    assert failure.value.code == "EMERGENCY_STOP_ACTIVE"
    target = Path(policy.worktree_root) / plan.workspace_id
    assert target.is_dir() and not list(target.iterdir())
    assert plan.workspace.branch not in git("branch", "--list")


def test_registration_does_not_execute_configured_checkout_hook(checkout, tmp_path):
    _, policy, observer, plan, owner, git = checkout
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    artifact = tmp_path / "hook-ran"
    hook = hooks / "post-checkout"
    hook.write_text("#!/bin/sh\nprintf ran > '" + artifact.as_posix() + "'\n", newline="\n")
    hook.chmod(0o755)
    git("config", "core.hooksPath", str(hooks))
    CheckoutDriver(observer, lambda: None).create(policy, plan, owner)
    assert not artifact.exists()
    assert not (Path(policy.worktree_root) / plan.workspace_id / "hello.txt").exists()


@pytest.mark.parametrize(
    "scenario,code",
    [
        ("output", "SELF_BUILD_GIT_OUTPUT_LIMIT"),
        ("inactive", "SELF_BUILD_GIT_INACTIVE"),
        ("stop", "EMERGENCY_STOP_ACTIVE"),
    ],
)
def test_supervision_failure_cleans_owned_native_process(checkout, monkeypatch, scenario, code):
    import sys

    import app.self_build.checkout_driver as module
    from app.self_build.owned_process import owned_process

    _, policy, observer, plan, _, _ = checkout
    launched = []
    checked = [0]

    def authority():
        checked[0] += 1
        if scenario == "stop" and launched:
            raise DomainError("EMERGENCY_STOP_ACTIVE", "operator stopped native process", 423)

    script = "import time; time.sleep(30)"
    if scenario == "output":
        script = (
            "import sys,time; sys.stdout.write('x'*2097152); sys.stdout.flush(); time.sleep(30)"
        )
    from contextlib import contextmanager

    @contextmanager
    def fixture_process(argv, **kwargs):
        # A real owned test child exercises the watchdog without permitting any
        # configurable executable or argv in the production driver.
        with owned_process(
            [sys.executable, "-c", script],
            cwd=policy.primary_root,
            env={},
            authority_check=authority,
            launch={},
        ) as process:
            launched.append(process)
            yield process

    monkeypatch.setattr(module, "owned_process", fixture_process)
    if scenario == "inactive":
        observer.inactivity_seconds = 0.1
    driver = CheckoutDriver(observer, authority)
    with pytest.raises(DomainError) as failure:
        driver.invoke(policy, "registrations", plan, Path(policy.worktree_root) / plan.workspace_id)
    assert failure.value.code == code
    assert len(launched) == 1 and launched[0].poll() is not None
    assert launched[0].stdout.closed and launched[0].stderr.closed


def test_mutation_on_unconfined_platform_fails_before_side_effects(checkout, monkeypatch):
    import app.self_build.checkout_driver as module

    _, policy, observer, plan, owner, _ = checkout
    monkeypatch.setattr(module, "supports_mutation", lambda: False)
    called = []
    with pytest.raises(DomainError) as failure:
        CheckoutDriver(observer, lambda: called.append(True)).create(policy, plan, owner)
    assert failure.value.code == "SELF_BUILD_CHECKOUT_PLATFORM_UNAVAILABLE"
    assert not called and not list(Path(policy.worktree_root).iterdir())


def test_target_directory_is_pinned_before_git_mutation(checkout, monkeypatch):
    _, policy, observer, plan, owner, _ = checkout
    driver = CheckoutDriver(observer, lambda: None)
    native = driver.invoke

    def attempt_swap(policy, operation, plan, target):
        if operation == "create":
            with pytest.raises(PermissionError):
                target.rename(target.with_name("foreign-target"))
        return native(policy, operation, plan, target)

    monkeypatch.setattr(driver, "invoke", attempt_swap)
    result = driver.create(policy, plan, owner)
    assert result["base_sha"] == plan.workspace.base_sha


def test_transient_alternates_are_rejected_during_fixed_mutation_command(checkout, monkeypatch):
    import os

    root, policy, observer, plan, owner, _ = checkout
    driver = CheckoutDriver(observer, lambda: None)
    driver.create(policy, plan, owner)
    original = driver._invoke
    path = root / ".git/objects/info/alternates"
    before = path.parent.stat()

    def changed(policy, operation, plan, target, **kwargs):
        path.write_bytes(b"")
        try:
            return original(policy, operation, plan, target, **kwargs)
        finally:
            path.unlink()
            os.utime(path.parent, ns=(before.st_atime_ns, before.st_mtime_ns))

    monkeypatch.setattr(driver, "_invoke", changed)
    with pytest.raises(DomainError) as failure:
        driver.invoke(policy, "index", plan, Path(policy.worktree_root) / plan.workspace_id)
    assert failure.value.code == "SELF_BUILD_GIT_STATE_CHANGED"
    assert not path.exists()
