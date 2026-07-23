class SupervisorError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False, state: str = None, instance_id: str = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.state = state
        self.instance_id = instance_id

class RuntimeNotInitialized(SupervisorError):
    def __init__(self):
        super().__init__("runtime_not_initialized", "Runtime directory not initialized")

class SupervisorOwnershipConflict(SupervisorError):
    def __init__(self):
        super().__init__("supervisor_lease_conflict", "Another supervisor instance holds the lease")

class WorkerStartupFailure(SupervisorError):
    def __init__(self, msg: str):
        super().__init__("worker_startup_failure", msg)

class CrashLoopActive(SupervisorError):
    def __init__(self):
        super().__init__("crash_loop_active", "Supervisor is in a crash loop and requires explicit reset")

class EmergencyStopActive(SupervisorError):
    def __init__(self):
        super().__init__("emergency_stop_active", "Emergency stop is active")

class WorkerIdentityUnverified(SupervisorError):
    def __init__(self):
        super().__init__("worker_identity_unverified", "Worker process identity could not be verified")
