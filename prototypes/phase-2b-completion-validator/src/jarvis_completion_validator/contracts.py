from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from datetime import datetime
from jarvis_completion_validator.enums import (
    CriterionType, VerificationMethod, EvidenceTrustLevel,
    FindingSeverity, FindingCategory, CriterionStatus, Recommendation
)

@dataclass
class CompletionCriterion:
    criterion_id: str
    description: str
    criterion_type: CriterionType
    required: bool
    verification_method: VerificationMethod
    expected_value: Any = None
    severity_if_unmet: Optional[FindingSeverity] = None
    automatic_validation_possible: bool = True
    human_review_required: bool = False

@dataclass
class RequiredArtifact:
    artifact_id: str
    artifact_type: str
    format: str
    required: bool

@dataclass
class TaskEnvelope:
    task_id: str
    title: str
    request: str
    task_type: str
    priority: str
    completion_criteria: List[CompletionCriterion]
    required_artifacts: List[RequiredArtifact]
    allowed_result_types: List[str]
    approval_policy_version: Optional[str] = None

@dataclass
class CriterionClaim:
    criterion_id: str
    claimed_satisfied: bool
    evidence_ids: List[str] = field(default_factory=list)

@dataclass
class WorkerResult:
    schema_version: str
    task_id: str
    status_claim: str
    summary: str
    result_type: str
    artifact_ids: List[str]
    criterion_claims: List[CriterionClaim]
    worker_confidence: float
    limitations: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    approval_claims: List[str] = field(default_factory=list)

@dataclass
class ArtifactManifest:
    artifact_id: str
    task_id: str
    artifact_type: str
    format: str
    relative_path: str
    size_bytes: int
    sha256: str
    created_by_worker_id: str
    created_at: datetime
    content_excerpt: Optional[str] = None
    validation_metadata: Dict[str, Any] = field(default_factory=dict)
    is_temporary: bool = False

@dataclass
class Evidence:
    evidence_id: str
    evidence_type: str
    source: str
    trust_level: EvidenceTrustLevel
    timestamp: datetime
    related_criterion_ids: List[str] = field(default_factory=list)
    payload: Any = None

@dataclass
class ApprovalRecord:
    approval_id: str
    task_id: str
    action_type: str
    status: str
    risk_level: str
    scope: str
    reviewed_by: str
    reviewed_at: datetime
    expiration: Optional[datetime] = None
    decision_token: Optional[str] = None

@dataclass
class TrustedCheck:
    check_id: str
    check_type: str
    status: str
    source: str
    task_id: str
    completed_at: datetime
    related_artifact_ids: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)

@dataclass
class TaskReviewInput:
    schema_version: str
    task: TaskEnvelope
    worker_result: WorkerResult
    artifacts: List[ArtifactManifest]
    evidence: List[Evidence]
    approvals: List[ApprovalRecord]
    trusted_checks: List[TrustedCheck]

@dataclass
class ReviewerFinding:
    finding_id: str
    severity: FindingSeverity
    category: FindingCategory
    summary: str
    detailed_reason: str
    automatically_remediable: bool
    related_criterion_id: Optional[str] = None
    related_artifact_id: Optional[str] = None
    related_evidence_id: Optional[str] = None
    recommended_next_action: Optional[str] = None

@dataclass
class Score:
    total: int
    criteria_coverage: int
    artifact_validity: int
    evidence_strength: int
    consistency: int
    policy_compliance: int
    approval_readiness: int

@dataclass
class EvaluatedCriterion:
    criterion_id: str
    status: CriterionStatus
    verification_method: str
    evidence_ids: List[str] = field(default_factory=list)
    findings: List[str] = field(default_factory=list)

@dataclass
class ActualResult:
    completion_status: str
    recommendation: Recommendation
    automatic_acceptance_allowed: bool

@dataclass
class CompletionReport:
    schema_version: str
    validator_version: str
    timestamp: datetime
    task_id: str
    worker_claim: Dict[str, Any]
    actual_result: ActualResult
    score: Score
    criteria: List[EvaluatedCriterion]
    artifacts: List[Dict[str, Any]]
    approvals: List[Dict[str, Any]]
    trusted_checks: List[Dict[str, Any]]
    findings: List[ReviewerFinding]
    contradictions: List[Dict[str, str]]
    unsupported_claims: List[Dict[str, str]]
    warnings: List[str]
    recommended_next_actions: List[str]
