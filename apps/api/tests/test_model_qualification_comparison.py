"""Deterministic tests for offline model qualification comparison."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.model_qualification.comparison import (  # noqa: E402, I001
    VERDICT_IMPROVED,
    VERDICT_INSUFFICIENT_EVIDENCE,
    VERDICT_NON_COMPARABLE,
    VERDICT_REGRESSED,
    VERDICT_UNCHANGED,
    compare_qualification_artifacts,
    load_qualification_artifact,
    read_comparison_json,
    write_comparison_json,
    write_comparison_markdown,
)
from app.model_qualification.profile import (  # noqa: E402, I001
    FIXTURE_MODE,
    FIXTURE_WARNING,
    INSTALLED_LOCAL_MODE,
    GateRecord,
    ModelProfile,
    RoleRecord,
)
from scripts.compare_model_qualification import (  # noqa: E402, I001
    EXIT_INVALID_INPUT,
    EXIT_OK,
    main as cli_main,
)


def _make_gate(
    key: str = "case_pass_rate_min",
    metric: str = "case_pass_rate",
    mandatory: bool = True,
    passed: bool = True,
    threshold: float = 0.70,
    observed: float = 0.85,
) -> GateRecord:
    return GateRecord(
        key=key,
        metric=metric,
        direction="min",
        threshold=threshold,
        observed=observed,
        passed=passed,
        mandatory=mandatory,
        status="passed" if passed else "failed",
        description="test gate",
    )


def _make_role_record(
    role: str = "manager",
    qualification: str = "qualified",
    score: float = 0.85,
    gates: tuple[GateRecord, ...] | None = None,
    evidence: dict | None = None,
) -> RoleRecord:
    gate_tuple = gates if gates is not None else (_make_gate(),)
    mandatory_passed = all(g.passed for g in gate_tuple if g.mandatory)
    return RoleRecord(
        role=role,
        qualification=qualification,
        score=score,
        mandatory_gates_passed=mandatory_passed,
        gates=gate_tuple,
        strengths=("strong instruction following",),
        weaknesses=(),
        reasons=("all gates passed",),
        evidence=evidence or {"cases": ["c1"], "evaluated_cases": ["c1"], "expected_cases": 1},
    )


def _make_profile(
    model: str = "qwen3:14b",
    provider: str = "ollama",
    inference_mode: str = INSTALLED_LOCAL_MODE,
    policy_version: str = "1.0",
    suite_version: str = "1.0",
    suite_digest: str = "digest_abc123",
    roles: dict[str, RoleRecord] | None = None,
) -> ModelProfile:
    warnings = (FIXTURE_WARNING,) if inference_mode == FIXTURE_MODE else ()
    role_dict = roles if roles is not None else {"manager": _make_role_record()}
    return ModelProfile(
        provider=provider,
        model=model,
        inference_mode=inference_mode,
        status="evaluated",
        evaluated_at=datetime.now(UTC),
        qualification_policy_version=policy_version,
        evaluation_suite_version=suite_version,
        evaluation_suite_digest=suite_digest,
        repo_sha="test_sha",
        roles=role_dict,
        warnings=warnings,
    )


def test_comparison_improvement():
    b_gate = _make_gate(passed=False, observed=0.50)
    t_gate = _make_gate(passed=True, observed=0.85)
    b_role = _make_role_record(qualification="unqualified", score=0.55, gates=(b_gate,))
    t_role = _make_role_record(qualification="qualified", score=0.85, gates=(t_gate,))

    b_prof = _make_profile(roles={"manager": b_role})
    t_prof = _make_profile(roles={"manager": t_role})

    report = compare_qualification_artifacts(b_prof, t_prof)
    assert len(report.comparisons) == 1
    c = report.comparisons[0]
    assert c.verdict == VERDICT_IMPROVED
    assert c.meaningful_score_change is True
    assert c.score_delta == 0.30
    assert "case_pass_rate_min" in c.newly_passing_gates
    assert report.summary[VERDICT_IMPROVED] == 1


def test_comparison_regression():
    b_gate = _make_gate(passed=True, observed=0.90)
    t_gate = _make_gate(passed=False, observed=0.40)
    b_role = _make_role_record(qualification="qualified", score=0.88, gates=(b_gate,))
    t_role = _make_role_record(qualification="unqualified", score=0.45, gates=(t_gate,))

    b_prof = _make_profile(roles={"manager": b_role})
    t_prof = _make_profile(roles={"manager": t_role})

    report = compare_qualification_artifacts(b_prof, t_prof)
    assert len(report.comparisons) == 1
    c = report.comparisons[0]
    assert c.verdict == VERDICT_REGRESSED
    assert c.meaningful_score_change is True
    assert "case_pass_rate_min" in c.previously_passing_failed_gates
    assert report.summary[VERDICT_REGRESSED] == 1


def test_comparison_unchanged():
    role = _make_role_record(qualification="qualified", score=0.85)
    b_prof = _make_profile(roles={"manager": role})
    t_prof = _make_profile(roles={"manager": role})

    report = compare_qualification_artifacts(b_prof, t_prof)
    assert len(report.comparisons) == 1
    c = report.comparisons[0]
    assert c.verdict == VERDICT_UNCHANGED
    assert c.meaningful_score_change is False
    assert c.score_delta == 0.0
    assert report.summary[VERDICT_UNCHANGED] == 1


def test_comparison_missing_role():
    b_prof = _make_profile(
        roles={"manager": _make_role_record("manager"), "planner": _make_role_record("planner")}
    )
    t_prof = _make_profile(roles={"manager": _make_role_record("manager")})

    report = compare_qualification_artifacts(b_prof, t_prof)
    assert len(report.comparisons) == 2
    comp_map = {c.role: c for c in report.comparisons}
    assert comp_map["manager"].verdict == VERDICT_UNCHANGED
    assert comp_map["planner"].verdict == VERDICT_INSUFFICIENT_EVIDENCE
    assert "missing in target report" in comp_map["planner"].reason


def test_comparison_missing_model():
    p1 = _make_profile(model="qwen3:14b")
    p2 = _make_profile(model="llama3:8b")

    report = compare_qualification_artifacts((p1, p2), (p1,))
    comp_map = {c.model: c for c in report.comparisons}
    assert comp_map["qwen3:14b"].verdict == VERDICT_UNCHANGED
    assert comp_map["llama3:8b"].verdict == VERDICT_INSUFFICIENT_EVIDENCE
    assert "missing in target report" in comp_map["llama3:8b"].reason


def test_comparison_fixture_vs_real_mismatch():
    b_prof = _make_profile(inference_mode=FIXTURE_MODE)
    t_prof = _make_profile(inference_mode=INSTALLED_LOCAL_MODE)

    report = compare_qualification_artifacts(b_prof, t_prof)
    assert len(report.comparisons) == 1
    c = report.comparisons[0]
    assert c.comparable is False
    assert c.verdict == VERDICT_NON_COMPARABLE
    assert "mismatched inference modes" in c.reason
    assert c.score_delta is None


def test_comparison_policy_version_mismatch():
    b_prof = _make_profile(policy_version="1.0")
    t_prof = _make_profile(policy_version="2.0")

    report = compare_qualification_artifacts(b_prof, t_prof)
    assert len(report.comparisons) == 1
    c = report.comparisons[0]
    assert c.comparable is False
    assert c.verdict == VERDICT_NON_COMPARABLE
    assert "mismatched qualification policy versions" in c.reason


def test_comparison_suite_digest_mismatch():
    b_prof = _make_profile(suite_digest="sha_a")
    t_prof = _make_profile(suite_digest="sha_b")

    report = compare_qualification_artifacts(b_prof, t_prof)
    assert len(report.comparisons) == 1
    c = report.comparisons[0]
    assert c.comparable is False
    assert c.verdict == VERDICT_NON_COMPARABLE
    assert "mismatched evaluation suite digests" in c.reason


def test_stable_output_ordering():
    p1 = _make_profile(
        model="b_model",
        roles={
            "specialist": _make_role_record("specialist"),
            "decomposer": _make_role_record("decomposer"),
        },
    )
    p2 = _make_profile(model="a_model", roles={"manager": _make_role_record("manager")})

    report = compare_qualification_artifacts((p1, p2), (p1, p2))
    keys = [(c.model, c.provider, c.role) for c in report.comparisons]
    assert keys == sorted(keys)


def test_load_qualification_artifact_file_and_run(tmp_path: Path):
    prof = _make_profile()
    file_path = tmp_path / "profile.json"
    file_path.write_text(json.dumps(prof.model_dump(mode="json")), encoding="utf-8")

    loaded = load_qualification_artifact(file_path)
    assert len(loaded) == 1
    assert loaded[0].model == "qwen3:14b"


def test_load_qualification_artifact_malformed(tmp_path: Path):
    file_path = tmp_path / "bad.json"
    file_path.write_text("{invalid json", encoding="utf-8")

    with pytest.raises(ValueError, match="failed to parse qualification artifact"):
        load_qualification_artifact(file_path)


def test_comparison_report_serialization(tmp_path: Path):
    prof = _make_profile()
    report = compare_qualification_artifacts(prof, prof)

    json_path = tmp_path / "report.json"
    md_path = tmp_path / "report.md"

    write_comparison_json(json_path, report)
    write_comparison_markdown(md_path, report)

    loaded_report = read_comparison_json(json_path)
    assert loaded_report.schema_version == report.schema_version
    assert len(loaded_report.comparisons) == len(report.comparisons)

    md_content = md_path.read_text(encoding="utf-8")
    assert "# Historical AI Model Qualification Comparison" in md_content
    assert "`qwen3:14b`" in md_content


def test_cli_comparison_execution(tmp_path: Path):
    p1 = _make_profile(model="qwen3:14b")
    p2 = _make_profile(
        model="qwen3:14b", roles={"manager": _make_role_record("manager", score=0.95)}
    )

    f1 = tmp_path / "b.json"
    f2 = tmp_path / "t.json"
    out_dir = tmp_path / "out"

    f1.write_text(json.dumps(p1.model_dump(mode="json")), encoding="utf-8")
    f2.write_text(json.dumps(p2.model_dump(mode="json")), encoding="utf-8")

    exit_code = cli_main([str(f1), str(f2), "--out", str(out_dir)])
    assert exit_code == EXIT_OK
    assert (out_dir / "qualification-comparison.json").exists()
    assert (out_dir / "qualification-comparison-summary.md").exists()


def test_cli_invalid_file():
    exit_code = cli_main(["non_existent_b.json", "non_existent_t.json"])
    assert exit_code == EXIT_INVALID_INPUT
