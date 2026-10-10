"""Real local Git metadata, containment and hostile configuration regressions."""

import os
import shutil
import subprocess
from hashlib import sha256
from pathlib import Path

import pytest

from app.core.errors import DomainError
from app.self_build.git_observer import GitObserver, remote_identity
from app.self_build.policy import RepositoryPolicy


@pytest.fixture
def repository(tmp_path):
    executable = shutil.which("git")
    if (
        executable
        and os.name == "nt"
        and (
            Path(executable).parent.name.casefold() == "cmd"
            or (
                Path(executable).parent.name.casefold() == "bin"
                and Path(executable).parent.parent.name.casefold() not in {"mingw32", "mingw64"}
            )
        )
    ):
        installation = Path(executable).parent.parent
        implementations = [
            installation / family / "bin/git.exe" for family in ("mingw64", "mingw32")
        ]
        executable = str(next(path for path in implementations if path.is_file()))
    assert executable, "Git is required for the native repository acceptance tests"
    root, worktrees = tmp_path / "repository", tmp_path / "worktrees"
    root.mkdir()
    worktrees.mkdir()

    def git(*args):
        result = subprocess.run(
            [executable, *args],
            cwd=root,
            check=True,
            capture_output=True,
        )
        return result.stdout.decode().strip()

    git("init", "--initial-branch=main")
    git("config", "user.name", "Isolated fixture")
    git("config", "user.email", "fixture@example.invalid")
    git("config", "commit.gpgsign", "false")
    git("config", "core.hooksPath", os.devnull)
    (root / "hello.txt").write_text("hello\n")
    git("add", "hello.txt")
    git("commit", "-m", "fixture")
    head = git("rev-parse", "HEAD")
    git("remote", "add", "origin", "https://github.com/example/jarvis.git")
    git("update-ref", "refs/remotes/origin/main", head)
    policy = RepositoryPolicy(
        repository_identity="github.com/example/jarvis",
        primary_root=str(root),
        worktree_root=str(worktrees),
    ).checked()
    observer = GitObserver(executable, sha256(Path(executable).read_bytes()).hexdigest())
    return root, policy, observer, head, git


def test_real_read_observation_preserves_primary_dirty_files_and_index(repository):
    root, policy, observer, head, _ = repository
    (root / "hello.txt").write_text("user edit\n")
    index_before = (root / ".git" / "index").read_bytes()
    result = observer.inspect(policy, head)
    assert result["base_sha"] == result["primary_head_sha"] == head
    assert result["base_matches_origin_tracking_ref"] and result["file_count"] == 1
    assert len(result["inventory_digest"]) == 64
    assert (root / "hello.txt").read_text() == "user edit\n"
    assert (root / ".git" / "index").read_bytes() == index_before
    assert str(root) not in str(result)


def test_origin_identity_mismatch_fails_closed(repository):
    _, policy, observer, head, git = repository
    git("remote", "set-url", "origin", "https://github.com/unrelated/repository.git")
    with pytest.raises(DomainError) as error:
        observer.inspect(policy, head)
    assert error.value.code == "SELF_BUILD_GIT_IDENTITY_MISMATCH"


def test_behind_base_is_evidence_not_a_conflict(repository):
    root, policy, observer, base, git = repository
    (root / "new.txt").write_text("new")
    git("add", "new.txt")
    git("commit", "-m", "second")
    newer = git("rev-parse", "HEAD")
    git("update-ref", "refs/remotes/origin/main", newer)
    result = observer.inspect(policy, base)
    assert not result["base_matches_origin_tracking_ref"]
    assert result["origin_tracking_sha"] == newer
    assert result["file_count"] == 1


def test_global_environment_and_config_include_do_not_choose_origin(
    repository, monkeypatch, tmp_path
):
    root, policy, observer, head, git = repository
    injected = tmp_path / "adversarial-config"
    injected.write_text('[remote "origin"]\n url = https://github.com/attacker/repository.git\n')
    git("config", "include.path", str(injected))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(injected))
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "unrelated"))
    result = observer.inspect(policy, head)
    assert result["repository_identity"] == policy.repository_identity


