"""Secret-safe output rules for diagnostics.

The doctor never serializes raw environments or configuration values. Report
fields are allowlisted by construction (``CheckResult.identifiers`` only
receives explicit scalar facts), and this module adds two defenses:

1. key-based redaction for anything that slipped in under a secret-like key;
2. value-based scrubbing of configured secret values (for example an API key
   set in the environment) so a secret cannot appear verbatim anywhere in
   rendered output.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

REDACTED = "[redacted]"

SECRET_KEY_PATTERN = re.compile(
    r"(secret|token|password|passwd|api_key|apikey|credential|authorization|auth_header|cookie)",
    re.IGNORECASE,
)

MAX_REDACTED_TEXT_LENGTH = 2000

_SECRET_VALUE_MINIMUM_LENGTH = 4


def collect_secret_values(environment: Mapping[str, str]) -> tuple[str, ...]:
    """Return distinctive environment values whose names look like secrets."""

    values = {
        value.strip()
        for key, value in environment.items()
        if SECRET_KEY_PATTERN.search(key) and len(value.strip()) >= _SECRET_VALUE_MINIMUM_LENGTH
    }
    return tuple(sorted(values, key=lambda value: (-len(value), value)))


def redact_text(value: str, secret_values: Sequence[str] = ()) -> str:
    """Scrub known secret values and obvious bearer/API-key patterns."""

    result = value
    for secret in secret_values:
        if secret:
            result = result.replace(secret, REDACTED)
    if len(result) > MAX_REDACTED_TEXT_LENGTH:
        result = result[:MAX_REDACTED_TEXT_LENGTH] + "...[truncated]"
    return result


def redact_value(value: Any, secret_values: Sequence[str] = ()) -> Any:
    """Recursively redact secret-like keys and scrub known secret values."""

    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = key if isinstance(key, str) else str(key)
            if SECRET_KEY_PATTERN.search(key_text):
                redacted[key_text] = REDACTED
            else:
                redacted[key_text] = redact_value(item, secret_values)
        return redacted
    if isinstance(value, (list, tuple)):
        return [redact_value(item, secret_values) for item in value]
    if isinstance(value, str):
        return redact_text(value, secret_values)
    return value
