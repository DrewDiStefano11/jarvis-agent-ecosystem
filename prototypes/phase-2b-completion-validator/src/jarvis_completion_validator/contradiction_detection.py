from typing import List, Tuple
from jarvis_completion_validator.contracts import (
    TaskReviewInput, ReviewerFinding
)
from jarvis_completion_validator.enums import (
    FindingSeverity, FindingCategory
)
import uuid

def detect_contradictions(input_data: TaskReviewInput) -> Tuple[List[ReviewerFinding], List[dict]]:
    findings = []
    contradictions_report = []

    worker_status = input_data.worker_result.status_claim.lower()

    # 1. Completed but has errors
    if worker_status == "completed" and input_data.worker_result.errors:
        f_id = f"find-{uuid.uuid4().hex[:8]}"
        findings.append(ReviewerFinding(
            finding_id=f_id,
            severity=FindingSeverity.MAJOR,
            category=FindingCategory.CONTRADICTION,
            summary="Worker claims completed but reports errors",
            detailed_reason="Status claim of 'completed' contradicts the presence of error strings.",
            automatically_remediable=True,
            recommended_next_action="request_revision"
        ))
        contradictions_report.append({"description": "Completed but has errors", "finding_id": f_id})

    # 2. Completed but missing required artifact
    provided_artifacts = {a.artifact_id for a in input_data.artifacts}
    for req_art in input_data.task.required_artifacts:
        if req_art.required and req_art.artifact_id not in provided_artifacts and worker_status == "completed":
            f_id = f"find-{uuid.uuid4().hex[:8]}"
            findings.append(ReviewerFinding(
                finding_id=f_id,
                severity=FindingSeverity.MAJOR,
                category=FindingCategory.CONTRADICTION,
                summary=f"Worker claims completed but missing required artifact {req_art.artifact_id}",
                detailed_reason="Cannot be completed if required artifacts are absent.",
                automatically_remediable=True,
                related_artifact_id=req_art.artifact_id,
                recommended_next_action="request_revision"
            ))
            contradictions_report.append({"description": f"Missing required artifact {req_art.artifact_id}", "finding_id": f_id})

    # 3. No-limitations claim plus warning list
    no_limitations = any("no limitation" in claim.lower() for claim in input_data.worker_result.limitations)
    if not input_data.worker_result.limitations and input_data.worker_result.warnings:
         if worker_status == "completed" and "no limitation" in input_data.worker_result.summary.lower():
             f_id = f"find-{uuid.uuid4().hex[:8]}"
             findings.append(ReviewerFinding(
                finding_id=f_id,
                severity=FindingSeverity.MINOR,
                category=FindingCategory.CONTRADICTION,
                summary="Worker claims no limitations but reports warnings",
                detailed_reason="Warnings inherently represent limitations or caveats.",
                automatically_remediable=True
            ))
             contradictions_report.append({"description": "Warnings contradict no-limitations claim", "finding_id": f_id})

    # 4. Completed but failed trusted check
    for check in input_data.trusted_checks:
        if check.status.lower() in ["failed", "error"] and worker_status == "completed":
            f_id = f"find-{uuid.uuid4().hex[:8]}"
            findings.append(ReviewerFinding(
                finding_id=f_id,
                severity=FindingSeverity.MAJOR,
                category=FindingCategory.CONTRADICTION,
                summary=f"Worker claims completed but trusted check {check.check_id} failed",
                detailed_reason="A task cannot be fully complete if a trusted verification check explicitly fails.",
                automatically_remediable=True,
                recommended_next_action="retry_step"
            ))
            contradictions_report.append({"description": f"Trusted check {check.check_id} failed", "finding_id": f_id})

    return findings, contradictions_report
