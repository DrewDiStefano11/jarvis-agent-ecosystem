from .config import SupervisorConfig

def calculate_backoff(attempt_count: int, config: SupervisorConfig) -> float:
    if attempt_count == 0:
        return 0.0

    delay = config.initial_backoff_seconds * (2 ** (attempt_count - 1))
    return min(delay, config.maximum_backoff_seconds)
