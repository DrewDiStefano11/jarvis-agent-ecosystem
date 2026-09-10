"""Deterministic role scoring and qualification decisions.

Input: role-scoped metrics (:mod:`app.model_qualification.metrics`).
Output: one :class:`RoleAssessment` per role with a qualification level, an
explicit gate table, and deterministic strengths/weaknesses.

Decision order (documented, never averaged into a single number):

1. **insufficient evidence** → ``not_evaluated``. No scored cases, incomplete
   coverage, or a mandatory gate whose metric was never measured. Fails closed.
2. **mandatory gate failed** → ``unqualified``, regardless of the score.
3. **score below the role minimum** → ``unqualified``.
4. **advisory gate failed, or score below the qualified threshold** →
   ``conditional`` (bounded use with stronger review).
5. otherwise → ``qualified``.

The role score is a weighted mean of the policy weights over the metrics that
were actually measured; missing metrics are dropped and the remaining weights
are renormalised, so a role is never punished for a dimension its cases do not
exercise.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.model_qualification.metrics import RoleMetrics
from app.model_qualification.policy import GateRule, policy_for
from app.model_qualification.roles import QualificationRole

#: Strength threshold: how far past a gate a metric must be to count as a strength.
STRENGTH_MARGIN = 0.10

#: Display precision for scores (rounding is display-level only; gates use raw metrics).
SCORE_PRECISION = 6


class QualificationLevel(StrEnum):
    QUALIFIED = "qualified"
    CONDITIONAL = "conditional"
    UNQUALIFIED = "unqualified"
    NOT_EVALUATED = "not_evaluated"


#: Ranking order: lower is better. Never randomised.
LEVEL_RANK: dict[QualificationLevel, int] = {
    QualificationLevel.QUALIFIED: 0,
    QualificationLevel.CONDITIONAL: 1,
    QualificationLevel.UNQUALIFIED: 2,
    QualificationLevel.NOT_EVALUATED: 3,
}


@dataclass(frozen=True)
class GateResult:
    """One policy gate applied to one measured metric."""

    key: str
    metric: str
    direction: str
    threshold: float
    observed: float | None
    passed: bool | None  # None = not evaluated (metric not measured)
    mandatory: bool
    description: str

    @property
    def status(self) -> str:
        if self.passed is None:
            return "not_evaluated"
        return "passed" if self.passed else "failed"

    @property
    def margin(self) -> float | None:
        """Distance past the threshold in the favourable direction (None if unmeasured)."""
        if self.observed is None:
            return None
        return (
            self.observed - self.threshold
            if self.direction == "min"
            else self.threshold - self.observed
        )


@dataclass(frozen=True)
class RoleAssessment:
    """Qualification verdict for one model in one role."""

    role: QualificationRole
    qualification: QualificationLevel
    score: float | None
    mandatory_gates_passed: bool | None
    gates: tuple[GateResult, ...]
    strengths: tuple[str, ...]
    weaknesses: tuple[str, ...]
    reasons: tuple[str, ...]
    evaluated_case_ids: tuple[str, ...]
    expected_case_ids: tuple[str, ...]
    unavailable_case_ids: tuple[str, ...]
    metrics: dict[str, Any]
    operational: dict[str, Any]
    failure_codes: dict[str, int]

    @property
    def level_rank(self) -> int:
        return LEVEL_RANK[self.qualification]


def evaluate_gates(role: QualificationRole, metrics: dict[str, Any]) -> tuple[GateResult, ...]:
    """Apply every policy gate for ``role`` to measured metrics."""
    results: list[GateResult] = []
    for gate in policy_for(role).gates:
        observed = metrics.get(gate.metric)
        observed_value = float(observed) if isinstance(observed, (int, float)) else None
        results.append(
            GateResult(
                key=gate.key,
                metric=gate.metric,
                direction=gate.direction,
                threshold=gate.threshold,
                observed=observed_value,
                passed=_satisfied(gate, observed_value),
                mandatory=gate.mandatory,
                description=gate.description,
            )
        )
    return tuple(results)


def _satisfied(gate: GateRule, observed: float | None) -> bool | None:
    return gate.satisfied_by(observed)


def role_score(role: QualificationRole, metrics: dict[str, Any]) -> float | None:
    """Weighted mean of measured policy metrics (missing metrics dropped)."""
    weights = policy_for(role).weights
    total_weight = 0.0
    total_value = 0.0
    for metric, weight in weights.items():
        value = metrics.get(metric)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        total_weight += weight
        total_value += weight * float(value)
    if total_weight <= 0:
        return None
    return round(total_value / total_weight, SCORE_PRECISION)


def assess_role(role: QualificationRole, role_metrics: RoleMetrics) -> RoleAssessment:
    """Qualify one model for one role from its measured role metrics."""
    metrics = role_metrics.metrics
    policy = policy_for(role)
    gates = evaluate_gates(role, metrics)
    score = role_score(role, metrics)

    mandatory = [gate for gate in gates if gate.mandatory]
    advisory = [gate for gate in gates if not gate.mandatory]
    unmeasured_mandatory = [gate for gate in mandatory if gate.passed is None]
    failed_mandatory = [gate for gate in mandatory if gate.passed is False]
    failed_advisory = [gate for gate in advisory if gate.passed is False]

    reasons: list[str] = []
    if not role_metrics.expected_case_ids:
        qualification = QualificationLevel.NOT_EVALUATED
        reasons.append("no evaluation cases are defined for this role")
    elif role_metrics.fully_unavailable:
        qualification = QualificationLevel.NOT_EVALUATED
        reasons.append(
            "provider unavailable: "
            f"{len(role_metrics.unavailable_case_ids)} case(s) produced no scoreable output"
        )
    elif not role_metrics.complete:
        qualification = QualificationLevel.NOT_EVALUATED
        reasons.append(
            "incomplete evidence: "
            f"{len(role_metrics.evaluated_case_ids)}/{len(role_metrics.expected_case_ids)} "
            "cases scored"
            + (
                f", unavailable={list(role_metrics.unavailable_case_ids)}"
                if role_metrics.unavailable_case_ids
                else ""
            )
            + (
                f", missing={list(role_metrics.missing_case_ids)}"
                if role_metrics.missing_case_ids
                else ""
            )
            + (", run stopped early" if role_metrics.stopped_early else "")
        )
    elif unmeasured_mandatory:
        qualification = QualificationLevel.NOT_EVALUATED
        reasons.append(
            "mandatory gate(s) not measured: "
            + ", ".join(gate.key for gate in unmeasured_mandatory)
        )
    elif score is None:
        qualification = QualificationLevel.NOT_EVALUATED
        reasons.append("no scored metric available to compute a role score")
    elif failed_mandatory:
        qualification = QualificationLevel.UNQUALIFIED
        reasons.append(
            "mandatory gate(s) failed: " + ", ".join(gate.key for gate in failed_mandatory)
        )
    elif score < policy.minimum_score:
        qualification = QualificationLevel.UNQUALIFIED
        reasons.append(f"score {score:.3f} below minimum threshold {policy.minimum_score:.2f}")
    elif failed_advisory or score < policy.qualified_score:
        qualification = QualificationLevel.CONDITIONAL
        if failed_advisory:
            reasons.append(
                "advisory gate(s) failed: " + ", ".join(gate.key for gate in failed_advisory)
            )
        if score < policy.qualified_score:
            reasons.append(
                f"score {score:.3f} below qualified threshold {policy.qualified_score:.2f}"
            )
    else:
        qualification = QualificationLevel.QUALIFIED
        reasons.append(
            "all mandatory and advisory gates passed and score "
            f"{score:.3f} >= {policy.qualified_score:.2f}"
        )

    mandatory_gates_passed: bool | None
    if unmeasured_mandatory or qualification is QualificationLevel.NOT_EVALUATED:
        mandatory_gates_passed = None
    else:
        mandatory_gates_passed = not failed_mandatory

    return RoleAssessment(
        role=role,
        qualification=qualification,
        score=score,
        mandatory_gates_passed=mandatory_gates_passed,
        gates=gates,
        strengths=_strengths(gates, score, policy.qualified_score),
        weaknesses=_weaknesses(gates, failed_advisory, score, policy.qualified_score),
        reasons=tuple(reasons),
        evaluated_case_ids=role_metrics.evaluated_case_ids,
        expected_case_ids=role_metrics.expected_case_ids,
        unavailable_case_ids=role_metrics.unavailable_case_ids,
        metrics=metrics,
        operational=role_metrics.operational,
        failure_codes=role_metrics.failure_codes,
    )


def _strengths(
    gates: tuple[GateResult, ...], score: float | None, qualified_score: float
) -> tuple[str, ...]:
    strengths: list[str] = []
    for gate in gates:
        margin = gate.margin
        if gate.passed is True and margin is not None and margin >= STRENGTH_MARGIN:
            strengths.append(
                f"{gate.metric}={gate.observed:.3f} clears {gate.key} "
                f"({gate.direction} {gate.threshold:.2f}) by {margin:.3f}"
            )
    if score is not None and score >= qualified_score:
        strengths.append(
            f"role_score={score:.3f} reaches qualified threshold {qualified_score:.2f}"
        )
    return tuple(strengths)


def _weaknesses(
    gates: tuple[GateResult, ...],
    failed_advisory: list[GateResult],
    score: float | None,
    qualified_score: float,
) -> tuple[str, ...]:
    weaknesses: list[str] = []
    for gate in gates:
        if gate.passed is False and gate.mandatory:
            weaknesses.append(
                f"MANDATORY {gate.key} failed: {gate.metric}="
                f"{_format_observed(gate.observed)} vs {gate.direction} {gate.threshold:.2f}"
            )
    for gate in failed_advisory:
        weaknesses.append(
            f"advisory {gate.key} failed: {gate.metric}="
            f"{_format_observed(gate.observed)} vs {gate.direction} {gate.threshold:.2f}"
        )
    if score is not None and score < qualified_score:
        weaknesses.append(f"role_score={score:.3f} below qualified threshold {qualified_score:.2f}")
    return tuple(weaknesses)


def _format_observed(value: float | None) -> str:
    return "not measured" if value is None else f"{value:.3f}"


__all__ = [
    "LEVEL_RANK",
    "QualificationLevel",
    "GateResult",
    "RoleAssessment",
    "assess_role",
    "evaluate_gates",
    "role_score",
]
