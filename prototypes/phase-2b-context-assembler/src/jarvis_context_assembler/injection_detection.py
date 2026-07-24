import re
from typing import Dict, Any, Optional, List
from .enums import InjectionSeverity

INJECTION_PATTERNS = {
    "ignore_instructions": {
        "pattern": re.compile(r'(?i)ignore\s+(all\s+)?previous\s+instructions'),
        "severity": InjectionSeverity.HIGH
    },
    "fake_system_message": {
        "pattern": re.compile(r'(?i)^(system|developer|admin):\s+', re.MULTILINE),
        "severity": InjectionSeverity.MEDIUM
    },
    "credential_access": {
        "pattern": re.compile(r'(?i)(send|reveal|read)\s+(the\s+)?(\.env|credentials|secrets|passwords)'),
        "severity": InjectionSeverity.CRITICAL
    },
    "shell_execution": {
        "pattern": re.compile(r'(?i)(run|execute)\s+(this\s+)?(shell|powershell|cmd|bash)(\s+command)?'),
        "severity": InjectionSeverity.CRITICAL
    },
    "audit_bypass": {
        "pattern": re.compile(r'(?i)disable\s+(the\s+)?(audit|safety\s+policy)'),
        "severity": InjectionSeverity.CRITICAL
    },
    "approval_bypass": {
        "pattern": re.compile(r'(?i)approve\s+this(\s+action)?\s+automatically'),
        "severity": InjectionSeverity.HIGH
    },
    "git_push": {
        "pattern": re.compile(r'(?i)git\s+push'),
        "severity": InjectionSeverity.HIGH
    },
    "send_email": {
        "pattern": re.compile(r'(?i)send\s+(an\s+)?email'),
        "severity": InjectionSeverity.HIGH
    },
}

def detect_injection(content: str) -> List[Dict[str, Any]]:
    findings = []
    for category, config in INJECTION_PATTERNS.items():
        pattern = config["pattern"]
        severity = config["severity"]
        for match in pattern.finditer(content):
            start, end = match.span()
            findings.append({
                "category": category,
                "severity": severity,
                "excerpt": content[max(0, start - 20):min(len(content), end + 20)].strip()
            })
    return findings
