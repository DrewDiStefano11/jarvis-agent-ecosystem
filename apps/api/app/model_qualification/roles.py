"""Jarvis model-role taxonomy for local-model qualification.

This module defines *what Jarvis asks a model to do*, not how well any model
does it. It reuses PR #64's evaluation catalog
(:class:`app.model_evaluation.cases.EvaluationRole`) as the evidence source so
qualification never invents a competing vocabulary: every qualification role
names the evaluation roles whose cases produce its evidence.

``EvaluationRole`` answers "which evaluation case is this?".
``QualificationRole`` answers "which Jarvis job is the model being qualified
for?". One qualification role may consume several evaluation roles (a manager
also has to emit structured output and synthesize), and one evaluation role
may feed several qualification roles.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.model_evaluation.cases import EvaluationCase, EvaluationRole, all_cases


class QualificationRole(StrEnum):
    """The Jarvis jobs a local model can be qualified for."""

    MANAGER = "manager"
    PLANNER = "planner"
    CAPABILITY_CLASSIFIER = "capability_classifier"
    DECOMPOSER = "decomposer"
    SPECIALIST = "specialist"
    REVIEWER = "reviewer"
    SYNTHESIZER = "synthesizer"
    REPAIR_RETRY = "repair_retry"


@dataclass(frozen=True)
class RoleContract:
    """What a role is, what matters for it, and where its evidence comes from."""

    role: QualificationRole
    label: str
    summary: str
    evaluation_roles: tuple[EvaluationRole, ...]
    what_matters: tuple[str, ...]
    notes: str = ""


ROLE_CONTRACTS: dict[QualificationRole, RoleContract] = {
    QualificationRole.MANAGER: RoleContract(
        role=QualificationRole.MANAGER,
        label="Jarvis manager / coordinator",
        summary=(
            "Assigns bounded work to known workforce agents, keeps operator objectives "
            "and constraints, and summarizes outcomes without inventing authority."
        ),
        evaluation_roles=(
            EvaluationRole.MANAGER_COORDINATOR,
            EvaluationRole.SYNTHESIS,
            EvaluationRole.STRUCTURED_JSON,
        ),
        what_matters=(
            "instruction following",
            "long-context coherence",
            "decision quality",
            "synthesis",
            "constraint retention",
            "avoiding unsupported claims",
            "structured output reliability",
        ),
        notes=(
            "Manager evidence also includes synthesis and strict structured output "
            "because a manager both summarizes results and emits machine-readable plans."
        ),
    ),
    QualificationRole.PLANNER: RoleContract(
        role=QualificationRole.PLANNER,
        label="Jarvis planner",
        summary=(
            "Turns an objective into a bounded, ordered plan that respects explicit "
            "milestones, step limits, and output schemas."
        ),
        evaluation_roles=(
            EvaluationRole.PLANNING,
            EvaluationRole.STRUCTURED_JSON,
        ),
        what_matters=(
            "objective interpretation",
            "sequencing",
            "bounded planning",
            "dependency reasoning",
            "completeness",
            "schema compliance",
        ),
    ),
    QualificationRole.CAPABILITY_CLASSIFIER: RoleContract(
        role=QualificationRole.CAPABILITY_CLASSIFIER,
        label="Capability classifier / team selector",
        summary=(
            "Maps an objective to canonical capability keys, refuses to invent "
            "capabilities, and stays stable as context grows."
        ),
        evaluation_roles=(EvaluationRole.CAPABILITY_INFERENCE,),
        what_matters=(
            "deterministic classification",
            "low false-positive rate",
            "capability vocabulary compliance",
            "structured output reliability",
            "context sensitivity",
        ),
    ),
    QualificationRole.DECOMPOSER: RoleContract(
        role=QualificationRole.DECOMPOSER,
        label="Task decomposer",
        summary=(
            "Produces a bounded, acyclic work graph with correct dependencies and "
            "canonical capability assignment."
        ),
        evaluation_roles=(EvaluationRole.TASK_DECOMPOSITION,),
        what_matters=(
            "DAG validity",
            "dependency correctness",
            "boundedness",
            "capability assignment correctness",
            "schema validity",
        ),
    ),
    QualificationRole.SPECIALIST: RoleContract(
        role=QualificationRole.SPECIALIST,
        label="Specialist executor",
        summary=(
            "Executes one assigned work node, follows the strict output contract, and "
            "treats untrusted context as data rather than instruction."
        ),
        evaluation_roles=(EvaluationRole.SPECIALIST_EXECUTION,),
        what_matters=(
            "task accuracy",
            "instruction following",
            "context use",
            "useful completion",
            "low hallucination rate",
            "trust boundary retention",
        ),
    ),
    QualificationRole.REVIEWER: RoleContract(
        role=QualificationRole.REVIEWER,
        label="Reviewer / critic",
        summary=(
            "Finds real defects in another agent's work, avoids inventing defects, and "
            "returns actionable, evidence-grounded criticism."
        ),
        evaluation_roles=(EvaluationRole.REVIEW_CRITIQUE,),
        what_matters=(
            "defect detection",
            "false-positive avoidance",
            "evidence grounding",
            "actionable criticism",
            "identifying incomplete results",
        ),
    ),
    QualificationRole.SYNTHESIZER: RoleContract(
        role=QualificationRole.SYNTHESIZER,
        label="Synthesis / integration",
        summary=(
            "Integrates several node results into one answer, handles conflicting "
            "inputs, and preserves provenance without inventing conclusions."
        ),
        evaluation_roles=(EvaluationRole.SYNTHESIS,),
        what_matters=(
            "integration of multiple results",
            "conflict handling",
            "completeness",
            "provenance retention",
            "avoiding invented conclusions",
        ),
    ),
    QualificationRole.REPAIR_RETRY: RoleContract(
        role=QualificationRole.REPAIR_RETRY,
        label="Repair / retry",
        summary=(
            "Reads validation feedback, corrects the actual defect, and produces a "
            "materially improved attempt without regressing what already worked."
        ),
        evaluation_roles=(EvaluationRole.CORRECTION_RETRY,),
        what_matters=(
            "understanding failure feedback",
            "correcting the actual defect",
            "avoiding regression",
            "materially improved subsequent attempts",
        ),
    ),
}


def all_roles() -> tuple[QualificationRole, ...]:
    """Every supported qualification role, in taxonomy order."""
    return tuple(QualificationRole)


def role_names() -> tuple[str, ...]:
    return tuple(role.value for role in all_roles())


def parse_roles(values: tuple[str, ...] | list[str] | None) -> tuple[QualificationRole, ...]:
    """Parse role names into :class:`QualificationRole` values.

    Unknown names raise ``ValueError``: qualification never silently evaluates
    a different role than the one requested.
    """
    if values is None:
        return all_roles()
    parsed: list[QualificationRole] = []
    for value in values:
        try:
            role = QualificationRole(value)
        except ValueError as exc:
            raise ValueError(
                f"unknown qualification role {value!r} (supported: {list(role_names())})"
            ) from exc
        if role not in parsed:
            parsed.append(role)
    return tuple(parsed)


def role_contract(role: QualificationRole) -> RoleContract:
    return ROLE_CONTRACTS[QualificationRole(role)]


def cases_for_role(
    role: QualificationRole, cases: tuple[EvaluationCase, ...] | None = None
) -> tuple[EvaluationCase, ...]:
    """Evaluation cases whose evidence qualifies ``role`` (catalog order)."""
    contract = role_contract(role)
    return tuple(case for case in (cases or all_cases()) if case.role in contract.evaluation_roles)


def expected_case_ids(
    role: QualificationRole, cases: tuple[EvaluationCase, ...] | None = None
) -> tuple[str, ...]:
    return tuple(case.case_id for case in cases_for_role(role, cases))


def cases_for_roles(
    roles: tuple[QualificationRole, ...], cases: tuple[EvaluationCase, ...] | None = None
) -> tuple[EvaluationCase, ...]:
    """Union of cases for ``roles``, de-duplicated in catalog order."""
    catalog = cases or all_cases()
    wanted = {case_id for role in roles for case_id in expected_case_ids(role, catalog)}
    return tuple(case for case in catalog if case.case_id in wanted)


def role_document() -> dict[str, dict[str, object]]:
    """Machine-readable role taxonomy (embedded in qualification evidence)."""
    document: dict[str, dict[str, object]] = {}
    for role in all_roles():
        contract = role_contract(role)
        document[role.value] = {
            "label": contract.label,
            "summary": contract.summary,
            "evaluation_roles": [item.value for item in contract.evaluation_roles],
            "what_matters": list(contract.what_matters),
            "notes": contract.notes,
            "cases": list(expected_case_ids(role)),
        }
    return document


__all__ = [
    "ROLE_CONTRACTS",
    "QualificationRole",
    "RoleContract",
    "all_roles",
    "cases_for_role",
    "cases_for_roles",
    "expected_case_ids",
    "parse_roles",
    "role_contract",
    "role_document",
    "role_names",
]
