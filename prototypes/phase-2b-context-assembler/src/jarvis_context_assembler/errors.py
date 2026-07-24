class ContextAssemblerError(Exception):
    def __init__(self, code: str, message: str, source_id: str | None = None, retryable: bool = False, recommended_action: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.source_id = source_id
        self.retryable = retryable
        self.recommended_action = recommended_action

class InvalidPolicyError(ContextAssemblerError):
    def __init__(self, message: str = "Invalid policy"):
        super().__init__("invalid_policy", message)

class InvalidTaskError(ContextAssemblerError):
    def __init__(self, message: str = "Invalid task"):
        super().__init__("invalid_task", message)

class InvalidSourceError(ContextAssemblerError):
    def __init__(self, message: str = "Invalid source", source_id: str | None = None):
        super().__init__("invalid_source", message, source_id)

class ContextOverBudgetError(ContextAssemblerError):
    def __init__(self, message: str = "Context over budget"):
        super().__init__("context_over_budget", message)
