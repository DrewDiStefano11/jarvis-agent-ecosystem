"""Regression coverage for CI assignment and failure diagnostics."""

import importlib.util
import json
import os
import sys
from pathlib import Path

import psutil
import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("backend_ci", ROOT / "scripts" / "backend_ci.py")
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


def fixture_manifest(tmp_path):
    api = tmp_path / "api"
    (api / "tests").mkdir(parents=True)
    groups = {name: [f"tests/test_{name}.py"] for name in ci.SHARDS}
    for files in groups.values():
        (api / files[0]).write_text("def test_example(): pass", encoding="utf-8")
    manifest = tmp_path / "shards.json"
    manifest.write_text(json.dumps(groups), encoding="utf-8")
    return api, manifest, groups


def test_repository_manifest_covers_every_file_once():
    groups = ci.load_shards()
    assert set(groups) == set(ci.SHARDS)


@pytest.mark.parametrize(
    "change", ["new", "nested", "suffix", "removed", "duplicate", "empty", "group", "escape"]
)
def test_manifest_rejects_omissions_and_duplicates(tmp_path, change):
    api, manifest, groups = fixture_manifest(tmp_path)
    if change == "new":
        (api / "tests/test_new.py").touch()
    elif change == "nested":
        (api / "tests/nested").mkdir()
        (api / "tests/nested/test_new.py").touch()
    elif change == "suffix":
        (api / "tests/new_test.py").touch()
    elif change == "removed":
        (api / groups["runtime"][0]).unlink()
    elif change == "duplicate":
        groups["runtime"].append(groups["system"][0])
    elif change == "empty":
        groups["runtime"].clear()
    elif change == "group":
        groups["unknown"] = groups.pop("runtime")
    elif change == "escape":
        groups["runtime"] = ["../test_runtime.py"]
    manifest.write_text(json.dumps(groups), encoding="utf-8")
    with pytest.raises(ValueError):
        ci.load_shards(api, manifest)


def test_manifest_valid_and_stable(tmp_path):
    api, manifest, groups = fixture_manifest(tmp_path)
    assert ci.load_shards(api, manifest) == groups
    assert ci.load_shards(api, manifest) == groups


def test_migration_gate_stops_at_first_failure(tmp_path, monkeypatch):
    calls = []

    def fail(command, **kwargs):
        calls.append((command, kwargs))
        return 7

    monkeypatch.setattr(ci, "run_command", fail)
    assert ci.migrations(tmp_path, dict(os.environ)) == 7
    assert len(calls) == 1
    assert calls[0][0][-2:] == ["upgrade", "head"]
    assert calls[0][1]["timeout"] == 120
    assert not Path(calls[0][1]["env"]["JARVIS_DATABASE_URL"].removeprefix("sqlite:///")).exists()


def test_migration_gate_preserves_all_phases(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        ci, "run_command", lambda command, **kwargs: calls.append(command[-2:]) or 0
    )
    assert ci.migrations(tmp_path, dict(os.environ)) == 0
    assert calls == [
        ["upgrade", "head"],
        ["alembic", "current"],
        ["alembic", "history"],
        ["downgrade", "20260729_04"],
        ["upgrade", "head"],
    ]


def test_command_preserves_failure_and_durable_output(tmp_path, capsys):
    log = tmp_path / "command.log"
    assert (
        ci.run_command(
            [sys.executable, "-c", "print('diagnostic', flush=True); raise SystemExit(7)"],
            env=dict(os.environ),
            log=log,
            timeout=10,
        )
        == 7
    )
    assert "diagnostic" in log.read_text(encoding="utf-8")
    assert "diagnostic" in capsys.readouterr().out


def test_command_deadline_cleans_its_child_tree(tmp_path):
    pid_path = tmp_path / "child.pid"
    source = "import subprocess,sys,time; from pathlib import Path; child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); Path(sys.argv[1]).write_text(str(child.pid)); time.sleep(60)"
    assert (
        ci.run_command(
            [sys.executable, "-c", source, str(pid_path)],
            env=dict(os.environ),
            log=tmp_path / "hang.log",
            timeout=2,
        )
        == 124
    )
    assert pid_path.exists()
    assert not psutil.pid_exists(int(pid_path.read_text()))


@pytest.mark.parametrize("phase", ["setup", "call", "teardown"])
def test_pytest_watchdog_dumps_stack_and_aborts(tmp_path, phase):
    test = tmp_path / "test_hang.py"
    test.write_text(
        "import threading, pytest\n"
        "@pytest.fixture\n"
        "def fixture():\n"
        + ("    threading.Event().wait()\n" if phase == "setup" else "")
        + "    yield\n"
        + ("    threading.Event().wait()\n" if phase == "teardown" else "")
        + "def test_hang(fixture):\n"
        + ("    threading.Event().wait()\n" if phase == "call" else "    pass\n"),
        encoding="utf-8",
    )
    log = tmp_path / "timeout.log"
    command = [
        sys.executable,
        "-m",
        "pytest",
        str(test),
        "-vv",
        "--timeout=1",
        "--timeout-method=thread",
    ]
    assert (
        ci.run_command(
            command,
            env=dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8"),
            log=log,
            timeout=20,
        )
        != 0
    )
    output = log.read_text(encoding="utf-8")
    assert "test_hang" in output
    assert "Timeout" in output
    assert "Stack of" in output


