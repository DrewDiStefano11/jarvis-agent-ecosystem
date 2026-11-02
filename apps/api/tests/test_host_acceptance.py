"""Acceptance runner controls; fixtures never establish production readiness."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "host_acceptance", ROOT / "scripts/permanent-host-acceptance.py"
)
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)


def test_offline_default_never_executes_a_model_or_process(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("offline preparation must not execute a command")

    monkeypatch.setattr(host.subprocess, "run", forbidden)
    assert host.main(["--out", str(tmp_path)]) == 2
    report = json.loads(next(tmp_path.glob("*/acceptance.json")).read_text())
    assert report["production_ready"] is False
    assert report["overall"] == "NOT TESTED"
    assert {row["id"] for row in report["criteria"]} == set("ABCDEF")
    assert all(row["status"] == "NOT TESTED" for row in report["criteria"])
    assert report["checks"] == []


@pytest.mark.parametrize(
    "arguments",
    [
        ["--local-model"],
        ["--task-id", "task"],
        ["--worker-id", "worker"],
        ["--timeout-seconds", "0"],
        ["--timeout-seconds", "nan"],
        ["--timeout-seconds", "inf"],
        ["--timeout-seconds", "601"],
    ],
)
def test_invalid_host_options_do_not_run_any_checks(tmp_path, monkeypatch, arguments):
    monkeypatch.setattr(host, "run_check", lambda *a, **kw: pytest.fail("invalid options executed"))
    with pytest.raises(SystemExit) as exc:
        host.main(["--out", str(tmp_path), *arguments])
    assert exc.value.code == 2
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "exit_code,status", [(0, "PASS"), (1, "FAIL"), (2, "BLOCKED"), (17, "FAIL")]
)
def test_process_status_mapping(tmp_path, exit_code, status):
    # Real bounded process verifies exit propagation and log handling.
    check = host.run_check("exit", ["-c", f"raise SystemExit({exit_code})"], tmp_path, 10)
    assert check["status"] == status
    assert check["exit_code"] == exit_code
    assert (tmp_path / check["log"]).is_file()


def test_process_deadline_is_a_failure(tmp_path):
    check = host.run_check("timeout", ["-c", "import time; time.sleep(10)"], tmp_path, 0.05)
    assert check["status"] == "FAIL"
    assert check["reason"] == "deadline_exceeded"


@pytest.mark.parametrize(
    "invalid", ["fixture", "empty", "failed", "omitted", "duplicate", "wrong_model", "missing"]
)
def test_invalid_live_evidence_cannot_pass(tmp_path, invalid):
    from app.model_evaluation.cases import all_cases

    evidence = {
        "inference": {"mode": "installed_local", "provider": "ollama", "model": "installed"},
        "cases": [{"case_id": case.case_id, "passed": True} for case in all_cases()],
    }
    if invalid == "fixture":
        evidence["inference"]["mode"] = "fixture"
    elif invalid == "empty":
        evidence["cases"] = []
    elif invalid == "failed":
        evidence["cases"][0]["passed"] = False
    elif invalid == "omitted":
        evidence["cases"].pop()
    elif invalid == "duplicate":
        evidence["cases"][-1] = evidence["cases"][0]
    elif invalid == "wrong_model":
        evidence["inference"]["model"] = "other"
    path = tmp_path / "model/model-evaluation-local.json"
    path.parent.mkdir()
    if invalid != "missing":
        path.write_text(json.dumps(evidence), encoding="utf-8")
    check = host.validate_model_evidence(
        {"name": "live", "status": "PASS"}, path, provider="ollama", model="installed"
    )
    assert check["status"] == "FAIL"


def test_nonpassing_preflight_does_not_start_authorized_task(tmp_path, monkeypatch):
    calls = []

    def blocked(name, *args):
        calls.append(name)
        return {"name": name, "status": "BLOCKED"}

    monkeypatch.setattr(host, "run_check", blocked)
    assert (
        host.main(
            [
                "--out",
                str(tmp_path),
                "--local-model",
                "--model",
                "installed",
                "--task-id",
                "task",
                "--worker-id",
                "worker",
            ]
        )
        == 2
    )
    assert calls == ["installed-local-evaluation"]
    report = json.loads(next(tmp_path.glob("*/acceptance.json")).read_text())
    assert report["checks"][-1]["status"] == "BLOCKED"


def test_previous_evidence_cannot_satisfy_new_run(tmp_path):
    assert host.main(["--out", str(tmp_path)]) == 2
    first = next(tmp_path.glob("*/acceptance.json"))
    first.write_text('{"production_ready":true}')
    assert host.main(["--out", str(tmp_path)]) == 2
    reports = list(tmp_path.glob("*/acceptance.json"))
    assert len(reports) == 2
    fresh = next(path for path in reports if path != first)
    assert json.loads(fresh.read_text())["production_ready"] is False


@pytest.mark.parametrize(
    "option,name",
    [("--discover", "installed-model-discovery"), ("--runtime-diagnostics", "runtime-doctor")],
)
def test_explicit_read_only_host_checks_are_dispatched(tmp_path, monkeypatch, option, name):
    calls = []

    def record(check_name, arguments, out, timeout):
        calls.append((check_name, arguments, timeout))
        return {"name": check_name, "status": "PASS"}

    monkeypatch.setattr(host, "run_check", record)
    assert host.main(["--out", str(tmp_path), option]) == 2
    assert calls[0][0] == name
    assert calls[0][1][0].startswith(str(ROOT / "scripts"))
    assert calls[0][2] <= 120


@pytest.mark.parametrize("status,expected_exit", [("FAIL", 1), ("BLOCKED", 2)])
def test_report_surfaces_failed_or_blocked_checks(tmp_path, monkeypatch, status, expected_exit):
    monkeypatch.setattr(host, "run_check", lambda name, *args: {"name": name, "status": status})
    assert host.main(["--out", str(tmp_path), "--fixture-controls"]) == expected_exit
    report = json.loads(next(tmp_path.glob("*/acceptance.json")).read_text())
    assert report["overall"] == status
    assert report["production_ready"] is False


@pytest.mark.parametrize(
    "state,reason,exit_code,status",
    [
        ("in_progress", "active", 1, "BLOCKED"),
        ("queued", "planning_pending", 1, "BLOCKED"),
        ("blocked", "COORDINATION_AUTHORIZATION_REVOKED", 1, "BLOCKED"),
        ("in_progress", "failed", 1, "FAIL"),
        ("completed", "inference_identity_mismatch", 1, "FAIL"),
        ("failed", "failed", 1, "FAIL"),
    ],
)
def test_production_evidence_preserves_pending_and_real_failure(
    tmp_path, state, reason, exit_code, status
):
    from datetime import UTC, datetime

    from app.autonomy.evidence import AcceptanceEvidence, EvidenceIds, InferenceIdentity

    evidence = AcceptanceEvidence(
        repo_sha="a" * 40,
        scenario="production_golden_path",
        inference=InferenceIdentity(mode="installed_local", provider="ollama", model="installed"),
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
        verdict="fail",
        terminal_state=state,
        failure_reason=reason,
        ids=EvidenceIds(task_ids=("task",)),
    )
    path = tmp_path / "production-autonomy.json"
    path.write_text(evidence.model_dump_json(), encoding="utf-8")
    result = host.validate_production_evidence(
        {"name": "production", "status": "FAIL", "exit_code": exit_code},
        path,
        provider="ollama",
        model="installed",
        task_id="task",
    )
    assert result["status"] == status


def test_production_malformed_evidence_cannot_turn_failure_into_blocked(tmp_path):
    path = tmp_path / "production-autonomy.json"
    path.write_text('{"terminal_state":"blocked"}', encoding="utf-8")
    result = host.validate_production_evidence(
        {"name": "production", "status": "FAIL", "exit_code": 1},
        path,
        provider="ollama",
        model="installed",
        task_id="task",
    )
    assert result["status"] == "FAIL"


@pytest.mark.parametrize(
    "change,expected",
    [
        (None, "PASS"),
        ("fixture", "FAIL"),
        ("wrong_task", "FAIL"),
        ("no_checks", "FAIL"),
        ("no_result", "FAIL"),
    ],
)
def test_completed_production_requires_matching_live_evidence(tmp_path, change, expected):
    from datetime import UTC, datetime

    from app.autonomy.evidence import (
        AcceptanceCheck,
        AcceptanceEvidence,
        EvidenceIds,
        InferenceIdentity,
    )

    evidence = AcceptanceEvidence(
        repo_sha="a" * 40,
        scenario="production_golden_path",
        inference=InferenceIdentity(
            mode="fixture" if change == "fixture" else "installed_local",
            provider="ollama",
            model="installed",
        ),
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
        verdict="pass",
        terminal_state="completed",
        ids=EvidenceIds(task_ids=("other" if change == "wrong_task" else "task",)),
        checks=()
        if change == "no_checks"
        else (
            AcceptanceCheck(
                name="completion", expected="completed", actual="completed", passed=True
            ),
        ),
        final_result="" if change == "no_result" else "Validated result",
    )
    path = tmp_path / "production-autonomy.json"
    path.write_text(evidence.model_dump_json(), encoding="utf-8")
    result = host.validate_production_evidence(
        {"name": "production", "status": "PASS", "exit_code": 0},
        path,
        provider="ollama",
        model="installed",
        task_id="task",
    )
    assert result["status"] == expected
