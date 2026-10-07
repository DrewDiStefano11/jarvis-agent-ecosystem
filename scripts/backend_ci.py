"""Serial, isolated backend CI commands. Run from any working directory."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "apps" / "api"
MANIFEST = ROOT / "scripts" / "backend_ci_shards.json"
SHARDS = ("runtime", "autonomy", "models", "system")


def load_shards(api: Path = API, manifest: Path = MANIFEST) -> dict[str, list[str]]:
    groups = json.loads(manifest.read_text(encoding="utf-8"))
    if set(groups) != set(SHARDS):
        raise ValueError(f"Expected shard names: {SHARDS}")
    assigned = [name for group in groups.values() for name in group]
    discovered = {
        path.relative_to(api).as_posix()
        for path in (api / "tests").rglob("*.py")
        if path.name.startswith("test_") or path.name.endswith("_test.py")
    }
    if len(assigned) != len(set(assigned)):
        raise ValueError("Duplicate test file assignments")
    if set(assigned) != discovered:
        raise ValueError(
            f"Shard coverage mismatch: missing={sorted(discovered - set(assigned))}, "
            f"stale={sorted(set(assigned) - discovered)}"
        )
    if any(not names for names in groups.values()):
        raise ValueError("Empty shard")
    return groups


def terminate_descendants(processes: list) -> list:
    import psutil

    terminated = []
    for child in reversed(processes):
        try:
            # is_running checks creation time as well as PID. An old record may
            # now refer to an exited process or a PID recycled by Windows.
            if child.is_running():
                child.kill()
                terminated.append(child)
        except psutil.NoSuchProcess:
            pass
    return terminated


def wait_for_descendants(processes: list, timeout: float = 5) -> None:
    # psutil.wait_procs calls wait() on raw PIDs before checking identity. On
    # Windows an exited/recycled PID can make OpenProcess return AccessDenied.
    # Poll only identity-checked records we actually killed instead.
    deadline = time.monotonic() + timeout
    while alive := [child for child in processes if child.is_running()]:
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"Tracked descendants survived cleanup: {[child.pid for child in alive]}"
            )
        time.sleep(0.05)


def run_command(
    command: list[str], *, env: dict[str, str], log: Path, inactivity_timeout: float
) -> int:
    """Stream a durable log and stop inactive commands, including interpreter shutdown.

    Track only this command's descendants, using psutil's PID-reuse protection.
    Clean up survivors after failure or success; never inspect unrelated runtimes.
    """
    import psutil

    started = time.monotonic()
    last_activity = started
    descendants: dict[int, psutil.Process] = {}
    timed_out = False
    with (
        log.open("w", encoding="utf-8") as output,
        log.open(encoding="utf-8", errors="replace") as reader,
    ):
        process = subprocess.Popen(
            command, cwd=API, env=env, stdout=output, stderr=subprocess.STDOUT
        )
        try:
            parent = psutil.Process(process.pid)
        except psutil.NoSuchProcess:
            parent = None
        try:
            while process.poll() is None:
                try:
                    for child in parent.children(recursive=True) if parent else []:
                        descendants[child.pid] = child
                except psutil.NoSuchProcess:
                    pass
                chunk = reader.read()
                if chunk:
                    last_activity = time.monotonic()
                    print(chunk, end="", flush=True)
                if time.monotonic() - last_activity > inactivity_timeout:
                    timed_out = True
                    print(
                        f"COMMAND INACTIVITY TIMEOUT after {inactivity_timeout}s without output: {command}",
                        flush=True,
                    )
                    break
                time.sleep(0.2)
        finally:
            # The timeout plugin can exit without fixture teardown. Discard this
            # command's process tree instead of resuming tests against its SQLite state.
            if process.poll() is None:
                try:
                    for child in parent.children(recursive=True) if parent else []:
                        descendants[child.pid] = child
                except psutil.NoSuchProcess:
                    pass
            try:
                terminated = terminate_descendants(list(descendants.values()))
            finally:
                # A genuine descendant permission error must still stop the
                # pytest command; never leave it running against abandoned state.
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=10)
            wait_for_descendants(terminated)
            chunk = reader.read()
            if chunk:
                last_activity = time.monotonic()
                print(chunk, end="", flush=True)
    elapsed = time.monotonic() - started
    print(
        f"COMMAND COMPLETE: {elapsed:.2f}s; exit={process.returncode}; timeout={timed_out}",
        flush=True,
    )
    log.with_suffix(".json").write_text(
        json.dumps(
            {
                "command": command,
                "elapsed": elapsed,
                "exitstatus": process.returncode,
                "timed_out": timed_out,
                "timeout_kind": "inactivity" if timed_out else None,
                "inactivity_timeout_seconds": inactivity_timeout,
                "last_output_elapsed": last_activity - started,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return 124 if timed_out else process.returncode


def pytest_command(
    files: list[str], artifacts: Path, *, collect: bool = False
) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "pytest",
        *files,
        "-vv",
        "--durations=50",
        "--timeout=300",
        "--timeout-method=thread",
        "-p",
        "backend_ci_plugin",
        f"--junitxml={artifacts / 'junit.xml'}",
    ]
    if collect:
        command.append("--collect-only")
    return command


def migrations(artifacts: Path, env: dict[str, str]) -> int:
    from sqlalchemy.engine import make_url

    # The database exists only for this invocation and is never cached/uploaded.
    with tempfile.TemporaryDirectory(prefix="jarvis-ci-migrations-") as directory:
        normalized = (Path(directory) / "blank.db").as_posix()
        env = dict(env, JARVIS_DATABASE_URL="sqlite:///" + normalized)
        if make_url(env["JARVIS_DATABASE_URL"]).database != normalized:
            raise ValueError("SQLite URL did not preserve the absolute path")
        for index, args in enumerate(
            [
                ["upgrade", "head"],
                ["current"],
                ["history"],
                ["downgrade", "20260729_04"],
                ["upgrade", "head"],
            ]
        ):
            print(f"MIGRATION PHASE {index}: {' '.join(args)}", flush=True)
            result = run_command(
                [sys.executable, "-m", "alembic", *args],
                env=env,
                log=artifacts / f"migration-{index}.log",
                inactivity_timeout=120,
            )
            if result:
                return result
    return 0


def verify_coverage(artifacts: Path) -> int:
    """Prove the collected node-ID union matches full collection exactly once."""
    expected = json.loads(
        (artifacts / "backend-collection" / "collected.json").read_text(
            encoding="utf-8"
        )
    )
    actual = []
    groups = load_shards()
    for shard in SHARDS:
        nodes = json.loads(
            (artifacts / f"backend-{shard}" / "collected.json").read_text(
                encoding="utf-8"
            )
        )
        if any(node.split("::", 1)[0] not in groups[shard] for node in nodes):
            raise ValueError(f"Unexpected file in {shard} collection artifact")
        actual.extend(nodes)
    if len(actual) != len(set(actual)) or len(expected) != len(set(expected)):
        raise ValueError("Duplicate collected node IDs")
    if set(actual) != set(expected):
        raise ValueError(
            f"Collected node-ID coverage mismatch: missing={sorted(set(expected) - set(actual))}, unexpected={sorted(set(actual) - set(expected))}"
        )
    print(
        f"Full backend coverage verified: {len(actual)} unique collected tests across {len(SHARDS)} shards"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("shard", choices=(*SHARDS, "check", "migrations", "verify"))
    parser.add_argument("--artifacts", type=Path)
    args = parser.parse_args()
    artifacts = (
        args.artifacts or ROOT / ".local" / "backend-ci" / args.shard
    ).resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    if args.shard == "verify":
        return verify_coverage(artifacts)
    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    if args.shard == "migrations":
        return migrations(artifacts, env)
    groups = load_shards()
    env["PYTHONPATH"] = str(ROOT / "scripts") + os.pathsep + env.get("PYTHONPATH", "")
    env["BACKEND_CI_ARTIFACTS"] = str(artifacts)
    env["BACKEND_CI_SHARD"] = args.shard
    files = ["."] if args.shard == "check" else groups[args.shard]
    print(f"SHARD {args.shard}: {len(files)} selection paths", flush=True)
    # Each invocation gets a new temp tree; neither runtime state nor a previous
    # failed test database is reused. Pytest owns isolated per-test subdirectories.
    with tempfile.TemporaryDirectory(prefix=f"jarvis-ci-{args.shard}-") as directory:
        command = pytest_command(files, artifacts, collect=args.shard == "check")
        command.append(f"--basetemp={Path(directory) / 'pytest'}")
        return run_command(
            command, env=env, log=artifacts / "pytest.log", inactivity_timeout=1500
        )


if __name__ == "__main__":
    raise SystemExit(main())
