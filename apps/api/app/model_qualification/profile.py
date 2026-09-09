"""Machine-readable qualification profiles, comparisons, and recommendations.

The profile is the durable evidence artifact: it records the exact model
identity, provider, inference mode (``fixture`` vs ``installed_local``),
qualification-policy version, evaluation-suite version and digest, per-role
verdicts, gate tables, metrics, operational signals, and warnings.

Design rules:

- No response bodies are stored. Only case ids, rates, gate outcomes and
  scrubbed free text, so a report can never leak model output or secrets.
- Fixture evidence is always labelled. A fixture profile carries
  :data:`FIXTURE_WARNING` and renders ``inference_mode = fixture``.
- Unavailable is a first-class status. ``status="unavailable"`` with
  ``not_evaluated`` roles never becomes a quality score.

The recommendation map is **evidence only**. Nothing here changes production
routing; a later milestone must explicitly consume an approved profile.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.autonomy.events import scrub_text
from app.model_qualification.metrics import EVALUATION_SUITE_VERSION
from app.model_qualification.policy import QUALIFICATION_POLICY_VERSION
from app.model_qualification.roles import QualificationRole, role_names
from app.model_qualification.scoring import GateResult, QualificationLevel, RoleAssessment

PROFILE_SCHEMA_VERSION = "1.0"

FIXTURE_MODE = "fixture"
INSTALLED_LOCAL_MODE = "installed_local"
INFERENCE_MODES = (FIXTURE_MODE, INSTALLED_LOCAL_MODE)

STATUS_EVALUATED = "evaluated"
STATUS_PARTIAL = "partial"
STATUS_UNAVAILABLE = "unavailable"
STATUS_SKIPPED = "skipped"
PROFILE_STATUSES = (STATUS_EVALUATED, STATUS_PARTIAL, STATUS_UNAVAILABLE, STATUS_SKIPPED)

FIXTURE_WARNING = "fixture evidence: deterministic scripted responses, NOT real model performance"
RECOMMENDATION_NOTE = (
    "Recommendations are evidence only. This artifact never changes production "
    "routing, agent models, manager authority, permissions, or coordinator state."
)


def _scrub_tuple(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    return tuple(scrub_text(str(value))[:400] for value in values)


class GateRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    metric: str
    direction: str
    threshold: float
    observed: float | None = None
    passed: bool | None = None
    mandatory: bool
    status: str
    description: str = ""


class RoleRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: str
    qualification: str
    score: float | None = None
    mandatory_gates_passed: bool | None = None
    gates: tuple[GateRecord, ...] = ()
    strengths: tuple[str, ...] = ()
    weaknesses: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    evidence: dict[str, Any] = Field(default_factory=dict)

    @field_validator("role")
    @classmethod
    def validate_role(cls, value: str) -> str:
        if value not in role_names():
            raise ValueError(f"unknown qualification role: {value!r}")
        return value

    @field_validator("qualification")
    @classmethod
    def validate_qualification(cls, value: str) -> str:
        if value not in tuple(QualificationLevel):
            raise ValueError(f"unknown qualification level: {value!r}")
        return value


class ModelProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = PROFILE_SCHEMA_VERSION
    provider: str
    model: str
    inference_mode: str
    status: str
    evaluated_at: datetime
    qualification_policy_version: str = QUALIFICATION_POLICY_VERSION
    evaluation_suite_version: str = EVALUATION_SUITE_VERSION
    evaluation_suite_digest: str
    repo_sha: str = "unknown"
    run: dict[str, Any] = Field(default_factory=dict)
    roles: dict[str, RoleRecord] = Field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @field_validator("inference_mode")
    @classmethod
    def validate_inference_mode(cls, value: str) -> str:
        if value not in INFERENCE_MODES:
            raise ValueError(f"inference_mode must be one of {list(INFERENCE_MODES)}")
        return value

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        if value not in PROFILE_STATUSES:
            raise ValueError(f"status must be one of {list(PROFILE_STATUSES)}")
        return value

    @model_validator(mode="after")
    def validate_role_keys(self) -> ModelProfile:
        unknown = [key for key in self.roles if key not in role_names()]
        if unknown:
            raise ValueError(f"unknown qualification role(s) in profile: {sorted(unknown)}")
        return self

    @model_validator(mode="after")
    def validate_fixture_labelling(self) -> ModelProfile:
        if self.inference_mode == FIXTURE_MODE and FIXTURE_WARNING not in self.warnings:
            raise ValueError("fixture profiles must carry the fixture warning")
        if self.inference_mode == INSTALLED_LOCAL_MODE and FIXTURE_WARNING in self.warnings:
            raise ValueError("installed-local profiles must not carry the fixture warning")
        return self

    @property
    def role_levels(self) -> dict[str, str]:
        return {role: record.qualification for role, record in self.roles.items()}


class RankedCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rank: int
    model: str
    provider: str
    inference_mode: str
    qualification: str
    score: float | None = None
    mandatory_gates_passed: bool | None = None
    tie_breaker: str


class RoleComparison(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: str
    candidates: tuple[RankedCandidate, ...] = ()


class RoleRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: str
    model: str | None = None
    provider: str | None = None
    inference_mode: str | None = None
    qualification: str | None = None
    score: float | None = None
    reason: str


class QualificationRun(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = PROFILE_SCHEMA_VERSION
    generated_at: datetime
    qualification_policy_version: str = QUALIFICATION_POLICY_VERSION
    evaluation_suite_version: str = EVALUATION_SUITE_VERSION
    evaluation_suite_digest: str
    repo_sha: str = "unknown"
    inference_modes: tuple[str, ...] = ()
    models: tuple[ModelProfile, ...] = ()
    comparisons: tuple[RoleComparison, ...] = ()
    recommendations: tuple[RoleRecommendation, ...] = ()
    warnings: tuple[str, ...] = ()
    notes: tuple[str, ...] = (RECOMMENDATION_NOTE,)


def gate_record(gate: GateResult) -> GateRecord:
    return GateRecord(
        key=gate.key,
        metric=gate.metric,
        direction=gate.direction,
        threshold=gate.threshold,
        observed=gate.observed,
        passed=gate.passed,
        mandatory=gate.mandatory,
        status=gate.status,
        description=gate.description,
    )


def role_record(assessment: RoleAssessment) -> RoleRecord:
    return RoleRecord(
        role=assessment.role.value,
        qualification=assessment.qualification.value,
        score=assessment.score,
        mandatory_gates_passed=assessment.mandatory_gates_passed,
        gates=tuple(gate_record(gate) for gate in assessment.gates),
        strengths=_scrub_tuple(assessment.strengths),
        weaknesses=_scrub_tuple(assessment.weaknesses),
        reasons=_scrub_tuple(assessment.reasons),
        evidence={
            "cases": list(assessment.expected_case_ids),
            "evaluated_cases": list(assessment.evaluated_case_ids),
            "expected_cases": len(assessment.expected_case_ids),
            "unavailable_cases": list(assessment.unavailable_case_ids),
            "failure_codes": dict(assessment.failure_codes),
            "metrics": _scrub_metrics(assessment.metrics),
            "operational": _scrub_metrics(assessment.operational),
        },
    )


def _scrub_metrics(values: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in values.items():
        cleaned[str(key)] = _scrub_metric(value)
    return cleaned


def _scrub_metric(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return scrub_text(value)[:300]
    if isinstance(value, dict):
        return {str(key)[:80]: _scrub_metric(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub_metric(item) for item in value][:64]
    return scrub_text(str(value))[:300]


def build_profile(
    *,
    provider: str,
    model: str,
    inference_mode: str,
    assessments: dict[QualificationRole, RoleAssessment],
    evaluated_at: datetime,
    evaluation_suite_digest: str,
    repo_sha: str = "unknown",
    run: dict[str, Any] | None = None,
    warnings: tuple[str, ...] = (),
    notes: tuple[str, ...] = (),
) -> ModelProfile:
    """Assemble a profile and derive its status from the role verdicts."""
    records = {role.value: role_record(assessment) for role, assessment in assessments.items()}
    active_warnings = list(warnings)
    active_notes = list(notes)
    if inference_mode == FIXTURE_MODE and FIXTURE_WARNING not in active_warnings:
        active_warnings.append(FIXTURE_WARNING)

    if not records:
        status = STATUS_SKIPPED
    else:
        levels = [record.qualification for record in records.values()]
        if all(level == QualificationLevel.NOT_EVALUATED.value for level in levels):
            status = STATUS_UNAVAILABLE
        elif any(level == QualificationLevel.NOT_EVALUATED.value for level in levels):
            status = STATUS_PARTIAL
        else:
            status = STATUS_EVALUATED
    active_notes.append(RECOMMENDATION_NOTE)

    return ModelProfile(
        provider=provider,
        model=model,
        inference_mode=inference_mode,
        status=status,
        evaluated_at=evaluated_at,
        evaluation_suite_digest=evaluation_suite_digest,
        repo_sha=repo_sha,
        run=dict(run or {}),
        roles=records,
        warnings=_scrub_tuple(active_warnings),
        notes=_scrub_tuple(active_notes),
    )


def unavailable_profile(
    *,
    provider: str,
    model: str,
    roles: tuple[QualificationRole, ...],
    evaluated_at: datetime,
    evaluation_suite_digest: str,
    repo_sha: str = "unknown",
    reason: str,
) -> ModelProfile:
    """A profile for a run that never reached a model (never a quality verdict)."""
    assessments: dict[QualificationRole, RoleAssessment] = {}
    for role in roles:
        assessments[role] = RoleAssessment(
            role=role,
            qualification=QualificationLevel.NOT_EVALUATED,
            score=None,
            mandatory_gates_passed=None,
            gates=(),
            strengths=(),
            weaknesses=(),
            reasons=(f"evaluation unavailable: {reason}",),
            evaluated_case_ids=(),
            expected_case_ids=(),
            unavailable_case_ids=(),
            metrics={},
            operational={},
            failure_codes={},
        )
    return build_profile(
        provider=provider,
        model=model,
        inference_mode=INSTALLED_LOCAL_MODE,
        assessments=assessments,
        evaluated_at=evaluated_at,
        evaluation_suite_digest=evaluation_suite_digest,
        repo_sha=repo_sha,
        run={"unavailable_reason": reason},
        warnings=(f"installed-local qualification unavailable: {reason}",),
    )


def write_profile_json(path: str | Path, profile: ModelProfile) -> Path:
    return _write_json(path, profile)


def read_profile_json(path: str | Path) -> ModelProfile:
    return ModelProfile.model_validate_json(Path(path).read_text(encoding="utf-8"))


def write_run_json(path: str | Path, run: QualificationRun) -> Path:
    return _write_json(path, run)


def read_run_json(path: str | Path) -> QualificationRun:
    return QualificationRun.model_validate_json(Path(path).read_text(encoding="utf-8"))


def _write_json(path: str | Path, payload: BaseModel) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target


def render_profile_markdown(profiles: tuple[ModelProfile, ...], run: QualificationRun) -> str:
    lines = [
        "# Local-model qualification",
        "",
        f"- generated_at: `{run.generated_at.isoformat()}`",
        f"- repo_sha: `{run.repo_sha}`",
        f"- qualification_policy_version: `{run.qualification_policy_version}`",
        f"- evaluation_suite_version: `{run.evaluation_suite_version}`",
        f"- evaluation_suite_digest: `{run.evaluation_suite_digest}`",
        f"- inference_modes: `{list(run.inference_modes)}`",
        "",
        "## Models",
        "",
    ]
    for profile in profiles:
        lines.append(
            f"### `{profile.model}` — provider `{profile.provider}` (status `{profile.status}`)"
        )
        lines.append("")
        lines.append(f"- inference_mode = {profile.inference_mode}")
        if profile.inference_mode == FIXTURE_MODE:
            lines.append(f"- **{FIXTURE_WARNING}**")
        lines.append(f"- evaluated_at: `{profile.evaluated_at.isoformat()}`")
        for warning in profile.warnings:
            lines.append(f"- warning: {warning}")
        lines += [
            "",
            "| role | qualification | score | mandatory gates | reasons |",
            "| --- | --- | --- | --- | --- |",
        ]
        for role in sorted(profile.roles):
            record = profile.roles[role]
            score = "n/a" if record.score is None else f"{record.score:.3f}"
            gates = (
                "n/a"
                if record.mandatory_gates_passed is None
                else str(record.mandatory_gates_passed)
            )
            reason = record.reasons[0] if record.reasons else ""
            lines.append(
                f"| `{role}` | **{record.qualification}** | {score} | {gates} | {reason} |"
            )
        lines.append("")
        for role in sorted(profile.roles):
            record = profile.roles[role]
            # "failed" and "not evaluated" are different facts; never conflate them
            failed = [gate for gate in record.gates if gate.status == "failed"]
            unmeasured = [gate for gate in record.gates if gate.status == "not_evaluated"]
            if failed:
                lines.append(
                    f"- `{role}` failed gates: "
                    + ", ".join(
                        f"{gate.key}{' (MANDATORY)' if gate.mandatory else ''}="
                        f"{gate.observed if gate.observed is not None else 'n/a'} "
                        f"vs {gate.direction} {gate.threshold:g}"
                        for gate in failed
                    )
                )
            if unmeasured:
                lines.append(
                    f"- `{role}` gates not measured: "
                    + ", ".join(
                        f"{gate.key}{' (MANDATORY)' if gate.mandatory else ''}"
                        f" ({gate.metric} unavailable in this run)"
                        for gate in unmeasured
                    )
                )
        lines.append("")

    lines += ["## Role ranking", ""]
    for comparison in run.comparisons:
        lines.append(f"### {comparison.role}")
        lines.append("")
        if not comparison.candidates:
            lines.append("- no candidates evaluated")
            lines.append("")
            continue
        for candidate in comparison.candidates:
            score = "n/a" if candidate.score is None else f"{candidate.score:.3f}"
            lines.append(
                f"{candidate.rank}. `{candidate.model}` ({candidate.provider}, "
                f"{candidate.inference_mode}) — {candidate.qualification} — {score}"
            )
        lines.append("")

    lines += ["## Recommended role map (evidence only)", ""]
    lines.append("```json")
    lines.append(
        json.dumps(
            {item.role: item.model for item in run.recommendations},
            indent=2,
            sort_keys=True,
        )
    )
    lines.append("```")
    lines.append("")
    for item in run.recommendations:
        lines.append(f"- `{item.role}`: `{item.model or 'none'}` — {item.reason}")
    lines += ["", f"> {RECOMMENDATION_NOTE}", ""]
    return "\n".join(lines)


def write_markdown_summary(
    path: str | Path, profiles: tuple[ModelProfile, ...], run: QualificationRun
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_profile_markdown(profiles, run), encoding="utf-8")
    return target


__all__ = [
    "FIXTURE_MODE",
    "FIXTURE_WARNING",
    "INSTALLED_LOCAL_MODE",
    "INFERENCE_MODES",
    "PROFILE_SCHEMA_VERSION",
    "PROFILE_STATUSES",
    "RECOMMENDATION_NOTE",
    "STATUS_EVALUATED",
    "STATUS_PARTIAL",
    "STATUS_SKIPPED",
    "STATUS_UNAVAILABLE",
    "GateRecord",
    "ModelProfile",
    "QualificationRun",
    "RankedCandidate",
    "RoleComparison",
    "RoleRecommendation",
    "RoleRecord",
    "build_profile",
    "gate_record",
    "read_profile_json",
    "read_run_json",
    "render_profile_markdown",
    "role_record",
    "unavailable_profile",
    "write_markdown_summary",
    "write_profile_json",
    "write_run_json",
]
