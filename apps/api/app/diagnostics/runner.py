"""Run the doctor checks and render operator-facing output.

Fast mode answers "is Jarvis ready to run, and if not, why?" with lightweight
read-only probes. Deep mode additionally exercises API request-path contracts,
database integrity, and cross-source state consistency. Neither mode starts
processes, runs migrations, calls models, or clears emergency stop.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, NamedTuple

from app.diagnostics import checks as check_functions
from app.diagnostics.context import Clock, DiagnosticsContext, build_context
from app.diagnostics.models import (
    CheckResult,
    CheckStatus,
    DiagnosticReport,
    overall_status,
)
from app.diagnostics.probes import HttpProbe
from app.diagnostics.redaction import redact_text, redact_value

REPORT_JSON_NAME = "jarvis-diagnostic.json"
REPORT_MARKDOWN_NAME = "jarvis-diagnostic.md"
MAX_BLOCKED_REASONS = 8


class DiagnosticsRun(NamedTuple):
    report: DiagnosticReport
    context: DiagnosticsContext


def run_diagnostics(
    repository: Path,
    *,
    environ: Mapping[str, str] | None = None,
    deep: bool = False,
    http_probe: HttpProbe | None = None,
    provider_health: Callable[[Any], Any] | None = None,
    clock: Clock | None = None,
) -> DiagnosticsRun:
    ctx = build_context(
        repository,
        environ=environ,
        deep=deep,
        http_probe=http_probe,
        clock=clock,
    )
    executed: dict[str, CheckResult] = {}

    def record(result: CheckResult) -> CheckResult:
        executed[result.name] = result
        return result

    record(check_functions.check_identity(ctx))
    record(check_functions.check_configuration(ctx))
    record(check_functions.check_api(ctx))
    if deep:
        record(check_functions.check_system_status_contract(ctx))
    record(check_functions.check_database(ctx))
    record(check_functions.check_emergency_stop(ctx))
    record(check_functions.check_frontend(ctx))
    record(check_functions.check_supervisor(ctx))
    if provider_health is None:
        record(check_functions.check_provider(ctx))
    else:
        record(check_functions.check_provider(ctx, provider_health))
    record(check_functions.check_worker(ctx))
    record(check_functions.check_workspace_tools(ctx))
    record(check_functions.check_office(ctx))
    record(check_functions.check_recent_failures(ctx))
    record(check_functions.check_readiness(ctx, executed))

    ordered = tuple(executed.values())
    overall = overall_status(ordered)
    database_facts = ctx.recalled("database_facts")
    blocked = [
        f"{result.name}: {result.reason}"
        for result in ordered
        if result.status == CheckStatus.BLOCKED
    ]
    report = DiagnosticReport(
        overall=overall,
        mode="deep" if deep else "fast",
        generated_at=ctx.utc_now(),
        repository=str(ctx.repository),
        git_sha=ctx.git_sha,
        git_dirty=ctx.git_dirty,
        app_version=_app_version(),
        app_env=ctx.settings.app_env if ctx.settings is not None else "unknown",
        api_endpoint=ctx.api_url,
        web_endpoint=ctx.web_url,
        database_revision=database_facts.revision if database_facts is not None else None,
        checks=ordered,
        ready_to_run=overall != CheckStatus.BLOCKED,
        blocked_reasons=tuple(blocked[:MAX_BLOCKED_REASONS]),
    )
    return DiagnosticsRun(report, ctx)


def _app_version() -> str | None:
    from importlib import metadata

    try:
        return metadata.version(check_functions.APP_PACKAGE)
    except metadata.PackageNotFoundError:
        return None


def report_payload(report: DiagnosticReport, secret_values: tuple[str, ...] = ()) -> dict[str, Any]:
    """Serialize the report with defensive secret scrubbing on top of allowlisting."""

    return redact_value(report.to_dict(), secret_values)


def render_json(report: DiagnosticReport, secret_values: tuple[str, ...] = ()) -> str:
    return json.dumps(
        report_payload(report, secret_values), indent=2, sort_keys=True, ensure_ascii=False
    )


def render_text(report: DiagnosticReport, secret_values: tuple[str, ...] = ()) -> str:
    lines = [
        f"Jarvis runtime doctor ({report.mode} mode) - {report.generated_at}",
        f"Overall: {report.overall.value}"
        + (
            f" (ready to run: {'yes' if report.ready_to_run else 'no'})"
            if report.ready_to_run is not None
            else ""
        ),
        f"Repository: {report.repository}",
        f"Git SHA: {report.git_sha or 'unknown'}"
        + (" (local changes)" if report.git_dirty else ""),
        f"App: {report.app_version or 'unknown'} env={report.app_env}",
        f"API endpoint: {report.api_endpoint or 'unknown'}",
        f"Web endpoint: {report.web_endpoint or 'unknown'}",
        "",
    ]
    for result in report.checks:
        reason = redact_text(result.reason, secret_values)
        remediation = redact_text(result.remediation, secret_values) if result.remediation else None
        lines.append(f"  {result.status.value.upper():8} {result.name}: {reason}")
        if remediation:
            lines.append(f"           fix: {remediation}")
    lines.append("")
    if report.blocked_reasons:
        lines.append("Blocked by:")
        for reason in report.blocked_reasons:
            lines.append(f"  - {redact_text(reason, secret_values)}")
    else:
        lines.append("No blocking conditions reported.")
    return "\n".join(lines)


def render_markdown(report: DiagnosticReport, secret_values: tuple[str, ...] = ()) -> str:
    emoji = {
        CheckStatus.HEALTHY: "healthy",
        CheckStatus.DEGRADED: "degraded",
        CheckStatus.BLOCKED: "blocked",
        CheckStatus.DISABLED: "disabled",
        CheckStatus.UNKNOWN: "unknown",
    }
    lines = [
        "# Jarvis runtime diagnostic",
        "",
        f"- **Overall:** {report.overall.value}",
        f"- **Generated:** {report.generated_at} ({report.mode} mode)",
        f"- **Repository:** `{report.repository}`",
        f"- **Git SHA:** `{report.git_sha or 'unknown'}`"
        + (" (local changes present)" if report.git_dirty else ""),
        f"- **Application:** {report.app_version or 'unknown'} (env: {report.app_env})",
        f"- **Endpoints:** API `{report.api_endpoint or 'unknown'}`, web `{report.web_endpoint or 'unknown'}`",
        f"- **Database revision:** `{report.database_revision or 'unknown'}`",
        f"- **Ready to run:** {'yes' if report.ready_to_run else 'no'}",
        "",
        "## Checks",
        "",
        "| Check | Status | Reason | Remediation |",
        "| --- | --- | --- | --- |",
    ]
    for result in report.checks:
        remediation = redact_text(result.remediation or "", secret_values).replace("|", "\\|")
        reason = redact_text(result.reason, secret_values).replace("|", "\\|")
        lines.append(f"| `{result.name}` | {emoji[result.status]} | {reason} | {remediation} |")
    lines.extend(["", "## Identifiers", ""])
    for result in report.checks:
        if not result.identifiers:
            continue
        lines.append(f"### {result.name}")
        lines.append("")
        for key, value in redact_value(result.identifiers, secret_values).items():
            lines.append(f"- `{key}`: `{json.dumps(value, default=str, sort_keys=True)}`")
        lines.append("")
    if report.blocked_reasons:
        lines.extend(["## Blocking conditions", ""])
        lines.extend(f"- {redact_text(reason, secret_values)}" for reason in report.blocked_reasons)
        lines.append("")
    lines.extend(
        [
            "## Redaction",
            "",
            "This report contains only allowlisted diagnostic fields. Secrets, credentials, "
            "raw environment variables, file contents, and workspace contents are never included.",
            "",
        ]
    )
    return "\n".join(lines)


def write_report_files(
    report: DiagnosticReport,
    directory: Path,
    secret_values: tuple[str, ...] = (),
) -> tuple[Path, Path]:
    """Write the JSON and Markdown diagnostic bundle (overwrites prior reports)."""

    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / REPORT_JSON_NAME
    markdown_path = directory / REPORT_MARKDOWN_NAME
    json_path.write_text(render_json(report, secret_values) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown(report, secret_values), encoding="utf-8")
    return json_path, markdown_path
