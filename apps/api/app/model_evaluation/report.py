"""Machine-readable evaluation evidence and human-readable summaries.

:class:`EvaluationEvidence` records everything needed to interpret a
local-model evaluation without re-running it: repo SHA, inference identity
(fixture vs. installed-local provider/model), per-case verdicts, aggregated
metrics with exact formulas (see :mod:`app.model_evaluation.runner`), failure
codes, latency, and token usage. Response bodies are never stored — only
redacted expectation details and pass/fail outcomes — so evidence cannot leak
secret-bearing model text.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.autonomy.events import scrub_text
from app.model_evaluation.runner import EvaluationReport

EVALUATION_SCHEMA_VERSION = "1.0"


class EvaluationInference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: str = Field(description="'fixture' or 'installed_local'.")
    provider: str
    model: str


class CaseEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    role: str
    passed: bool
    consistent: bool | None
    failure_codes: tuple[str, ...]
    failed_expectations: tuple[str, ...]
    latency_ms_max: float | None


class EvaluationEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = EVALUATION_SCHEMA_VERSION
    repo_sha: str
    inference: EvaluationInference
    started_at: datetime
    ended_at: datetime
    repetitions: int
    allow_repair: bool
    bounds: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    failure_codes: dict[str, int] = Field(default_factory=dict)
    cases: tuple[CaseEvidence, ...] = ()
    roles: dict[str, dict[str, Any]] = Field(default_factory=dict)


def to_evidence(
    report: EvaluationReport,
    *,
    repo_sha: str,
    started_at: datetime,
    ended_at: datetime,
) -> EvaluationEvidence:
    cases: list[CaseEvidence] = []
    for case in report.cases:
        primary = [attempt for attempt in case.attempts if not attempt.is_repair]
        failed = sorted(
            {
                expectation.name
                for attempt in primary
                for expectation in attempt.expectations
                if not expectation.passed
            }
        )
        latencies = [attempt.latency_ms for attempt in primary]
        cases.append(
            CaseEvidence(
                case_id=case.case_id,
                role=case.role.value,
                passed=case.passed,
                consistent=case.consistent,
                failure_codes=tuple(attempt.failure_code.value for attempt in primary),
                failed_expectations=tuple(failed),
                latency_ms_max=max(latencies) if latencies else None,
            )
        )
    totals: dict[str, int] = {}
    passed_counts: dict[str, int] = {}
    role_cases: dict[str, list[str]] = {}
    for case in report.cases:
        totals[case.role.value] = totals.get(case.role.value, 0) + 1
        passed_counts[case.role.value] = passed_counts.get(case.role.value, 0) + int(case.passed)
        role_cases.setdefault(case.role.value, []).append(case.case_id)
    roles: dict[str, dict[str, Any]] = {}
    for role, total in totals.items():
        roles[role] = {
            "total": total,
            "passed": passed_counts[role],
            "cases": role_cases[role],
            "pass_rate": (passed_counts[role] / total) if total else None,
        }
    return EvaluationEvidence(
        repo_sha=repo_sha,
        inference=EvaluationInference(
            mode=report.inference_mode, provider=report.provider_name, model=report.model_name
        ),
        started_at=started_at,
        ended_at=ended_at,
        repetitions=report.repetitions,
        allow_repair=report.allow_repair,
        bounds={
            "max_calls": report.bounds.max_calls,
            "per_call_timeout_seconds": report.bounds.per_call_timeout_seconds,
            "max_output_chars": report.bounds.max_output_chars,
        },
        metrics={key: _clean_metric(value) for key, value in report.metrics.items()},
        failure_codes=dict(report.failure_codes),
        cases=tuple(cases),
        roles=roles,
    )


def _clean_metric(value: Any) -> Any:
    """Preserve metric types while scrubbing any string content.

    Aggregated metrics are numbers, nulls, and small string-keyed mappings;
    response bodies never reach this layer, but strings are still scrubbed
    defensively so evidence cannot leak secret-bearing model text.
    """
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return scrub_text(value)[:300]
    if isinstance(value, dict):
        return {str(key)[:80]: _clean_metric(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean_metric(item) for item in value][:64]
    return scrub_text(str(value))[:300]


def write_evaluation_json(path: str | Path, evidence: EvaluationEvidence) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(evidence.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target


def read_evaluation_json(path: str | Path) -> EvaluationEvidence:
    return EvaluationEvidence.model_validate_json(Path(path).read_text(encoding="utf-8"))


_METRIC_LABELS = (
    ("case_pass_rate", "case pass rate"),
    ("structured_output_success_rate", "structured-output success"),
    ("schema_validity_rate", "schema validity"),
    ("instruction_following_rate", "instruction following"),
    ("capability_classification_accuracy", "capability classification"),
    ("decomposition_quality", "decomposition quality"),
    ("synthesis_completeness", "synthesis completeness"),
    ("consistency_rate", "consistency"),
    ("hallucination_rate", "hallucination"),
    ("malformed_response_rate", "malformed responses"),
    ("repair_frequency", "repair frequency"),
    ("repair_success_rate", "repair success"),
    ("bounded_instruction_rate", "bounded instructions"),
    ("trust_boundary_rate", "trust boundary"),
    ("secret_pass_rate", "secret hygiene"),
)


def render_evaluation_summary(evidence: EvaluationEvidence) -> str:
    lines = [
        "# Local-model evaluation",
        "",
        f"- repo_sha: `{evidence.repo_sha}`",
        "- inference: "
        f"`{evidence.inference.mode}` provider=`{evidence.inference.provider}` "
        f"model=`{evidence.inference.model}`",
        f"- window: `{evidence.started_at.isoformat()}` → `{evidence.ended_at.isoformat()}`",
        f"- repetitions={evidence.repetitions} allow_repair={evidence.allow_repair}",
        "",
        "## Metrics",
        "",
    ]
    for key, label in _METRIC_LABELS:
        lines.append(f"- {label}: `{evidence.metrics.get(key)}`")
    lines += [
        f"- latency mean/p95/max ms: `{evidence.metrics.get('latency_ms_mean')}` / "
        f"`{evidence.metrics.get('latency_ms_p95')}` / `{evidence.metrics.get('latency_ms_max')}`",
        f"- failure codes: `{evidence.failure_codes}`",
        f"- context sensitivity: `{evidence.metrics.get('context_sensitivity')}`",
        "",
        "## Roles",
        "",
    ]
    for role in sorted(evidence.roles):
        entry = evidence.roles[role]
        lines.append(
            f"- `{role}`: passed={entry['passed']}/{entry['total']} "
            f"pass_rate=`{entry['pass_rate']}`"
        )
    lines += ["", "## Cases", ""]
    for case in evidence.cases:
        mark = "PASS" if case.passed else "FAIL"
        lines.append(
            f"- [{mark}] `{case.case_id}` ({case.role}) failures={list(case.failure_codes)} "
            f"failed_expectations={list(case.failed_expectations)}"
        )
    lines.append("")
    return "\n".join(lines)


def write_evaluation_summary(path: str | Path, evidence: EvaluationEvidence) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_evaluation_summary(evidence), encoding="utf-8")
    return target
