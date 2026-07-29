from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

REDACTED = "[REDACTED]"
SECRET_KEY = re.compile(
    r"(?:api[_-]?key|authorization|token|secret|password|credential)", re.IGNORECASE
)
BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|authorization|token|secret|password|credential)"
    r"\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)


def redact_secrets(value: Any, _seen: set[int] | None = None) -> Any:
    if isinstance(value, str):
        redacted = BEARER.sub(f"Bearer {REDACTED}", value)
        return ASSIGNMENT.sub(lambda match: f"{match.group(1)}={REDACTED}", redacted)
    seen = _seen if _seen is not None else set()
    if isinstance(value, Mapping):
        if id(value) in seen:
            return "[RECURSIVE]"
        seen.add(id(value))
        return {
            str(key): REDACTED if SECRET_KEY.search(str(key)) else redact_secrets(item, seen)
            for key, item in list(value.items())[:32]
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if id(value) in seen:
            return "[RECURSIVE]"
        seen.add(id(value))
        return [redact_secrets(item, seen) for item in list(value)[:64]]
    return value
