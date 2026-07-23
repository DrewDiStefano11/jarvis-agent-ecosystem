import json
from pathlib import Path
from typing import Dict, Any, TypeVar, Type, Optional
from datetime import datetime

from jarvis_completion_validator.errors import SchemaValidationError
from jarvis_completion_validator.contracts import (
    TaskReviewInput, TaskEnvelope, CompletionCriterion, RequiredArtifact,
    WorkerResult, CriterionClaim, ArtifactManifest, Evidence, ApprovalRecord,
    TrustedCheck
)
from jarvis_completion_validator.enums import (
    CriterionType, VerificationMethod, EvidenceTrustLevel, FindingSeverity
)

# Note: We are minimizing external dependencies (like jsonschema).
# Basic validation and parsing into dataclasses happens here.

def parse_datetime(dt_str: Optional[str]) -> Optional[datetime]:
    if not dt_str:
        return None
    # Very basic ISO parsing, replaces Z with +00:00 for fromisoformat in 3.11
    dt_str = dt_str.replace("Z", "+00:00")
    return datetime.fromisoformat(dt_str)

def load_task_review_input(data: Dict[str, Any]) -> TaskReviewInput:
    try:
        task_data = data["task"]
        criteria = [
            CompletionCriterion(
                criterion_id=c["criterion_id"],
                description=c["description"],
                criterion_type=CriterionType(c["criterion_type"]),
                required=c["required"],
                verification_method=VerificationMethod(c["verification_method"]),
                expected_value=c.get("expected_value"),
                severity_if_unmet=FindingSeverity(c["severity_if_unmet"]) if c.get("severity_if_unmet") else None,
                automatic_validation_possible=c.get("automatic_validation_possible", True),
                human_review_required=c.get("human_review_required", c.get("criterion_type") == "manual_review")
            ) for c in task_data.get("completion_criteria", [])
        ]

        req_artifacts = [
            RequiredArtifact(
                artifact_id=a["artifact_id"],
                artifact_type=a["artifact_type"],
                format=a["format"],
                required=a.get("required", True)
            ) for a in task_data.get("required_artifacts", [])
        ]

        task = TaskEnvelope(
            task_id=task_data["task_id"],
            title=task_data["title"],
            request=task_data["request"],
            task_type=task_data["task_type"],
            priority=task_data.get("priority", "medium"),
            completion_criteria=criteria,
            required_artifacts=req_artifacts,
            allowed_result_types=task_data.get("allowed_result_types", []),
            approval_policy_version=task_data.get("approval_policy_version")
        )

        wr_data = data["worker_result"]
        claims = [
            CriterionClaim(
                criterion_id=cc["criterion_id"],
                claimed_satisfied=cc["claimed_satisfied"],
                evidence_ids=cc.get("evidence_ids", [])
            ) for cc in wr_data.get("criterion_claims", [])
        ]

        worker_result = WorkerResult(
            schema_version=wr_data["schema_version"],
            task_id=wr_data["task_id"],
            status_claim=wr_data["status_claim"],
            summary=wr_data["summary"],
            result_type=wr_data["result_type"],
            artifact_ids=wr_data.get("artifact_ids", []),
            criterion_claims=claims,
            worker_confidence=wr_data.get("worker_confidence", 0.0),
            limitations=wr_data.get("limitations", []),
            errors=wr_data.get("errors", []),
            warnings=wr_data.get("warnings", []),
            approval_claims=wr_data.get("approval_claims", [])
        )

        artifacts = [
            ArtifactManifest(
                artifact_id=am["artifact_id"],
                task_id=am["task_id"],
                artifact_type=am["artifact_type"],
                format=am["format"],
                relative_path=am["relative_path"],
                size_bytes=am["size_bytes"],
                sha256=am["sha256"],
                created_by_worker_id=am.get("created_by_worker_id", ""),
                created_at=parse_datetime(am["created_at"]) or datetime.now(),
                content_excerpt=am.get("content_excerpt"),
                validation_metadata=am.get("validation_metadata", {}),
                is_temporary=am.get("is_temporary", False)
            ) for am in data.get("artifacts", [])
        ]

        evidence = [
            Evidence(
                evidence_id=ev["evidence_id"],
                evidence_type=ev["evidence_type"],
                source=ev["source"],
                trust_level=EvidenceTrustLevel(ev["trust_level"]),
                timestamp=parse_datetime(ev["timestamp"]) or datetime.now(),
                related_criterion_ids=ev.get("related_criterion_ids", []),
                payload=ev.get("payload")
            ) for ev in data.get("evidence", [])
        ]

        approvals = [
            ApprovalRecord(
                approval_id=ap["approval_id"],
                task_id=ap["task_id"],
                action_type=ap["action_type"],
                status=ap["status"],
                risk_level=ap.get("risk_level", "low"),
                scope=ap["scope"],
                reviewed_by=ap["reviewed_by"],
                reviewed_at=parse_datetime(ap["reviewed_at"]) or datetime.now(),
                expiration=parse_datetime(ap.get("expiration")),
                decision_token=ap.get("decision_token")
            ) for ap in data.get("approvals", [])
        ]

        trusted_checks = [
            TrustedCheck(
                check_id=tc["check_id"],
                check_type=tc["check_type"],
                status=tc["status"],
                source=tc["source"],
                task_id=tc["task_id"],
                completed_at=parse_datetime(tc["completed_at"]) or datetime.now(),
                related_artifact_ids=tc.get("related_artifact_ids", []),
                details=tc.get("details", {})
            ) for tc in data.get("trusted_checks", [])
        ]

        return TaskReviewInput(
            schema_version=data["schema_version"],
            task=task,
            worker_result=worker_result,
            artifacts=artifacts,
            evidence=evidence,
            approvals=approvals,
            trusted_checks=trusted_checks
        )
    except KeyError as e:
        raise SchemaValidationError(f"Missing required field: {e}")
    except ValueError as e:
        raise SchemaValidationError(f"Invalid value mapping: {e}")
