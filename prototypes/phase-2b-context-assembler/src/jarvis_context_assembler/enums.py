from enum import Enum, auto

class TrustLevel(str, Enum):
    SYSTEM_POLICY = "system_policy"
    TRUSTED_CONFIGURATION = "trusted_configuration"
    OPERATOR_INSTRUCTION = "operator_instruction"
    TASK_REQUEST = "task_request"
    TRUSTED_VALIDATOR = "trusted_validator"
    TRUSTED_TOOL_RESULT = "trusted_tool_result"
    APPROVED_ARTIFACT = "approved_artifact"
    REPOSITORY_CONTENT = "repository_content"
    EXTERNAL_CONTENT = "external_content"
    PRIOR_MODEL_OUTPUT = "prior_model_output"
    UNKNOWN = "unknown"

class SourceType(str, Enum):
    SYSTEM_POLICY = "system_policy"
    OPERATOR_INSTRUCTION = "operator_instruction"
    TASK_REQUEST = "task_request"
    REPOSITORY_FILE = "repository_file"
    ARTIFACT = "artifact"
    TOOL_RESULT = "tool_result"
    VALIDATOR_RESULT = "validator_result"
    PRIOR_MODEL_OUTPUT = "prior_model_output"
    EXTERNAL_DOCUMENT = "external_document"
    MANUAL_NOTE = "manual_note"

class InjectionSeverity(str, Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

class ApprovalState(str, Enum):
    APPROVED = "approved"
    UNREVIEWED = "unreviewed"
    REJECTED = "rejected"
    QUARANTINED = "quarantined"

class TruncationStrategy(str, Enum):
    REJECT = "reject"
    HEAD = "head"
    TAIL = "tail"
    HEAD_AND_TAIL = "head_and_tail"
    SECTION_AWARE = "section_aware"
    LINE_BOUNDED = "line_bounded"

class ExclusionReason(str, Enum):
    SOURCE_TYPE_DENIED = "source_type_denied"
    TRUST_LEVEL_DENIED = "trust_level_denied"
    WRONG_PROJECT = "wrong_project"
    NOT_APPROVED = "not_approved"
    REJECTED_SOURCE = "rejected_source"
    QUARANTINED_SOURCE = "quarantined_source"
    CRITICAL_INJECTION = "critical_injection"
    SENSITIVE_DATA = "sensitive_data"
    TOO_LARGE = "too_large"
    OVER_BUDGET = "over_budget"
    DUPLICATE = "duplicate"
    STALE = "stale"
    MISSING_PROVENANCE = "missing_provenance"
    INVALID_HASH = "invalid_hash"
    POLICY_CONFLICT = "policy_conflict"
    UNKNOWN_SOURCE_TYPE = "unknown_source_type"
