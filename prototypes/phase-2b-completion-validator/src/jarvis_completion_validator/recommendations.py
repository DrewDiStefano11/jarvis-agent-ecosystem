from typing import List, Tuple
from jarvis_completion_validator.contracts import (
    TaskReviewInput, ReviewerFinding, EvaluatedCriterion, ActualResult
)
from jarvis_completion_validator.enums import (
    FindingSeverity, FindingCategory, Recommendation, CriterionStatus
)

def determine_recommendation(
    task_data: TaskReviewInput,
    evaluated_criteria: List[EvaluatedCriterion],
    findings: List[ReviewerFinding]
) -> Tuple[ActualResult, List[str]]:

    # Identify blocking conditions first (precedence)

    # 1. Reject
    if any(f.recommended_next_action == "reject" for f in findings) or any(f.category in [FindingCategory.POLICY_VIOLATION, FindingCategory.WRONG_TASK, FindingCategory.INTEGRITY_FAILURE] for f in findings):
         return ActualResult(
            completion_status="incomplete",
            recommendation=Recommendation.REJECT,
            automatic_acceptance_allowed=False
         ), ["Reject due to policy or integrity violation"]

    # 2. Block
    if any(f.recommended_next_action == "block" for f in findings):
        return ActualResult(
            completion_status="incomplete",
            recommendation=Recommendation.BLOCK,
            automatic_acceptance_allowed=False
         ), ["Block due to missing external dependency"]

    # 3. Request Approval
    if any(f.category in [FindingCategory.APPROVAL_MISSING, FindingCategory.APPROVAL_INVALID] for f in findings):
        return ActualResult(
            completion_status="incomplete",
            recommendation=Recommendation.REQUEST_APPROVAL,
            automatic_acceptance_allowed=False
         ), ["Request pending approvals"]

    # 4. Human Review
    if any(ec.status == CriterionStatus.MANUAL_REVIEW_REQUIRED for ec in evaluated_criteria):
        return ActualResult(
            completion_status="incomplete",
            recommendation=Recommendation.HUMAN_REVIEW,
            automatic_acceptance_allowed=False
         ), ["Awaiting manual review for specific criteria"]

    # 5. Replan Task
    if any(f.recommended_next_action == "replan_task" for f in findings):
        return ActualResult(
            completion_status="incomplete",
            recommendation=Recommendation.REPLAN_TASK,
            automatic_acceptance_allowed=False
         ), ["Replan task due to fundamental mismatch"]

    # 6. Retry Step
    if any(f.recommended_next_action == "retry_step" for f in findings):
        return ActualResult(
            completion_status="incomplete",
            recommendation=Recommendation.RETRY_STEP,
            automatic_acceptance_allowed=False
         ), ["Retry specific failed step or check"]

    # 7. Request Revision
    if any(f.recommended_next_action == "request_revision" for f in findings) or any(f.severity in [FindingSeverity.MAJOR, FindingSeverity.CRITICAL] for f in findings):
        return ActualResult(
            completion_status="incomplete",
            recommendation=Recommendation.REQUEST_REVISION,
            automatic_acceptance_allowed=False
         ), ["Request revision to fix major findings"]

    if any(ec.status == CriterionStatus.UNMET and any(c.required for c in task_data.task.completion_criteria if c.criterion_id == ec.criterion_id) for ec in evaluated_criteria):
        return ActualResult(
            completion_status="incomplete",
            recommendation=Recommendation.REQUEST_REVISION,
            automatic_acceptance_allowed=False
         ), ["Request revision to meet required criteria"]

    # 8. Accept with Warnings
    if any(f.severity in [FindingSeverity.INFO, FindingSeverity.WARNING, FindingSeverity.MINOR] for f in findings) or task_data.worker_result.warnings:
         return ActualResult(
            completion_status="completed",
            recommendation=Recommendation.ACCEPT_WITH_WARNINGS,
            automatic_acceptance_allowed=True
         ), ["Accept with warnings"]

    # 9. Accept
    return ActualResult(
        completion_status="completed",
        recommendation=Recommendation.ACCEPT,
        automatic_acceptance_allowed=True
    ), ["Accept"]
