from typing import List
from jarvis_completion_validator.contracts import (
    TaskReviewInput, ReviewerFinding, Score, EvaluatedCriterion
)
from jarvis_completion_validator.enums import (
    FindingSeverity, FindingCategory, CriterionStatus
)

def calculate_score(
    task_data: TaskReviewInput,
    evaluated_criteria: List[EvaluatedCriterion],
    findings: List[ReviewerFinding]
) -> Score:
    # Base dimensions (0-100)
    criteria_coverage = 100
    artifact_validity = 100
    evidence_strength = 100
    consistency = 100
    policy_compliance = 100
    approval_readiness = 100

    # Analyze criteria
    req_criteria = [c for c in task_data.task.completion_criteria if c.required]
    if req_criteria:
        met_count = sum(1 for ec in evaluated_criteria if ec.status == CriterionStatus.MET)
        criteria_coverage = int((met_count / len(req_criteria)) * 100)

    # Apply deductions based on findings
    for finding in findings:
        if finding.category in [FindingCategory.MISSING_ARTIFACT, FindingCategory.INVALID_ARTIFACT, FindingCategory.WRONG_RESULT_TYPE, FindingCategory.WRONG_TASK]:
            artifact_validity -= 25 if finding.severity == FindingSeverity.MAJOR else 50
        elif finding.category in [FindingCategory.MISSING_EVIDENCE, FindingCategory.UNSUPPORTED_CLAIM]:
            evidence_strength -= 20 if finding.severity == FindingSeverity.MINOR else 40
        elif finding.category == FindingCategory.CONTRADICTION:
            consistency -= 30
        elif finding.category == FindingCategory.POLICY_VIOLATION:
            policy_compliance = 0  # Immediate zero
        elif finding.category in [FindingCategory.APPROVAL_MISSING, FindingCategory.APPROVAL_INVALID]:
            approval_readiness = 0

    # Cap dimensions
    criteria_coverage = max(0, min(100, criteria_coverage))
    artifact_validity = max(0, min(100, artifact_validity))
    evidence_strength = max(0, min(100, evidence_strength))
    consistency = max(0, min(100, consistency))
    policy_compliance = max(0, min(100, policy_compliance))
    approval_readiness = max(0, min(100, approval_readiness))

    # Calculate weighted total
    total = int(
        (criteria_coverage * 0.3) +
        (artifact_validity * 0.2) +
        (evidence_strength * 0.1) +
        (consistency * 0.1) +
        (policy_compliance * 0.2) +
        (approval_readiness * 0.1)
    )

    # Hard overrides capping the score
    has_unmet_required = any(
        ec.status == CriterionStatus.UNMET and
        any(c.required for c in task_data.task.completion_criteria if c.criterion_id == ec.criterion_id)
        for ec in evaluated_criteria
    )

    if has_unmet_required:
        total = min(total, 49)

    if policy_compliance == 0:
        total = 0

    if approval_readiness == 0:
        total = min(total, 69)

    return Score(
        total=total,
        criteria_coverage=criteria_coverage,
        artifact_validity=artifact_validity,
        evidence_strength=evidence_strength,
        consistency=consistency,
        policy_compliance=policy_compliance,
        approval_readiness=approval_readiness
    )
