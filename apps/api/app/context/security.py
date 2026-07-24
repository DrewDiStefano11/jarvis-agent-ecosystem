from __future__ import annotations

import re
from collections import Counter

from app.models.context import InjectionSeverity

INJECTION_PATTERNS: dict[str, tuple[re.Pattern[str], InjectionSeverity]] = {
    "ignore_instructions": (
        re.compile(r"ignore\s+(?:all\s+)?previous\s+instructions", re.IGNORECASE),
        InjectionSeverity.HIGH,
    ),
    "fake_system_message": (
        re.compile(r"^(?:system|developer|admin)\s*:", re.IGNORECASE | re.MULTILINE),
        InjectionSeverity.MEDIUM,
    ),
    "credential_access": (
        re.compile(
            r"(?:send|reveal|read)\s+(?:the\s+)?(?:\.env|credentials|secrets|passwords)",
            re.IGNORECASE,
        ),
        InjectionSeverity.CRITICAL,
    ),
    "shell_execution": (
        re.compile(
            r"(?:run|execute)\s+(?:this\s+)?(?:shell|powershell|cmd|bash)"
            r"(?:\s+command)?",
            re.IGNORECASE,
        ),
        InjectionSeverity.CRITICAL,
    ),
    "audit_bypass": (
        re.compile(r"disable\s+(?:the\s+)?(?:audit|safety\s+policy)", re.IGNORECASE),
        InjectionSeverity.CRITICAL,
    ),
    "approval_bypass": (
        re.compile(r"approve\s+this(?:\s+action)?\s+automatically", re.IGNORECASE),
        InjectionSeverity.HIGH,
    ),
    "git_push": (re.compile(r"\bgit\s+push\b", re.IGNORECASE), InjectionSeverity.HIGH),
    "send_email": (
        re.compile(r"\bsend\s+(?:an\s+)?email\b", re.IGNORECASE),
        InjectionSeverity.HIGH,
    ),
}

REDACTION_PATTERNS: dict[str, re.Pattern[str]] = {
    "api_key": re.compile(
        r"""(?ix)
        (?:api[_-]?key|secret|token|password)
        [\s:=]+
        ["']?
        ([a-z0-9_.-]{16,})
        ["']?
        """
    ),
    "bearer_token": re.compile(r"Bearer\s+([a-zA-Z0-9_.-]+)", re.IGNORECASE),
    "private_key": re.compile(
        r"-----BEGIN\s+[A-Z\s]+PRIVATE\s+KEY-----"
        r".+?"
        r"-----END\s+[A-Z\s]+PRIVATE\s+KEY-----",
        re.DOTALL,
    ),
}


def redact_sensitive_data(content: str) -> tuple[str, dict[str, int]]:
    redacted = content
    findings: Counter[str] = Counter()
    for category, pattern in REDACTION_PATTERNS.items():
        matches = list(pattern.finditer(redacted))
        for match in reversed(matches):
            start, end = match.span(1) if match.lastindex else match.span()
            if category != "private_key" and end - start < 8:
                continue
            redacted = f"{redacted[:start]}[REDACTED]{redacted[end:]}"
            findings[category] += 1
    return redacted, dict(sorted(findings.items()))


def detect_injection(content: str) -> list[tuple[str, InjectionSeverity, int]]:
    findings = [
        (category, severity, len(pattern.findall(content)))
        for category, (pattern, severity) in INJECTION_PATTERNS.items()
        if pattern.search(content)
    ]
    return sorted(findings, key=lambda item: (item[1].value, item[0]))
