from enum import Enum

class NetworkPolicy(str, Enum):
    NONE = "none"
    LOOPBACK = "loopback"
    RESTRICTED = "restricted"
    ALL = "all"

class StepType(str, Enum):
    TOOL = "tool"
    REASONING = "reasoning"
    VALIDATION = "validation"
    HUMAN_REVIEW = "human_review"
    ARTIFACT_ASSEMBLY = "artifact_assembly"

class ApprovalHint(str, Enum):
    NONE = "none"
    AUTOMATIC = "automatic"
    REQUIRED = "required"

class FinalOutputType(str, Enum):
    ARTIFACT = "artifact"
    SUMMARY = "summary"
    REPORT = "report"
    ANALYSIS = "analysis"
    PATCH_PROPOSAL = "patch_proposal"

class FinalOutputFormat(str, Enum):
    MARKDOWN = "markdown"
    JSON = "json"
    TEXT = "text"
    UNIFIED_DIFF = "unified_diff"

class ToolRiskLevel(str, Enum):
    GREEN = "green"
    YELLOW = "yellow"
    ORANGE = "orange"
    RED = "red"
    BLACK = "black"

class ValidationStatus(str, Enum):
    VALID = "valid"
    INVALID = "invalid"

class ExecutionEligibility(str, Enum):
    AUTOMATIC = "automatic"
    AUTOMATIC_WITH_NOTIFICATION = "automatic_with_notification"
    APPROVAL_REQUIRED = "approval_required"
    PARTIALLY_APPROVABLE = "partially_approvable"
    PROHIBITED = "prohibited"
    UNSUPPORTED = "unsupported"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"

class RecommendedAction(str, Enum):
    ACCEPT = "accept"
    REQUEST_APPROVAL = "request_approval"
    RETRY_MODEL = "retry_model"
    REPLAN = "replan"
    BLOCK = "block"
    REJECT = "reject"
    HUMAN_REVIEW = "human_review"

class ExitCode(int, Enum):
    VALID_AUTOMATIC = 0
    VALID_APPROVAL_REQUIRED = 1
    VALID_BLOCKED_OR_REVIEW = 2
    INVALID_SCHEMA_OR_MALFORMED = 3
    INVALID_GRAPH = 4
    POLICY_VIOLATION = 5
    UNSUPPORTED_TOOL = 6
    CONFIGURATION_ERROR = 7
    REPORT_WRITE_FAILURE = 8
    UNEXPECTED_ERROR = 9