def test_successful_command_cleans_surviving_child(tmp_path):
    pid_path = tmp_path / "child.pid"
    source = "import subprocess,sys,time; from pathlib import Path; child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); Path(sys.argv[1]).write_text(str(child.pid)); time.sleep(0.6)"
    assert (
        ci.run_command(
            [sys.executable, "-c", source, str(pid_path)],
            env=dict(os.environ),
            log=tmp_path / "success.log",
            timeout=10,
        )
        == 0
    )
    assert not psutil.pid_exists(int(pid_path.read_text()))


def evidence(tmp_path, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    import backend_ci_plugin

    monkeypatch.setenv("BACKEND_CI_ARTIFACTS", str(tmp_path))
    monkeypatch.setenv("BACKEND_CI_SHARD", "runtime")
    plugin = backend_ci_plugin.Evidence()
    return plugin, backend_ci_plugin, SimpleNamespace


def test_phase_evidence_is_durable_before_test_finishes(tmp_path, monkeypatch):
    plugin, _, namespace = evidence(tmp_path, monkeypatch)
    item = namespace(nodeid="tests/test_runtime.py::test_stuck")
    plugin.pytest_runtest_logstart(item.nodeid, None)
    plugin.pytest_runtest_setup(item)
    plugin.pytest_runtest_call(item)
    plugin.pytest_runtest_teardown(item)
    records = [json.loads(line) for line in (tmp_path / "progress.jsonl").read_text().splitlines()]
    assert [row["phase"] for row in records] == [
        "start",
        "setup_start",
        "call_start",
        "teardown_start",
    ]
    assert all(row["nodeid"] == item.nodeid and row["shard"] == "runtime" for row in records)
    plugin = type(plugin)()
    assert (tmp_path / "progress.jsonl").read_text() == ""


@pytest.mark.parametrize("actual", [[], ["tests/test_unassigned.py"]])
def test_collection_guard_rejects_actual_omissions_and_unknown_files(tmp_path, monkeypatch, actual):
    plugin, module, namespace = evidence(tmp_path, monkeypatch)
    monkeypatch.setattr(module, "load_shards", lambda: {"runtime": ["tests/test_expected.py"]})
    session = namespace(items=[namespace(path=ci.API / name) for name in actual])
    with pytest.raises(pytest.UsageError, match="coverage mismatch"):
        plugin.pytest_collection_finish(session)


def test_duration_summary_includes_setup_call_and_teardown(tmp_path, monkeypatch):
    plugin, _, namespace = evidence(tmp_path, monkeypatch)
    for phase in ["setup", "call", "teardown"]:
        plugin.pytest_runtest_logreport(
            namespace(
                nodeid="tests/test_runtime.py::test_example",
                duration=2,
                when=phase,
                outcome="passed",
            )
        )
    plugin.pytest_sessionfinish(None, 0)
    summary = json.loads((tmp_path / "timings.json").read_text())
    assert summary["files"] == {"tests/test_runtime.py": 6}
    assert summary["slowest_tests"] == [["tests/test_runtime.py::test_example", 6]]


@pytest.mark.parametrize(
    "change", [None, "omitted_parameter", "duplicate", "unexpected", "wrong_shard"]
)
def test_exact_node_union_matches_full_collection(tmp_path, monkeypatch, change):
    groups = {shard: [f"tests/test_{shard}.py"] for shard in ci.SHARDS}
    monkeypatch.setattr(ci, "load_shards", lambda: groups)
    nodes = {
        shard: [
            f"tests/test_{shard}.py::test_example[0]",
            f"tests/test_{shard}.py::test_example[1]",
        ]
        for shard in ci.SHARDS
    }
    expected = [node for values in nodes.values() for node in values]
    if change == "omitted_parameter":
        nodes["runtime"].pop()
    elif change == "duplicate":
        nodes["runtime"].append(nodes["runtime"][0])
    elif change == "unexpected":
        nodes["runtime"].append("tests/test_runtime.py::test_unknown")
    elif change == "wrong_shard":
        nodes["runtime"].append(nodes["autonomy"].pop())
    for shard, values in {"collection": expected, **nodes}.items():
        directory = tmp_path / f"backend-{shard}"
        directory.mkdir()
        (directory / "collected.json").write_text(json.dumps(values), encoding="utf-8")
    if change:
        with pytest.raises(ValueError):
            ci.verify_coverage(tmp_path)
    else:
        assert ci.verify_coverage(tmp_path) == 0
