from __future__ import annotations

from app.core.errors import DomainError

ERROR_MESSAGES = {
    "AUTONOMOUS_WORKER_DISABLED": "The autonomous worker is disabled.",
    "MODEL_EXECUTION_DISABLED": "Local model execution is disabled.",
    "LOCAL_PROVIDER_REQUIRED": "A structurally loopback model provider is required.",
    "NO_LOCAL_PROVIDER_AVAILABLE": "No eligible local model provider is available.",
    "RUNTIME_EXECUTION_NOT_ELIGIBLE": "The runtime run is not eligible for autonomous execution.",
    "CONTEXT_ASSEMBLY_REQUIRED": "An explicit Context Assembly reference is required.",
    "CONTEXT_ASSEMBLY_UNAVAILABLE": "The referenced Context Assembly is unavailable.",
    "CONTEXT_ASSEMBLY_REVIEW_REQUIRED": "The Context Assembly requires human review.",
    "CONTEXT_ASSEMBLY_MISMATCH": "The Context Assembly does not match the runtime task.",
    "MODEL_EXECUTION_BUDGET_EXCEEDED": "The model execution budget is exhausted.",
    "MODEL_EXECUTION_TIMEOUT": "The model execution exceeded its time limit.",
    "MODEL_OUTPUT_INVALID": "The model output does not match the fixed result schema.",
    "MODEL_OUTPUT_REPAIR_EXHAUSTED": "The bounded model-output repair allowance is exhausted.",
    "MODEL_RESULT_CONFLICT": "A different result already exists for this runtime attempt.",
    "MODEL_RESULT_CORRUPT": "The persisted model result failed its integrity check.",
    "PLAN_REVIEW_RECORD_CORRUPT": "The durable planning review record failed its integrity check.",
    "MODEL_RESULT_PERSISTENCE_FAILED": "The validated model result could not be persisted.",
    "EXECUTION_LEASE_LOST": "The worker no longer owns the current task lease.",
    "EXECUTION_CANCELLED": "The task or runtime execution was cancelled.",
    "EXECUTION_EMERGENCY_STOPPED": "Emergency stop blocked autonomous execution.",
    "EXECUTION_AUTHORIZATION_REVOKED": "Authorization changed during autonomous execution.",
}


class AutonomousWorkerError(DomainError):
    def __init__(self, code: str, *, status_code: int = 409) -> None:
        super().__init__(code, ERROR_MESSAGES[code], status_code)
