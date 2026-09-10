"""Machine-readable evidence and human-readable summaries for acceptance runs.

Every acceptance scenario produces an :class:`AcceptanceEvidence` record
sufficient to answer, without re-running anything:

- exact repo SHA, scenario name, fixture-vs-real inference mode,
  provider/model identity, start/end timestamps, pass/fail,
- expected vs. actual conditions for every check,
- model call counts, retries, execution/task/runtime/checkpoint ids,
- the final result and the failure reason when applicable.

Evidence is JSON-serializable and secret-scrubbed. The Markdown summary is a
concise human projection of the same record — never a separate source of
truth.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.autonomy.events import TimelineEvent, scrub_text

EVIDENCE_SCHEMA_VERSION = "1.0"


class InferenceIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: str = Field(description="'fixture' or 'installed_local'; never anything else.")
    provider: str
    model: str


class EvidenceCounts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model_calls: int = 0
    repairs: int = 0
    attempts: int = 0
    tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    retries: int = 0


class EvidenceIds(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    execution_ids: tuple[str, ...] = ()
    task_ids: tuple[str, ...] = ()
    runtime_ids: tuple[str, ...] = ()
    checkpoint_ids: tuple[str, ...] = ()
    command_ids: tuple[str, ...] = ()


class AcceptanceCheck(BaseModel):
    """One expected-vs-actual condition with its deterministic verdict."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    expected: str
    actual: str
    passed: bool


class AcceptanceEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = EVIDENCE_SCHEMA_VERSION
    repo_sha: str
    scenario: str
    inference: InferenceIdentity
    started_at: datetime
    ended_at: datetime
    verdict: str = Field(description="'pass' or 'fail'.")
    terminal_state: str
    failure_reason: str | None = None
    bounds: dict[str, Any] = Field(default_factory=dict)
    counts: EvidenceCounts = Field(default_factory=EvidenceCounts)
    ids: EvidenceIds = Field(default_factory=EvidenceIds)
    provenance: tuple[dict[str, str], ...] = ()
    checks: tuple[AcceptanceCheck, ...] = ()
    timeline: tuple[TimelineEvent, ...] = ()
    final_result: str = ""


def make_check(name: str, expected: str, actual: str, passed: bool) -> AcceptanceCheck:
    return AcceptanceCheck(
        name=name,
        expected=scrub_text(expected)[:1000],
        actual=scrub_text(actual)[:1000],
        passed=passed,
    )


def write_evidence_json(path: str | Path, evidence: AcceptanceEvidence) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(evidence.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target


def read_evidence_json(path: str | Path) -> AcceptanceEvidence:
    return AcceptanceEvidence.model_validate_json(Path(path).read_text(encoding="utf-8"))


def render_markdown_summary(evidence: AcceptanceEvidence) -> str:
    """Render a concise human-readable projection of the evidence record."""
    lines = [
        f"# Autonomy acceptance: {evidence.scenario}",
        "",
        f"- verdict: **{evidence.verdict}**",
        f"- terminal_state: `{evidence.terminal_state}`",
        f"- repo_sha: `{evidence.repo_sha}`",
        "- inference: "
        f"`{evidence.inference.mode}` provider=`{evidence.inference.provider}` "
        f"model=`{evidence.inference.model}`",
        f"- window: `{evidence.started_at.isoformat()}` → `{evidence.ended_at.isoformat()}`",
        f"- model_calls={evidence.counts.model_calls} repairs={evidence.counts.repairs} "
        f"attempts={evidence.counts.attempts} retries={evidence.counts.retries}",
        f"- tasks={evidence.counts.tasks} completed={evidence.counts.completed_tasks} "
        f"failed={evidence.counts.failed_tasks}",
    ]
    if evidence.failure_reason:
        lines.append(f"- failure_reason: `{scrub_text(evidence.failure_reason)[:300]}`")
    lines += ["", "## Provenance (production vs fixture)", ""]
    for entry in evidence.provenance:
        lines.append(
            f"- `{entry.get('stage')}`: **{entry.get('implementation')}** — {entry.get('detail')}"
        )
    lines += ["", "## Checks", ""]
    for check in evidence.checks:
        mark = "PASS" if check.passed else "FAIL"
        lines.append(f"- [{mark}] `{check.name}`")
        lines.append(f"  - expected: {check.expected}")
        lines.append(f"  - actual: {check.actual}")
    lines += ["", "## Final result", "", evidence.final_result or "(none)", ""]
    lines += ["## Timeline (abridged)", ""]
    for entry in evidence.timeline:
        target = f" {entry.node_id}" if entry.node_id else ""
        attempt = f" attempt={entry.attempt_number}" if entry.attempt_number else ""
        lines.append(f"- `{entry.seq:03d}` `{entry.stage.value}` `{entry.event}`{target}{attempt}")
    lines.append("")
    return "\n".join(lines)


def write_markdown_summary(path: str | Path, evidence: AcceptanceEvidence) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_markdown_summary(evidence), encoding="utf-8")
    return target
