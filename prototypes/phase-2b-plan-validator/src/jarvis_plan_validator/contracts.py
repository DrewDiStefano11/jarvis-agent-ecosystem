from dataclasses import dataclass, field
from typing import List, Optional, Any, Dict
from .enums import (
    NetworkPolicy, StepType, ApprovalHint, FinalOutputType,
    FinalOutputFormat, ToolRiskLevel, ValidationStatus,
    ExecutionEligibility, RecommendedAction
)

@dataclass
class TaskEnvelope:
    schema_version: str
    task_id: str
    title: str
    request: str
    allowed_tools: List[str]
    maximum_steps: int
    network_policy: NetworkPolicy
    project_id: Optional[str] = None
    workspace_roots: List[str] = field(default_factory=list)
    denied_tools: List[str] = field(default_factory=list)
    maximum_model_retries: int = 0
    approval_policy_version: Optional[str] = None

@dataclass
class RetryPolicy:
    maximum_attempts: int
    retryable_errors: List[str] = field(default_factory=list)

@dataclass
class ExecutionStep:
    step_id: str
    title: str
    description: str
    step_type: StepType
    depends_on: List[str]
    expected_output: str
    tool_name: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)
    approval_hint: ApprovalHint = ApprovalHint.NONE
    retry_policy: Optional[RetryPolicy] = None
    checkpoint_after: bool = False

@dataclass
class FinalOutput:
    type: FinalOutputType
    format: FinalOutputFormat

@dataclass
class ExecutionPlan:
    schema_version: str
    plan_id: str
    task_id: str
    objective: str
    completion_criteria: List[str]
    steps: List[ExecutionStep]
    final_output: FinalOutput
    assumptions: List[str] = field(default_factory=list)

@dataclass
class ValidationReportInput:
    raw_bytes: int
    json_extraction: str
    extra_prose_detected: bool

@dataclass
class ValidationReportResult:
    status: ValidationStatus
    execution_eligibility: ExecutionEligibility
    schema_valid: bool
    graph_valid: bool
    policy_valid: bool

@dataclass
class ValidationReportLimits:
    step_count: int
    maximum_steps: int

@dataclass
class ValidationReportGraph:
    topological_order: List[str]
    cycles: List[List[str]]

@dataclass
class ValidationReportStep:
    step_id: str
    valid: bool
    errors: List[str]
    warnings: List[str]
    tool_name: Optional[str] = None
    risk_level: Optional[ToolRiskLevel] = None
    approval: Optional[str] = None

@dataclass
class ValidationReportNormalization:
    field: str
    original: str
    normalized: str
    reason: str

@dataclass
class ValidationReport:
    schema_version: str
    validator_version: str
    timestamp: str
    task_id: str
    plan_id: str
    input: ValidationReportInput
    result: ValidationReportResult
    limits: ValidationReportLimits
    graph: ValidationReportGraph
    steps: List[ValidationReportStep]
    errors: List[str]
    warnings: List[str]
    recommended_action: RecommendedAction
    normalizations: List[ValidationReportNormalization] = field(default_factory=list)
