"""Real native ownership, pre-start failure and descendant cleanup regressions."""

import os
import subprocess
import sys
import time

import psutil
import pytest

from app.self_build.owned_process import owned_process
from tests.native_git import FAMILIES, implementation_path, native_git_executable


def guard():
    return None


def invoke(tmp_path, code, authority=guard):
    return owned_process(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=os.environ.copy(),
        authority_check=authority,
        launch={},
    )


def wait_dead(pid):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                return
        except psutil.NoSuchProcess:
            return
        time.sleep(0.02)
    pytest.fail("owned descendant survived cleanup")


def test_real_owned_process_runs_and_releases_pipes(tmp_path):
    with invoke(tmp_path, "print('measured native output', flush=True)") as process:
        assert process.stdout.readline().strip() == b"measured native output"
        assert process.wait(timeout=5) == 0
    assert process.stdout.closed and process.stderr.closed


def test_context_exit_kills_child_even_after_parent_exits(tmp_path):
    code = (
        "import subprocess, sys; "
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        "print(child.pid, flush=True)"
    )
    with invoke(tmp_path, code) as process:
        child_pid = int(process.stdout.readline())
        assert process.wait(timeout=5) == 0
        assert psutil.pid_exists(child_pid)
    wait_dead(child_pid)


def test_failed_authority_prevents_launch(tmp_path, monkeypatch):
    def revoked():
        raise RuntimeError("revoked")

    def forbidden(*args, **kwargs):
        pytest.fail("authority failure must not create a process")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    with pytest.raises(RuntimeError, match="revoked"):
        with invoke(tmp_path, "print('forbidden')", revoked):
            pytest.fail("not authorized")


def test_exception_kills_owned_operation_without_killing_unrelated_process(tmp_path):
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        with pytest.raises(RuntimeError, match="cancelled"):
            with invoke(tmp_path, "import time; time.sleep(60)") as process:
                owner_pid = process.pid
                raise RuntimeError("cancelled")
        wait_dead(owner_pid)
        assert unrelated.poll() is None
    finally:
        unrelated.kill()
        unrelated.wait(timeout=5)


def test_windows_assignment_failure_never_executes_tool(tmp_path, monkeypatch):
    if os.name != "nt":
        # The real POSIX session ownership behavior is exercised above.
        with invoke(tmp_path, "print('owned', flush=True)") as process:
            assert process.stdout.readline().strip() == b"owned"
        return
    from app.self_build import owned_process as native

    marker = tmp_path / "must-not-exist"

    def rejected(self, handle):
        raise OSError("job assignment unavailable")

    monkeypatch.setattr(native.WindowsJob, "assign", rejected)
    with pytest.raises(OSError, match="job assignment"):
        with invoke(tmp_path, f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')"):
            pytest.fail("uncontained process must stay suspended")
    assert not marker.exists()


@pytest.mark.parametrize("failure", ["revocation", "resume"])
def test_windows_pre_resume_failure_never_runs_tool(tmp_path, monkeypatch, failure):
    if os.name != "nt":
        with invoke(tmp_path, "print('owned', flush=True)") as process:
            assert process.stdout.readline().strip() == b"owned"
        return
    from app.self_build import owned_process as native

    marker = tmp_path / "must-not-exist"
    checks = 0

    def fence():
        nonlocal checks
        checks += 1
        if checks == 2 and failure == "revocation":
            raise RuntimeError("revoked before resume")

    def unavailable(process):
        raise RuntimeError("resume unavailable")

    if failure == "resume":
        monkeypatch.setattr(native, "resume_initial_thread", unavailable)
    with pytest.raises(RuntimeError):
        with invoke(
            tmp_path, f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')", fence
        ):
            pytest.fail("pre-resume failure must prevent execution")
    assert checks == 2 and not marker.exists()


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("entry", ["cmd", "bin", "native"])
def test_native_git_fixture_supports_installation_layouts(tmp_path, family, entry):
    native = tmp_path / family / "bin/git.exe"
    native.parent.mkdir(parents=True)
    native.write_bytes(b"fixture image")
    launcher = native if entry == "native" else tmp_path / entry / "git.exe"
    assert implementation_path(launcher, windows=True) == native


def test_native_git_fixture_refuses_missing_implementation(tmp_path):
    with pytest.raises(AssertionError, match="No supported native Git implementation"):
        implementation_path(tmp_path / "cmd/git.exe", windows=True)


def test_real_git_runs_from_immutable_image_inside_owned_process(tmp_path):
    from hashlib import sha256

    from app.self_build.git_image import pinned_image

    executable = native_git_executable()
    image_hash = sha256(executable.read_bytes()).hexdigest()
    environment = {
        key: os.environ[key] for key in ("SystemRoot", "WINDIR", "TEMP", "TMP") if key in os.environ
    }
    with pinned_image(executable, image_hash) as launch:
        with owned_process(
            [str(executable), "--version"],
            cwd=tmp_path,
            env=environment,
            authority_check=guard,
            launch=launch,
        ) as process:
            output, error = process.communicate(timeout=5)
            assert process.returncode == 0 and not error
            assert output.startswith(b"git version ")
    assert list(tmp_path.iterdir()) == []
