from typing import List
from jarvis_completion_validator.contracts import (
    TaskReviewInput, ReviewerFinding
)
from jarvis_completion_validator.enums import (
    FindingSeverity, FindingCategory
)
import uuid

def validate_artifacts(input_data: TaskReviewInput) -> List[ReviewerFinding]:
    findings = []

    provided_artifacts = {a.artifact_id: a for a in input_data.artifacts}

    # 1. Check required artifacts
    for req_art in input_data.task.required_artifacts:
        if req_art.required and req_art.artifact_id not in provided_artifacts:
            findings.append(ReviewerFinding(
                finding_id=f"find-{uuid.uuid4().hex[:8]}",
                severity=FindingSeverity.MAJOR,
                category=FindingCategory.MISSING_ARTIFACT,
                summary=f"Missing required artifact: {req_art.artifact_id}",
                detailed_reason=f"Task requires an artifact of type {req_art.artifact_type} ({req_art.format})",
                automatically_remediable=True,
                related_artifact_id=req_art.artifact_id,
                recommended_next_action="request_revision"
            ))
            continue

        if req_art.artifact_id in provided_artifacts:
            art = provided_artifacts[req_art.artifact_id]
            if art.artifact_type != req_art.artifact_type:
                findings.append(ReviewerFinding(
                    finding_id=f"find-{uuid.uuid4().hex[:8]}",
                    severity=FindingSeverity.MAJOR,
                    category=FindingCategory.INVALID_ARTIFACT,
                    summary=f"Artifact {art.artifact_id} has wrong type",
                    detailed_reason=f"Expected {req_art.artifact_type}, got {art.artifact_type}",
                    automatically_remediable=True,
                    related_artifact_id=art.artifact_id,
                    recommended_next_action="replan_task"
                ))
            if art.format != req_art.format:
                findings.append(ReviewerFinding(
                    finding_id=f"find-{uuid.uuid4().hex[:8]}",
                    severity=FindingSeverity.MAJOR,
                    category=FindingCategory.INVALID_ARTIFACT,
                    summary=f"Artifact {art.artifact_id} has wrong format",
                    detailed_reason=f"Expected {req_art.format}, got {art.format}",
                    automatically_remediable=True,
                    related_artifact_id=art.artifact_id,
                    recommended_next_action="request_revision"
                ))

    # 2. Check artifact general validity
    for art in input_data.artifacts:
        if art.task_id != input_data.task.task_id:
            findings.append(ReviewerFinding(
                finding_id=f"find-{uuid.uuid4().hex[:8]}",
                severity=FindingSeverity.CRITICAL,
                category=FindingCategory.WRONG_TASK,
                summary=f"Artifact {art.artifact_id} belongs to different task",
                detailed_reason=f"Artifact task_id {art.task_id} does not match current task {input_data.task.task_id}",
                automatically_remediable=False,
                related_artifact_id=art.artifact_id,
                recommended_next_action="reject"
            ))

        if art.is_temporary and art.artifact_id in [ra.artifact_id for ra in input_data.task.required_artifacts]:
             findings.append(ReviewerFinding(
                finding_id=f"find-{uuid.uuid4().hex[:8]}",
                severity=FindingSeverity.MAJOR,
                category=FindingCategory.INVALID_ARTIFACT,
                summary=f"Artifact {art.artifact_id} is marked temporary",
                detailed_reason="Required artifacts cannot be temporary.",
                automatically_remediable=True,
                related_artifact_id=art.artifact_id,
                recommended_next_action="request_revision"
            ))

        # Check placeholders loosely
        if art.content_excerpt:
            placeholders = ["TODO", "TBD", "PLACEHOLDER", "INSERT HERE", "LOREM IPSUM", "FIXME"]
            if any(p in art.content_excerpt.upper() for p in placeholders):
                findings.append(ReviewerFinding(
                    finding_id=f"find-{uuid.uuid4().hex[:8]}",
                    severity=FindingSeverity.MINOR,
                    category=FindingCategory.PLACEHOLDER_CONTENT,
                    summary=f"Artifact {art.artifact_id} contains placeholder content",
                    detailed_reason="Detected placeholder strings in content excerpt.",
                    automatically_remediable=True,
                    related_artifact_id=art.artifact_id,
                    recommended_next_action="request_revision"
                ))

    return findings