def test_tool_pin_and_unknown_base_fail_closed(repository):
    _, policy, observer, head, _ = repository
    observer.executable_hash = "0" * 64
    with pytest.raises(DomainError) as error:
        observer.inspect(policy, head)
    assert error.value.code == "SELF_BUILD_GIT_TOOL_INVALID"
    with pytest.raises(DomainError):
        observer.inspect(policy, "--evil")


@pytest.mark.parametrize(
    "remote",
    [
        "https://user:secret@github.com/example/jarvis.git",
        "https://github.com/example/jarvis?credential=hidden",
        "git@evil.test:example/jarvis.git",
        "https://github.com/example/jarvis#hidden",
        "https://github.com:444/example/jarvis.git",
    ],
)
def test_credential_or_ambiguous_remote_never_leaks(remote):
    with pytest.raises(DomainError) as error:
        remote_identity(remote)
    assert remote not in error.value.message


@pytest.mark.parametrize(
    "remote",
    [
        "https://github.com/Example/Jarvis.git",
        "git@github.com:Example/Jarvis.git",
        "ssh://git@github.com/Example/Jarvis.git",
    ],
)
def test_canonical_remote_identity(remote):
    assert remote_identity(remote) == "github.com/example/jarvis"


def test_tree_links_and_case_collisions_fail_closed(repository):
    root, policy, observer, _, git = repository
    blob = git("hash-object", "-w", "hello.txt")
    git("update-index", "--add", "--cacheinfo", "120000," + blob + ",alias")
    git("commit", "-m", "unsafe link tree")
    head = git("rev-parse", "HEAD")
    git("update-ref", "refs/remotes/origin/main", head)
    with pytest.raises(DomainError) as failure:
        observer.inspect(policy, head)
    assert failure.value.code == "SELF_BUILD_GIT_TREE_UNSAFE"


def test_tree_inventory_bound_is_measured(repository, monkeypatch):
    from app.self_build import git_observer

    root, policy, observer, _, git = repository
    for name in ("two.txt", "three.txt"):
        (root / name).write_text(name)
        git("add", name)
    git("commit", "-m", "inventory")
    head = git("rev-parse", "HEAD")
    git("update-ref", "refs/remotes/origin/main", head)
    monkeypatch.setattr(git_observer, "MAX_FILES", 2)
    with pytest.raises(DomainError) as failure:
        observer.inspect(policy, head)
    assert failure.value.code == "SELF_BUILD_GIT_TREE_LIMIT"


def test_concurrent_reference_change_is_not_reported_as_coherent(repository, monkeypatch):
    root, policy, observer, head, git = repository
    original = observer._read

    def changing_read(policy, operation, base_sha):
        result = original(policy, operation, base_sha)
        if operation == "inventory":
            (root / "changed.txt").write_text("concurrent change")
            if os.name == "nt":
                with pytest.raises(subprocess.CalledProcessError):
                    git("add", "changed.txt")
                assert git("rev-parse", "HEAD") == head
            else:
                git("add", "changed.txt")
                git("commit", "-m", "concurrent")
        return result

    monkeypatch.setattr(observer, "_read", changing_read)
    with pytest.raises(DomainError) as failure:
        observer.inspect(policy, head)
    assert failure.value.code == "SELF_BUILD_GIT_STATE_CHANGED"


def test_bounded_stdout_and_stderr_never_reach_payload(repository, monkeypatch):
    from app.self_build import git_observer

    _, policy, observer, head, _ = repository
    monkeypatch.setattr(git_observer, "MAX_OUTPUT", 2)
    with pytest.raises(DomainError) as failure:
        observer.inspect(policy, head)
    assert failure.value.code == "SELF_BUILD_GIT_OUTPUT_LIMIT"


def test_linked_primary_git_directory_is_rejected(repository, tmp_path):
    root, policy, observer, head, _ = repository
    metadata = tmp_path / "metadata"
    (root / ".git").rename(metadata)
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(metadata), str(root / ".git"))
    else:
        (root / ".git").symlink_to(metadata, target_is_directory=True)
    with pytest.raises(DomainError):
        observer.inspect(policy, head)


