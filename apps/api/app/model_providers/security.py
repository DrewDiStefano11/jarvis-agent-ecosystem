from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

REDACTED = "[REDACTED]"
SECRET_KEY = re.compile(
    r"(?:api[_-]?key|authorization|token|secret|password|credential)", re.IGNORECASE
)
BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
ASSIGNMENT = re.compile(r"(?i)\b(api[_-]?key|token|secret|password|credential)\s*[:=]\s*([^\s,;]+)")


def redact_secrets(value: Any) -> Any:
    if isinstance(value, str):
        value = BEARER.sub(f"Bearer {REDACTED}", value)
        return ASSIGNMENT.sub(lambda match: f"{match.group(1)}={REDACTED}", value)
    if isinstance(value, Mapping):
        return {
            str(key): REDACTED if SECRET_KEY.search(str(key)) else redact_secrets(item)
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [redact_secrets(item) for item in value]
    return value
