import re
from typing import Any, Dict, List

SENSITIVE_KEY_PATTERN = re.compile(
    r"api_key|authorization|token|password|secret|credential|private_key",
    re.IGNORECASE
)

def redact_sensitive_values(obj: Any) -> Any:
    """
    Recursively replaces values for keys that match sensitive patterns.
    """
    if isinstance(obj, dict):
        redacted = {}
        for k, v in obj.items():
            if SENSITIVE_KEY_PATTERN.search(str(k)):
                redacted[k] = "***REDACTED***"
            else:
                redacted[k] = redact_sensitive_values(v)
        return redacted
    elif isinstance(obj, list):
        return [redact_sensitive_values(i) for i in obj]
    else:
        return obj
