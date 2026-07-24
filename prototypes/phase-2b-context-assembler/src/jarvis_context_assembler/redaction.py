import re
from typing import Tuple, List, Dict

# Basic conservative patterns for typical secrets
REDACTION_PATTERNS = {
    "api_key": re.compile(r'(?i)(?:api[_-]?key|secret|token|password)[\s:=]+["\']?([a-zA-Z0-9_\-\.]{16,})["\']?'),
    "bearer_token": re.compile(r'(?i)Bearer\s+([a-zA-Z0-9_\-\.]+)'),
    "private_key": re.compile(r'-----BEGIN\s+[A-Z\s]+PRIVATE\s+KEY-----.+?-----END\s+[A-Z\s]+PRIVATE\s+KEY-----', re.DOTALL),
}

def redact_sensitive_data(content: str) -> Tuple[str, List[Dict]]:
    """
    Detects and redact sensitive data.
    Returns the redacted string and a list of findings.
    """
    findings = []
    redacted_content = content

    for category, pattern in REDACTION_PATTERNS.items():
        matches = list(pattern.finditer(redacted_content))
        for match in reversed(matches):
            if category == "private_key":
                start, end = match.span()
            else:
                # usually group 1 is the actual secret
                if match.lastindex and match.lastindex >= 1:
                    start, end = match.span(1)
                else:
                    start, end = match.span()

            # Simple check to avoid redacting ordinary "secret" without context,
            # our regex requires some assignment (=, :, space) and length,
            # so it's a bit conservative.
            original = redacted_content[start:end]
            if len(original) < 8 and category != "private_key":
                continue # likely false positive

            redacted_content = redacted_content[:start] + "[REDACTED]" + redacted_content[end:]
            findings.append({
                "category": category,
                "length": end - start
            })

    return redacted_content, findings
