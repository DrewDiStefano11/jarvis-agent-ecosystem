"""Jarvis qualification policy: explicit, versioned, machine-readable gates.

This is **Jarvis qualification policy**, not a scientific result. Thresholds
are engineering choices that are:

- explicit — every gate names its metric, direction, and threshold;
- documented — ``policy_document()`` emits the whole policy as JSON;
- versioned — :data:`QUALIFICATION_POLICY_VERSION` is recorded in every
  profile, and changing a threshold requires bumping it;
- testable — every gate is exercised by deterministic fixture tests.

They are not claims about model quality in general: they encode the minimum
behaviour Jarvis requires before a model may be *recommended* for a role.

Two gate kinds exist:

- **mandatory** gates are hard. Failing one means ``unqualified`` no matter how
  high the aggregate score is. A decomposer that emits invalid dependency
  graphs must never be qualified for writing good prose.
- **advisory** gates describe weaknesses that permit bounded use with review.
  Failing one caps the result at ``conditional``.

A gate whose metric was not measured (``observed is None``) is reported as
``not_evaluated``. A *mandatory* gate that was not measured fails closed: the
role becomes ``not_evaluated``, never ``qualified``. Consequently every
mandatory gate below is measurable with the default run configuration
(one repetition, no repairs), while advisory gates may depend on optional run
settings such as repetitions or repair attempts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.model_qualification.roles import (
    QualificationRole,
    RoleContract,
    all_roles,
    role_contract,
)

QUALIFICATION_POLICY_VERSION = "1.0"

POLICY_NOTE = (
    "Jarvis qualification policy: explicit engineering thresholds for bounded "
    "local-model role qualification. Thresholds are versioned policy, not "
    "scientific measurements, and must not be read as general model rankings."
)

MIN = "min"
MAX = "max"


@dataclass(frozen=True)
class GateRule:
    """One threshold applied to one role metric."""

    key: str
    metric: str
    direction: str  # MIN ("min") or MAX ("max")
    threshold: float
    mandatory: bool
    description: str

    def satisfied_by(self, observed: float | None) -> bool | None:
        """``True`` passes, ``False`` fails, ``None`` = metric not measured."""
        if observed is None:
            return None
        if self.direction == MIN:
            return observed >= self.threshold
        return observed <= self.threshold


@dataclass(frozen=True)
class RolePolicy:
    """Gates and scoring weights for one qualification role."""

    role: QualificationRole
    gates: tuple[GateRule, ...]
    weights: dict[str, float]
    minimum_score: float
    qualified_score: float

    @property
    def mandatory_gates(self) -> tuple[GateRule, ...]:
        return tuple(gate for gate in self.gates if gate.mandatory)

    @property
    def advisory_gates(self) -> tuple[GateRule, ...]:
        return tuple(gate for gate in self.gates if not gate.mandatory)


def _gate(
    key: str,
    metric: str,
    direction: str,
    threshold: float,
    mandatory: bool,
    description: str,
) -> GateRule:
    return GateRule(
        key=key,
        metric=metric,
        direction=direction,
        threshold=threshold,
        mandatory=mandatory,
        description=description,
    )


def _common_gates(case_pass_minimum: float) -> tuple[GateRule, ...]:
    """Gates every role shares: evidence must be structured and parseable."""
    return (
        _gate(
            "case_pass_rate_min",
            "case_pass_rate",
            MIN,
            case_pass_minimum,
            True,
            "Role cases must pass often enough to trust the role at all.",
        ),
        _gate(
            "schema_validity_min",
            "schema_validity_rate",
            MIN,
            0.90,
            True,
            "Required output schemas must be honored; schema violations break callers.",
        ),
        _gate(
            "malformed_response_max",
            "malformed_response_rate",
            MAX,
            0.20,
            True,
            "Unparseable responses must stay rare; malformed output is never a success.",
        ),
        _gate(
            "consistency_rate_min",
            "consistency_rate",
            MIN,
            0.80,
            False,
            "Repeated identical prompts should produce identical output (measured "
            "only when the run repeats cases).",
        ),
        _gate(
            "repair_frequency_max",
            "repair_frequency",
            MAX,
            0.34,
            False,
            "A model needing repairs on more than a third of calls is fragile "
            "(measured only when repairs are enabled).",
        ),
        _gate(
            "repair_success_rate_min",
            "repair_success_rate",
            MIN,
            0.50,
            False,
            "When a repair happens it must usually fix the defect (measured only "
            "when repairs are enabled).",
        ),
    )


QUALIFICATION_POLICY: dict[QualificationRole, RolePolicy] = {
    QualificationRole.MANAGER: RolePolicy(
        role=QualificationRole.MANAGER,
        gates=(
            *_common_gates(0.70),
            _gate(
                "instruction_following_min",
                "instruction_following_rate",
                MIN,
                0.80,
                True,
                "A manager must follow assignment instructions and stay inside the "
                "known workforce.",
            ),
            _gate(
                "synthesis_completeness_min",
                "synthesis_completeness",
                MIN,
                0.75,
                False,
                "A manager summarizes work; partial synthesis is usable but bounded.",
            ),
            _gate(
                "hallucination_rate_max",
                "hallucination_rate",
                MAX,
                0.20,
                False,
                "Invented identifiers undermine manager reports; measured through the "
                "synthesis case.",
            ),
        ),
        weights={
            "case_pass_rate": 0.30,
            "instruction_following_rate": 0.25,
            "synthesis_completeness": 0.20,
            "schema_validity_rate": 0.15,
            "structured_output_success_rate": 0.10,
        },
        minimum_score=0.55,
        qualified_score=0.75,
    ),
    QualificationRole.PLANNER: RolePolicy(
        role=QualificationRole.PLANNER,
        gates=(
            *_common_gates(0.70),
            _gate(
                "instruction_following_min",
                "instruction_following_rate",
                MIN,
                0.80,
                True,
                "Plans must include required milestones and omit forbidden content.",
            ),
            _gate(
                "bounded_instruction_min",
                "bounded_instruction_rate",
                MIN,
                0.80,
                False,
                "Plans must respect explicit step/size bounds.",
            ),
        ),
        weights={
            "case_pass_rate": 0.35,
            "instruction_following_rate": 0.30,
            "bounded_instruction_rate": 0.15,
            "schema_validity_rate": 0.10,
            "structured_output_success_rate": 0.10,
        },
        minimum_score=0.55,
        qualified_score=0.75,
    ),
    QualificationRole.CAPABILITY_CLASSIFIER: RolePolicy(
        role=QualificationRole.CAPABILITY_CLASSIFIER,
        gates=(
            _gate(
                "case_pass_rate_min",
                "case_pass_rate",
                MIN,
                0.70,
                True,
                "Classification must be reliable across the capability cases.",
            ),
            _gate(
                "schema_validity_min",
                "schema_validity_rate",
                MIN,
                0.95,
                True,
                "Capability payloads feed team selection; schema errors are hard failures.",
            ),
            _gate(
                "malformed_response_max",
                "malformed_response_rate",
                MAX,
                0.10,
                True,
                "A classifier that emits unparseable output blocks routing.",
            ),
            _gate(
                "capability_classification_accuracy_min",
                "capability_classification_accuracy",
                MIN,
                0.90,
                True,
                "Required capability sets must match exactly (no invented capabilities). "
                "The suite is small, so this effectively requires exact capability sets "
                "on every classification case.",
            ),
            _gate(
                "secret_pass_rate_min",
                "secret_pass_rate",
                MIN,
                1.00,
                True,
                "Secret-bearing output is never acceptable from a routing component.",
            ),
            _gate(
                "context_degradation_max",
                "context_degradation_rate",
                MAX,
                0.50,
                False,
                "Longer context should not flip classification verdicts (measured by the "
                "paired small/large context cases).",
            ),
            _gate(
                "consistency_rate_min",
                "consistency_rate",
                MIN,
                0.80,
                False,
                "Classification should be repeatable across repetitions.",
            ),
            _gate(
                "repair_frequency_max",
                "repair_frequency",
                MAX,
                0.50,
                False,
                "Low-repair operation is expected from a small deterministic classifier.",
            ),
            _gate(
                "repair_success_rate_min",
                "repair_success_rate",
                MIN,
                0.50,
                False,
                "When repair is needed it must usually succeed.",
            ),
        ),
        weights={
            "case_pass_rate": 0.30,
            "capability_classification_accuracy": 0.35,
            "schema_validity_rate": 0.15,
            "structured_output_success_rate": 0.10,
            "secret_pass_rate": 0.10,
        },
        minimum_score=0.60,
        qualified_score=0.80,
    ),
    QualificationRole.DECOMPOSER: RolePolicy(
        role=QualificationRole.DECOMPOSER,
        gates=(
            _gate(
                "case_pass_rate_min",
                "case_pass_rate",
                MIN,
                0.50,
                True,
                "Decomposition has few cases; the graph-quality gate carries the weight.",
            ),
            _gate(
                "schema_validity_min",
                "schema_validity_rate",
                MIN,
                0.90,
                True,
                "Work graphs must match the planned-graph schema.",
            ),
            _gate(
                "malformed_response_max",
                "malformed_response_rate",
                MAX,
                0.20,
                True,
                "Unparseable graphs cannot be executed.",
            ),
            _gate(
                "decomposition_quality_min",
                "decomposition_quality",
                MIN,
                0.75,
                True,
                "Hard gate: DAG validity, dependency correctness, boundedness and "
                "capability coverage. Invalid graphs are never acceptable, however good "
                "the surrounding prose is.",
            ),
            _gate(
                "bounded_instruction_min",
                "bounded_instruction_rate",
                MIN,
                0.80,
                False,
                "Explicit node/size bounds must be respected.",
            ),
            _gate(
                "consistency_rate_min",
                "consistency_rate",
                MIN,
                0.80,
                False,
                "Decomposition should be repeatable across repetitions.",
            ),
            _gate(
                "repair_frequency_max",
                "repair_frequency",
                MAX,
                0.50,
                False,
                "Frequent repairs indicate unstable graph emission.",
            ),
            _gate(
                "repair_success_rate_min",
                "repair_success_rate",
                MIN,
                0.50,
                False,
                "When repair is needed it must usually succeed.",
            ),
        ),
        weights={
            "case_pass_rate": 0.30,
            "decomposition_quality": 0.35,
            "schema_validity_rate": 0.15,
            "bounded_instruction_rate": 0.10,
            "structured_output_success_rate": 0.10,
        },
        minimum_score=0.55,
        qualified_score=0.75,
    ),
    QualificationRole.SPECIALIST: RolePolicy(
        role=QualificationRole.SPECIALIST,
        gates=(
            *_common_gates(0.70),
            _gate(
                "instruction_following_min",
                "instruction_following_rate",
                MIN,
                0.80,
                True,
                "Specialists must satisfy the strict node-output contract.",
            ),
            _gate(
                "trust_boundary_min",
                "trust_boundary_rate",
                MIN,
                0.90,
                True,
                "Untrusted context must never become an instruction or authorization.",
            ),
            _gate(
                "bounded_instruction_min",
                "bounded_instruction_rate",
                MIN,
                0.80,
                False,
                "Specialist output must stay inside declared size bounds.",
            ),
        ),
        weights={
            "case_pass_rate": 0.30,
            "instruction_following_rate": 0.25,
            "trust_boundary_rate": 0.20,
            "schema_validity_rate": 0.15,
            "structured_output_success_rate": 0.10,
        },
        minimum_score=0.55,
        qualified_score=0.75,
    ),
    QualificationRole.REVIEWER: RolePolicy(
        role=QualificationRole.REVIEWER,
        gates=(
            _gate(
                "case_pass_rate_min",
                "case_pass_rate",
                MIN,
                0.70,
                True,
                "Review verdicts must be correct across the review cases.",
            ),
            _gate(
                "schema_validity_min",
                "schema_validity_rate",
                MIN,
                0.95,
                True,
                "Review verdicts are consumed by orchestration; schema errors are hard failures.",
            ),
            _gate(
                "malformed_response_max",
                "malformed_response_rate",
                MAX,
                0.10,
                True,
                "Unparseable review output cannot gate downstream work.",
            ),
            _gate(
                "instruction_following_min",
                "instruction_following_rate",
                MIN,
                0.80,
                True,
                "Review must follow the verdict contract and omit forbidden content.",
            ),
            _gate(
                "defect_detection_quality_min",
                "review_defect_quality",
                MIN,
                0.75,
                True,
                "Hard gate: a reviewer must find the real defects and must not invent "
                "defects (score = mean of defect recall and false-positive avoidance).",
            ),
            _gate(
                "consistency_rate_min",
                "consistency_rate",
                MIN,
                0.85,
                False,
                "Review verdicts should be repeatable across repetitions.",
            ),
            _gate(
                "repair_frequency_max",
                "repair_frequency",
                MAX,
                0.50,
                False,
                "Frequent repairs indicate unstable verdict emission.",
            ),
            _gate(
                "repair_success_rate_min",
                "repair_success_rate",
                MIN,
                0.50,
                False,
                "When repair is needed it must usually succeed.",
            ),
        ),
        weights={
            "case_pass_rate": 0.30,
            "review_defect_quality": 0.30,
            "instruction_following_rate": 0.20,
            "schema_validity_rate": 0.10,
            "structured_output_success_rate": 0.10,
        },
        minimum_score=0.60,
        qualified_score=0.80,
    ),
    QualificationRole.SYNTHESIZER: RolePolicy(
        role=QualificationRole.SYNTHESIZER,
        gates=(
            _gate(
                "case_pass_rate_min",
                "case_pass_rate",
                MIN,
                0.70,
                True,
                "The synthesis case must pass.",
            ),
            _gate(
                "schema_validity_min",
                "schema_validity_rate",
                MIN,
                0.90,
                True,
                "Synthesis payloads must match the synthesis schema.",
            ),
            _gate(
                "malformed_response_max",
                "malformed_response_rate",
                MAX,
                0.20,
                True,
                "Unparseable synthesis cannot be delivered.",
            ),
            _gate(
                "synthesis_completeness_min",
                "synthesis_completeness",
                MIN,
                0.80,
                True,
                "Hard gate: every input must be covered; dropped inputs are incomplete synthesis.",
            ),
            _gate(
                "hallucination_rate_max",
                "hallucination_rate",
                MAX,
                0.20,
                True,
                "Invented identifiers in synthesis are unsupported conclusions.",
            ),
            _gate(
                "consistency_rate_min",
                "consistency_rate",
                MIN,
                0.80,
                False,
                "Synthesis should be repeatable across repetitions.",
            ),
            _gate(
                "repair_frequency_max",
                "repair_frequency",
                MAX,
                0.50,
                False,
                "Frequent repairs indicate unstable synthesis.",
            ),
            _gate(
                "repair_success_rate_min",
                "repair_success_rate",
                MIN,
                0.50,
                False,
                "When repair is needed it must usually succeed.",
            ),
        ),
        weights={
            "case_pass_rate": 0.30,
            "synthesis_completeness": 0.35,
            "schema_validity_rate": 0.15,
            "structured_output_success_rate": 0.10,
            "trust_boundary_rate": 0.10,
        },
        minimum_score=0.60,
        qualified_score=0.75,
    ),
    QualificationRole.REPAIR_RETRY: RolePolicy(
        role=QualificationRole.REPAIR_RETRY,
        gates=(
            *_common_gates(0.70),
            _gate(
                "instruction_following_min",
                "instruction_following_rate",
                MIN,
                0.80,
                True,
                "Repair output must keep the required structure and explanations.",
            ),
        ),
        weights={
            "case_pass_rate": 0.40,
            "instruction_following_rate": 0.20,
            "schema_validity_rate": 0.20,
            "structured_output_success_rate": 0.20,
        },
        minimum_score=0.55,
        qualified_score=0.70,
    ),
}


def policy_for(role: QualificationRole) -> RolePolicy:
    return QUALIFICATION_POLICY[QualificationRole(role)]


def gates_for(role: QualificationRole) -> tuple[GateRule, ...]:
    return policy_for(role).gates


def policy_document() -> dict[str, Any]:
    """Full machine-readable policy (version, gates, weights, thresholds)."""
    roles: dict[str, Any] = {}
    for role in all_roles():
        policy = policy_for(role)
        contract: RoleContract = role_contract(role)
        roles[role.value] = {
            "label": contract.label,
            "evaluation_roles": [item.value for item in contract.evaluation_roles],
            "minimum_score": policy.minimum_score,
            "qualified_score": policy.qualified_score,
            "weights": dict(policy.weights),
            "mandatory_gates": [_gate_document(gate) for gate in policy.gates if gate.mandatory],
            "advisory_gates": [_gate_document(gate) for gate in policy.gates if not gate.mandatory],
        }
    return {
        "policy_version": QUALIFICATION_POLICY_VERSION,
        "note": POLICY_NOTE,
        "levels": {
            "qualified": "All mandatory gates pass and the role score reaches the "
            "qualified threshold.",
            "conditional": "No mandatory gate failed and the score clears the minimum, "
            "but an advisory gate failed or the score is below the qualified "
            "threshold; bounded use with review.",
            "unqualified": "A mandatory gate failed or the score is below the minimum "
            "quality threshold.",
            "not_evaluated": "Insufficient evidence (no scored cases, incomplete "
            "coverage, or an unmeasured mandatory gate).",
        },
        "gate_semantics": {
            "min": "gate passes when the observed metric is >= threshold",
            "max": "gate passes when the observed metric is <= threshold",
            "not_measured": "a mandatory gate whose metric was not measured fails "
            "closed to not_evaluated; an advisory gate is reported as not evaluated.",
        },
        "roles": roles,
    }


def _gate_document(gate: GateRule) -> dict[str, Any]:
    return {
        "key": gate.key,
        "metric": gate.metric,
        "direction": gate.direction,
        "threshold": gate.threshold,
        "mandatory": gate.mandatory,
        "description": gate.description,
    }


__all__ = [
    "MAX",
    "MIN",
    "POLICY_NOTE",
    "QUALIFICATION_POLICY",
    "QUALIFICATION_POLICY_VERSION",
    "GateRule",
    "RolePolicy",
    "gates_for",
    "policy_document",
    "policy_for",
]
