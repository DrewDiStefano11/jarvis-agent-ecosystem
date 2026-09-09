"""Role-scoped metrics derived from a PR #64 evaluation report.

One evaluation run produces evidence for several roles at once: this module
projects the run onto one :class:`~app.model_qualification.roles.QualificationRole`
using PR #64's own aggregation formulas
(:func:`app.model_evaluation.runner.aggregate_metrics_for_cases`), then adds the
few role-specific derived metrics the policy needs.

Two rules keep evidence honest:

- **Unavailable is not bad quality.** A case whose every attempt failed at the
  call level (provider error, timeout) is *unavailable*, not scored. It is
  excluded from rates and reported separately, so an offline provider never
  becomes a quality score of zero.
- **Missing evidence fails closed.** If fewer cases were scored than the role
  requires, the metrics are marked incomplete and the role can only be
  ``not_evaluated``.

Quality metrics and operational signals (latency, counts, tokens) are kept in
separate mappings: a slower model is not a worse model.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from app.model_evaluation.cases import all_cases
from app.model_evaluation.runner import (
    AttemptRecord,
    CaseEvaluation,
    EvalFailureCode,
    EvaluationReport,
    aggregate_metrics_for_cases,
    context_sensitivity_for_cases,
)
from app.model_qualification.roles import QualificationRole, expected_case_ids

#: Attempts that failed before any model output could be scored. These are
#: availability signals, never quality signals.
CALL_LEVEL_FAILURES = frozenset(
    {EvalFailureCode.PROVIDER_ERROR, EvalFailureCode.TIMEOUT, EvalFailureCode.CALL_BUDGET_EXCEEDED}
)

#: Catalog version for the evaluation suite used by qualification. Bump when the
#: case catalog or the derived-metric definitions change.
EVALUATION_SUITE_VERSION = "1.1"

#: Metrics that describe cost/behaviour rather than quality.
OPERATIONAL_METRICS = (
    "total_calls",
    "request_count",
    "repair_count",
    "malformed_response_count",
    "latency_ms_mean",
    "latency_ms_p95",
    "latency_ms_max",
    "input_tokens_total",
    "output_tokens_total",
    "tokens_reported",
)


def evaluation_suite_digest(cases: tuple[Any, ...] | None = None) -> str:
    """Stable short digest of the exact case catalog used for a run."""
    catalog = cases if cases is not None else all_cases()
    payload = "|".join(f"{case.case_id}:{case.role.value}" for case in catalog)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class RoleMetrics:
    """Everything the policy needs to qualify one model for one role."""

    role: QualificationRole
    expected_case_ids: tuple[str, ...]
    evaluated_case_ids: tuple[str, ...]
    unavailable_case_ids: tuple[str, ...]
    missing_case_ids: tuple[str, ...]
    metrics: dict[str, Any]
    operational: dict[str, Any]
    failure_codes: dict[str, int]
    context_sensitivity: dict[str, int]
    stopped_early: bool

    @property
    def complete(self) -> bool:
        """True when every expected case produced at least one scored attempt."""
        return (
            not self.missing_case_ids
            and not self.unavailable_case_ids
            and self.evaluated_case_ids == self.expected_case_ids
            and not self.stopped_early
        )

    @property
    def fully_unavailable(self) -> bool:
        return not self.evaluated_case_ids and bool(self.unavailable_case_ids)


def _primary_attempts(case: CaseEvaluation) -> list[AttemptRecord]:
    return [attempt for attempt in case.attempts if not attempt.is_repair]


def _scored(case: CaseEvaluation) -> bool:
    """True when at least one attempt produced scoreable model output."""
    return any(
        attempt.failure_code not in CALL_LEVEL_FAILURES for attempt in _primary_attempts(case)
    )


def _role_failure_codes(cases: tuple[CaseEvaluation, ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for case in cases:
        for attempt in case.attempts:
            key = attempt.failure_code.value
            counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _category_scores(cases: tuple[CaseEvaluation, ...], category: str) -> list[float]:
    scores: list[float] = []
    for case in cases:
        for attempt in _primary_attempts(case):
            for expectation in attempt.expectations:
                if expectation.category == category and expectation.score is not None:
                    scores.append(expectation.score)
    return scores


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _context_degradation(
    cases: tuple[CaseEvaluation, ...], sensitivity: dict[str, int]
) -> float | None:
    """Share of paired context cases whose verdict degraded with a larger context.

    Only pairs where *both* the small and large variant were scored count; a
    missing variant is not evidence of degradation.
    """
    evaluated = {case.case_id for case in cases}
    deltas: list[int] = []
    for case in cases:
        if case.context_variant_of is None:
            continue
        if case.case_id not in evaluated or case.context_variant_of not in evaluated:
            continue
        if case.case_id in sensitivity:
            deltas.append(sensitivity[case.case_id])
    if not deltas:
        return None
    return sum(1 for delta in deltas if delta > 0) / len(deltas)


def _operational(cases: tuple[CaseEvaluation, ...], base: dict[str, Any]) -> dict[str, Any]:
    attempts = [attempt for case in cases for attempt in case.attempts]
    return {
        "total_calls": len(attempts),
        "request_count": len(attempts),
        "repair_count": sum(1 for attempt in attempts if attempt.is_repair),
        "malformed_response_count": sum(
            1
            for attempt in attempts
            if attempt.failure_code == EvalFailureCode.JSON_PARSE_ERROR
            or attempt.parsed_ok is False
        ),
        "latency_ms_mean": base.get("latency_ms_mean"),
        "latency_ms_p95": base.get("latency_ms_p95"),
        "latency_ms_max": base.get("latency_ms_max"),
        "input_tokens_total": base.get("input_tokens_total"),
        "output_tokens_total": base.get("output_tokens_total"),
        "tokens_reported": bool(base.get("tokens_reported")),
    }


def role_metrics(
    report: EvaluationReport,
    role: QualificationRole,
    *,
    cases: tuple[Any, ...] | None = None,
) -> RoleMetrics:
    """Project ``report`` onto ``role``: rates, derived metrics, and coverage."""
    expected = expected_case_ids(role, cases)
    by_id = {case.case_id: case for case in report.cases}
    evaluated_cases: list[CaseEvaluation] = []
    unavailable: list[str] = []
    for case_id in expected:
        case = by_id.get(case_id)
        if case is None:
            continue
        if _scored(case):
            evaluated_cases.append(case)
        else:
            unavailable.append(case_id)
    evaluated_ids = tuple(case.case_id for case in evaluated_cases)
    missing = tuple(case_id for case_id in expected if case_id not in by_id)

    base = aggregate_metrics_for_cases(evaluated_cases)
    sensitivity = context_sensitivity_for_cases(evaluated_cases)
    metrics: dict[str, Any] = dict(base)
    metrics["context_sensitivity"] = sensitivity
    metrics["context_degradation_rate"] = _context_degradation(evaluated_cases, sensitivity)
    metrics["review_defect_quality"] = _mean(_category_scores(evaluated_cases, "review"))
    metrics["evaluated_case_count"] = len(evaluated_cases)
    metrics["expected_case_count"] = len(expected)
    metrics["unavailable_case_count"] = len(unavailable)

    return RoleMetrics(
        role=role,
        expected_case_ids=expected,
        evaluated_case_ids=evaluated_ids,
        unavailable_case_ids=tuple(unavailable),
        missing_case_ids=missing,
        metrics=metrics,
        operational=_operational(evaluated_cases, base),
        failure_codes=_role_failure_codes(tuple(evaluated_cases)),
        context_sensitivity=sensitivity,
        stopped_early=report.stopped_early is not None,
    )


__all__ = [
    "CALL_LEVEL_FAILURES",
    "EVALUATION_SUITE_VERSION",
    "OPERATIONAL_METRICS",
    "RoleMetrics",
    "evaluation_suite_digest",
    "role_metrics",
]
