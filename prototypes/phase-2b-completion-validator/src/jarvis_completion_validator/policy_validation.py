from typing import List
from jarvis_completion_validator.contracts import (
    TaskReviewInput, ReviewerFinding
)
from jarvis_completion_validator.enums import (
    FindingSeverity, FindingCategory
)
import uuid
from datetime import datetime, timezone

def validate_approvals_and_policy(input_data: TaskReviewInput) -> List[ReviewerFinding]:
    findings = []

    # Check explicitly defined policies (mocked for prototype, e.g. unapproved patches)
    if "unapproved patch" in input_data.worker_result.summary.lower():
        findings.append(ReviewerFinding(
            finding_id=f"find-{uuid.uuid4().hex[:8]}",
            severity=FindingSeverity.CRITICAL,
            category=FindingCategory.POLICY_VIOLATION,
            summary="Worker claims to have applied an unapproved patch",
            detailed_reason="Applying patches without explicit approval violates core system policy.",
            automatically_remediable=False,
            recommended_next_action="reject"
        ))

    # General approval validation
    now = datetime.now(timezone.utc)
    for approval in input_data.approvals:
        # Check task matching
        if approval.task_id != input_data.task.task_id:
            findings.append(ReviewerFinding(
                finding_id=f"find-{uuid.uuid4().hex[:8]}",
                severity=FindingSeverity.CRITICAL,
                category=FindingCategory.APPROVAL_INVALID,
                summary=f"Approval {approval.approval_id} is for a different task",
                detailed_reason="Approvals cannot be reused across tasks.",
                automatically_remediable=False,
                recommended_next_action="reject"
            ))
            continue

        # Check expiration
        if approval.expiration and approval.expiration.replace(tzinfo=timezone.utc) < now:
             findings.append(ReviewerFinding(
                finding_id=f"find-{uuid.uuid4().hex[:8]}",
                severity=FindingSeverity.MAJOR,
                category=FindingCategory.APPROVAL_INVALID,
                summary=f"Approval {approval.approval_id} has expired",
                detailed_reason="Expired approvals are invalid.",
                automatically_remediable=False,
                recommended_next_action="request_approval"
            ))

        # Check status
        if approval.status.lower() == "rejected":
             findings.append(ReviewerFinding(
                finding_id=f"find-{uuid.uuid4().hex[:8]}",
                severity=FindingSeverity.CRITICAL,
                category=FindingCategory.APPROVAL_INVALID,
                summary=f"Approval {approval.approval_id} was rejected",
                detailed_reason="Proceeding with a rejected approval is a policy violation.",
                automatically_remediable=False,
                recommended_next_action="reject"
            ))

        if approval.status.lower() == "pending":
             findings.append(ReviewerFinding(
                finding_id=f"find-{uuid.uuid4().hex[:8]}",
                severity=FindingSeverity.MAJOR,
                category=FindingCategory.APPROVAL_MISSING,
                summary=f"Approval {approval.approval_id} is pending",
                detailed_reason="Task requires this approval to be resolved.",
                automatically_remediable=False,
                recommended_next_action="request_approval"
            ))

    return findings