def test_external_git_object_store_is_rejected(repository, tmp_path):
    root, policy, observer, head, _ = repository
    (root / ".git" / "objects" / "info" / "alternates").write_text(
        str(tmp_path / "external-objects")
    )
    with pytest.raises(DomainError) as failure:
        observer.inspect(policy, head)
    assert failure.value.code == "SELF_BUILD_GIT_METADATA_UNSAFE"


def test_native_process_watchdog_uses_inactivity_and_kills_only_its_process(
    repository, monkeypatch
):
    import sys

    from app.self_build import git_observer

    _, policy, observer, head, _ = repository
    native_popen = subprocess.Popen
    children = []

    def controlled_process(*args, **kwargs):
        child = native_popen(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        children.append(child)
        return child

    monkeypatch.setattr(git_observer.subprocess, "Popen", controlled_process)
    observer.inactivity_seconds = 0.15
    with pytest.raises(DomainError) as failure:
        observer._read(policy, "head", head)
    assert failure.value.code == "SELF_BUILD_GIT_INACTIVE"
    assert children[0].poll() is not None


def test_healthy_native_output_can_run_longer_than_inactivity_budget(repository, monkeypatch):
    import sys

    from app.self_build import git_observer

    _, policy, observer, head, _ = repository
    native_popen = subprocess.Popen

    def controlled_process(*args, **kwargs):
        return native_popen(
            [
                sys.executable,
                "-c",
                "import time; [(print('progress', flush=True), time.sleep(0.25)) for _ in range(6)]",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    monkeypatch.setattr(git_observer.subprocess, "Popen", controlled_process)
    observer.inactivity_seconds = 1
    assert observer._read(policy, "head", head).count(b"progress") == 6


def test_large_output_is_rejected_even_when_child_exits_immediately(repository, monkeypatch):
    import sys

    from app.self_build import git_observer

    _, policy, observer, head, _ = repository
    native_popen = subprocess.Popen

    def controlled_process(*args, **kwargs):
        return native_popen(
            [sys.executable, "-c", "print('x' * 100000, flush=True)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    monkeypatch.setattr(git_observer.subprocess, "Popen", controlled_process)
    monkeypatch.setattr(git_observer, "MAX_OUTPUT", 16)
    with pytest.raises(DomainError) as failure:
        observer._read(policy, "head", head)
    assert failure.value.code == "SELF_BUILD_GIT_OUTPUT_LIMIT"


def test_real_repository_dotfiles_are_metadata_not_report_tool_authority(repository):
    from app.tool_execution.filesystem import parts

    root, policy, observer, _, git = repository
    (root / ".gitignore").write_text("node_modules/\n")
    (root / ".env.example").write_text("PUBLIC_OPTION=example\n")
    (root / ".github").mkdir()
    (root / ".github" / "workflow.yml").write_text("name: example\n")
    git("add", ".gitignore", ".env.example", ".github/workflow.yml")
    git("commit", "-m", "ordinary repository metadata")
    head = git("rev-parse", "HEAD")
    git("update-ref", "refs/remotes/origin/main", head)
    assert observer.inspect(policy, head)["file_count"] == 4
    with pytest.raises(DomainError):
        parts(".github/workflow.yml")


@pytest.mark.parametrize("path", [".env", ".aws/config", "data/runtime.sqlite3", "secrets.json"])
def test_committed_credentials_or_runtime_data_fail_inspection(repository, path):
    root, policy, observer, _, git = repository
    file = root / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text("never expose this content")
    git("add", "--force", path)
    git("commit", "-m", "unsafe fixture")
    head = git("rev-parse", "HEAD")
    git("update-ref", "refs/remotes/origin/main", head)
    with pytest.raises(DomainError):
        observer.inspect(policy, head)


def test_replacement_refs_cannot_change_the_approved_base_tree(repository):
    root, policy, observer, base, git = repository
    expected = observer.inspect(policy, base)
    (root / "replacement.txt").write_text("unapproved replacement tree\n")
    git("add", "replacement.txt")
    git("commit", "-m", "replacement fixture")
    replacement = git("rev-parse", "HEAD")
    git("replace", base, replacement)
    assert git("rev-parse", base + "^{tree}") != expected["base_tree_sha"]
    measured = observer.inspect(policy, base)
    assert measured["base_sha"] == base
    assert measured["base_tree_sha"] == expected["base_tree_sha"]
    assert measured["inventory_digest"] == expected["inventory_digest"]
    assert measured["file_count"] == 1


@pytest.mark.parametrize(
    "configured,accepted",
    [
        ("C:/Program Files/Git/cmd/git.exe", False),
        ("C:/Program Files/Git/bin/git.exe", False),
        ("C:/Tools/git.exe", False),
        ("C:/Program Files/Git/mingw64/bin/git.exe", True),
        ("C:/Program Files/Git/mingw32/bin/git.exe", True),
    ],
)
def test_windows_tool_policy_requires_actual_pinned_implementation(configured, accepted):
    from pathlib import PureWindowsPath

    from app.self_build.git_observer import windows_implementation_path

    assert windows_implementation_path(PureWindowsPath(configured)) == accepted


def test_windows_wrapper_configuration_is_rejected_without_starting_it(repository, monkeypatch):
    from app.self_build import git_observer

    _, policy, observer, head, _ = repository

    def forbidden_start(*args, **kwargs):
        pytest.fail("a configured Windows wrapper must not start")

    if os.name == "nt":
        wrapper = observer.executable.parents[2] / "cmd/git.exe"
        assert wrapper.is_file()
        monkeypatch.setattr(git_observer.subprocess, "Popen", forbidden_start)
        with pytest.raises(DomainError) as error:
            GitObserver(str(wrapper), sha256(wrapper.read_bytes()).hexdigest())
        assert error.value.code == "SELF_BUILD_GIT_TOOL_INVALID"
    else:
        # Windows layout policy is exercised above on every platform. Native POSIX
        # direct implementation retains its independently approved content pin.
        assert observer._tool(policy) == str(observer.executable.resolve())


def test_actual_implementation_change_invalidates_its_content_pin(
    repository, tmp_path, monkeypatch
):
    from app.self_build import git_observer

    _, policy, observer, _, _ = repository
    actual = tmp_path / "external-installation/mingw64/bin/git.exe"
    actual.parent.mkdir(parents=True)
    actual.write_bytes(observer.executable.read_bytes())
    pinned = GitObserver(str(actual), sha256(actual.read_bytes()).hexdigest())
    assert pinned._tool(policy) == str(actual.resolve())
    actual.write_bytes(actual.read_bytes() + b"unapproved implementation change")

    def forbidden_start(*args, **kwargs):
        pytest.fail("changed implementation must be rejected before execution")

    monkeypatch.setattr(git_observer.subprocess, "Popen", forbidden_start)
    with pytest.raises(DomainError) as error:
        pinned._read(policy, "head", "a" * 40)
    assert error.value.code == "SELF_BUILD_GIT_TOOL_INVALID"


def test_origin_change_during_inspection_is_not_reported_as_coherent(repository, monkeypatch):
    _, policy, observer, head, git = repository
    native_read = observer._read
    changed = False

    def changing_read(*args):
        nonlocal changed
        result = native_read(*args)
        if args[1] == "remote" and not changed:
            changed = True
            if os.name == "nt":
                with pytest.raises(subprocess.CalledProcessError):
                    git(
                        "remote", "set-url", "origin", "https://github.com/unrelated/repository.git"
                    )
                assert git("config", "remote.origin.url") == "https://github.com/example/jarvis.git"
            else:
                git("remote", "set-url", "origin", "https://github.com/unrelated/repository.git")
        return result

    monkeypatch.setattr(observer, "_read", changing_read)
    with pytest.raises(DomainError) as failure:
        observer.inspect(policy, head)
    assert failure.value.code == "SELF_BUILD_GIT_STATE_CHANGED"


def test_change_between_path_validation_and_image_lock_never_starts_git(
    repository, tmp_path, monkeypatch
):
    from app.self_build import git_observer

    _, policy, observer, head, _ = repository
    actual = tmp_path / "race-installation/mingw64/bin/git.exe"
    actual.parent.mkdir(parents=True)
    actual.write_bytes(observer.executable.read_bytes())
    pinned = GitObserver(str(actual), sha256(actual.read_bytes()).hexdigest())
    native_tool = pinned._tool

    def replace_after_hash(policy):
        result = native_tool(policy)
        actual.write_bytes(b"unapproved executable")
        return result

    def forbidden_start(*args, **kwargs):
        pytest.fail("replacement image must fail before execution")

    monkeypatch.setattr(pinned, "_tool", replace_after_hash)
    monkeypatch.setattr(git_observer.subprocess, "Popen", forbidden_start)
    with pytest.raises(DomainError) as failure:
        pinned._read(policy, "head", head)
    assert failure.value.code == "SELF_BUILD_GIT_TOOL_INVALID"


def test_approved_image_stays_immutable_across_launch_boundary(repository, tmp_path):
    from app.self_build.git_image import pinned_image

    _, _, observer, _, _ = repository
    actual = tmp_path / "locked-installation/bin/git.exe"
    actual.parent.mkdir(parents=True)
    original = observer.executable.read_bytes()
    actual.write_bytes(original)
    replacement = actual.with_name("replacement.exe")
    replacement.write_bytes(b"unapproved executable")
    with pinned_image(actual, sha256(original).hexdigest()) as launch:
        if os.name == "nt":
            with pytest.raises(OSError):
                actual.write_bytes(b"unapproved executable")
            with pytest.raises(OSError):
                os.replace(replacement, actual)
            with pytest.raises(OSError):
                actual.parent.rename(actual.parent.with_name("changed-parent"))
            assert actual.read_bytes() == original
        else:
            import fcntl

            fd = launch["pass_fds"][0]
            with pytest.raises(OSError):
                os.write(fd, b"unapproved executable")
            assert fcntl.fcntl(fd, fcntl.F_GET_SEALS) & fcntl.F_SEAL_WRITE
            os.replace(replacement, actual)
            result = subprocess.run(
                [str(actual), "--version"], **launch, capture_output=True, check=True
            )
            assert result.stdout.startswith(b"git version ")
    # Windows cleanup releases locks; Linux source replacement was harmless.
    actual.write_bytes(b"replacement after operation")


def test_native_read_never_uses_unbounded_path_image_read(repository, monkeypatch):
    _, policy, observer, head, _ = repository

    def forbidden(self):
        pytest.fail("executable bytes must come from a bounded non-following descriptor")

    monkeypatch.setattr(Path, "read_bytes", forbidden)
    assert observer._read(policy, "head", head).decode().strip() == head


@pytest.mark.parametrize("remove_after_read", [False, True])
def test_alternate_store_added_during_read_cannot_supply_approved_objects(
    repository, tmp_path, monkeypatch, remove_after_read
):
    root, policy, observer, head, _ = repository
    external = tmp_path / "outside-approved-repository"
    external.mkdir()
    for bucket in (root / ".git/objects").iterdir():
        if len(bucket.name) == 2 and bucket.is_dir():
            target = external / bucket.name
            target.mkdir()
            for blob in bucket.iterdir():
                blob.replace(target / blob.name)
    alternate = root / ".git/objects/info/alternates"
    native = observer._read_image

    def changed(policy, operation, base, executable, launch):
        alternate.write_bytes(external.as_posix().encode() + b"\n")
        try:
            return native(policy, operation, base, executable, launch)
        finally:
            if remove_after_read:
                alternate.unlink()

    monkeypatch.setattr(observer, "_read_image", changed)
    with pytest.raises(DomainError) as failure:
        observer._read(policy, "base", head)
    assert failure.value.code in {"SELF_BUILD_GIT_METADATA_UNSAFE", "SELF_BUILD_GIT_STATE_CHANGED"}


@pytest.mark.parametrize("directory", [".git", ".git/objects"])
def test_transient_metadata_directory_replacement_is_blocked_or_rejected(
    repository, tmp_path, monkeypatch, directory
):
    root, policy, observer, base, _ = repository
    original = root / directory
    parked = tmp_path / "parked-original"
    external = tmp_path / "external-metadata"
    shutil.copytree(original, external)
    native = observer._read_image
    attempted, blocked = [], []

    def swapped(policy, operation, sha, executable, launch):
        if operation != "base" or attempted:
            return native(policy, operation, sha, executable, launch)
        attempted.append(True)
        try:
            original.rename(parked)
        except PermissionError:
            # Windows inspection handles deny replacement; Linux must detect
            # the real successful swap even after restoring every original name.
            assert os.name == "nt"
            blocked.append(True)
            return native(policy, operation, sha, executable, launch)
        shutil.copytree(external, original)
        try:
            return native(policy, operation, sha, executable, launch)
        finally:
            displaced = tmp_path / "displaced-copy"
            original.rename(displaced)
            parked.rename(original)

    monkeypatch.setattr(observer, "_read_image", swapped)
    if os.name == "nt":
        result = observer.inspect(policy, base)
        assert blocked and result["base_sha"] == base
    else:
        with pytest.raises(DomainError) as failure:
            observer.inspect(policy, base)
        assert failure.value.code == "SELF_BUILD_GIT_STATE_CHANGED"
    assert attempted and original.is_dir() and not parked.exists()


@pytest.mark.parametrize("relative", ["config", "HEAD", "refs/heads/main"])
def test_transient_in_place_metadata_write_is_blocked_or_rejected(
    repository, monkeypatch, relative
):
    root, policy, observer, base, _ = repository
    path = root / ".git" / relative
    original = path.read_bytes()
    original_stat = path.stat()
    native = observer._read_image
    attempted = []

    def changed(policy, operation, sha, executable, launch):
        if operation != "base" or attempted:
            return native(policy, operation, sha, executable, launch)
        attempted.append(True)
        if os.name == "nt":
            with pytest.raises(PermissionError):
                with path.open("r+b") as stream:
                    stream.write(original)
            return native(policy, operation, sha, executable, launch)
        with path.open("r+b") as stream:
            stream.write(b"x" * len(original))
            stream.flush()
            os.fsync(stream.fileno())
            stream.seek(0)
            stream.write(original)
            stream.flush()
            os.fsync(stream.fileno())
        os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        return native(policy, operation, sha, executable, launch)

    monkeypatch.setattr(observer, "_read_image", changed)
    if os.name == "nt":
        assert observer.inspect(policy, base)["base_sha"] == base
    else:
        with pytest.raises(DomainError) as failure:
            observer.inspect(policy, base)
        assert failure.value.code == "SELF_BUILD_GIT_STATE_CHANGED"
    assert attempted and path.read_bytes() == original


def test_bounded_metadata_pins_do_not_exhaust_crt_file_descriptors(repository):
    root, policy, observer, base, _ = repository
    metadata = root / ".git" / "inspection-metadata"
    metadata.mkdir()
    for number in range(700):
        (metadata / f"ordinary-{number}").write_bytes(b"bounded")
    assert observer.inspect(policy, base)["base_sha"] == base
    # Successful post-operation writes verify every native pin was released.
    (metadata / "ordinary-0").write_bytes(b"after inspection")


@pytest.mark.parametrize("relative", ["objects/info/alternates", "refs/remotes/origin/main"])
def test_absent_metadata_name_creation_cannot_be_hidden_by_restored_timestamps(
    repository, monkeypatch, relative
):
    root, policy, observer, base, git = repository
    path = root / ".git" / relative
    if relative.startswith("refs/"):
        git("pack-refs", "--all", "--prune")
        path.parent.mkdir(parents=True, exist_ok=True)
    assert not path.exists()
    before = path.parent.stat()
    native = observer._read_image
    attempted = []

    def changed(policy, operation, sha, executable, launch):
        if operation != ("origin_tip" if relative.startswith("refs/") else "base") or attempted:
            return native(policy, operation, sha, executable, launch)
        attempted.append(True)
        path.write_bytes((base + "\n").encode() if relative.startswith("refs/") else b"")
        try:
            return native(policy, operation, sha, executable, launch)
        finally:
            path.unlink()
            os.utime(path.parent, ns=(before.st_atime_ns, before.st_mtime_ns))

    monkeypatch.setattr(observer, "_read_image", changed)
    with pytest.raises(DomainError) as failure:
        observer.inspect(policy, base)
    assert failure.value.code == "SELF_BUILD_GIT_STATE_CHANGED"
    assert attempted and not path.exists() and path.parent.stat().st_mtime_ns == before.st_mtime_ns
