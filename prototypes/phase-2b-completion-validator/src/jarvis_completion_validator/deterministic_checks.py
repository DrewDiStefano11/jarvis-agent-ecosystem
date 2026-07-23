from typing import List
from jarvis_completion_validator.contracts import (
    TaskReviewInput, ReviewerFinding
)
from jarvis_completion_validator.enums import (
    FindingSeverity, FindingCategory
)
import uuid

def validate_trusted_checks(input_data: TaskReviewInput) -> List[ReviewerFinding]:
    findings = []

    for check in input_data.trusted_checks:
        if check.task_id != input_data.task.task_id:
             findings.append(ReviewerFinding(
                finding_id=f"find-{uuid.uuid4().hex[:8]}",
                severity=FindingSeverity.CRITICAL,
                category=FindingCategory.WRONG_TASK,
                summary=f"Trusted check {check.check_id} belongs to a different task",
                detailed_reason="Cannot use checks from other tasks.",
                automatically_remediable=False,
                recommended_next_action="reject"
            ))

        if check.status.lower() in ["failed", "error"]:
             findings.append(ReviewerFinding(
                finding_id=f"find-{uuid.uuid4().hex[:8]}",
                severity=FindingSeverity.MAJOR,
                category=FindingCategory.TRUSTED_CHECK_FAILED,
                summary=f"Trusted check {check.check_id} ({check.check_type}) failed",
                detailed_reason="Required trusted verification failed.",
                automatically_remediable=True,
                recommended_next_action="retry_step"
            ))

    return findings
