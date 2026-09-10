"""Operator-facing runtime diagnostics for the Jarvis AI Hub.

The doctor answers one question with evidence: is the local runtime ready to
run, and if not, exactly what is preventing it? Checks are read-only,
bounded, secret-free, and built entirely on existing repository authority
(health endpoints, durable system state, supervisor coordination state,
provider abstractions, and workspace trust markers).
"""

from app.diagnostics.models import (
    CheckResult,
    CheckStatus,
    DiagnosticReport,
    overall_status,
)
from app.diagnostics.runner import (
    DiagnosticsRun,
    render_json,
    render_markdown,
    render_text,
    run_diagnostics,
    write_report_files,
)

__all__ = [
    "CheckResult",
    "CheckStatus",
    "DiagnosticReport",
    "DiagnosticsRun",
    "overall_status",
    "render_json",
    "render_markdown",
    "render_text",
    "run_diagnostics",
    "write_report_files",
]
