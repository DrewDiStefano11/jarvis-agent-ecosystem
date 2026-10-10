"""Offline qualification report comparison for Jarvis AI roles.

Compares two previously generated model qualification artifacts (profiles or runs)
to determine whether a model's role qualification improved, regressed, remained unchanged,
lacks sufficient evidence, or is non-comparable.

Rules:
- Reuses existing ModelProfile and QualificationRun schemas.
- Rejects numerical comparison between fixture mode and installed_local mode.
- Identifies version/digest/policy mismatches and flags them as non-comparable.
- Compares qualification level, role scores, mandatory/advisory gates, and evidence completeness.
- Sorts comparison items deterministically by (model, provider, role).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.autonomy.events import scrub_text
from app.model_qualification.profile import (
    ModelProfile,
    QualificationRun,
    read_profile_json,
    read_run_json,
)
from app.model_qualification.roles import role_names
from app.model_qualification.scoring import LEVEL_RANK, QualificationLevel

COMPARISON_SCHEMA_VERSION = "1.0"
SCORE_DELTA_THRESHOLD = 0.005
MAX_ARTIFACT_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB limit for input files

VERDICT_IMPROVED = "improved"
VERDICT_REGRESSED = "regressed"
VERDICT_UNCHANGED = "unchanged"
VERDICT_INSUFFICIENT_EVIDENCE = "insufficient_evidence"
VERDICT_NON_COMPARABLE = "non_comparable"

COMPARISON_VERDICTS = (
    VERDICT_IMPROVED,
    VERDICT_REGRESSED,
    VERDICT_UNCHANGED,
    VERDICT_INSUFFICIENT_EVIDENCE,
    VERDICT_NON_COMPARABLE,
)


def _scrub_str_tuple(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    return tuple(scrub_text(str(v))[:400] for v in values)


class RoleQualificationComparison(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str
    provider: str
    role: str
    comparable: bool
    verdict: str
    reason: str
    baseline_inference_mode: str | None = None
    target_inference_mode: str | None = None
    baseline_qualification: str | None = None
    target_qualification: str | None = None
    baseline_score: float | None = None
    target_score: float | None = None
    score_delta: float | None = None
    meaningful_score_change: bool = False
    mandatory_gate_changes: tuple[str, ...] = ()
    previously_passing_failed_gates: tuple[str, ...] = ()
    newly_passing_gates: tuple[str, ...] = ()
    missing_or_incomplete_evidence: tuple[str, ...] = ()

    @field_validator("role")
    @classmethod
    def validate_role(cls, value: str) -> str:
        if value not in role_names():
            raise ValueError(f"unknown qualification role: {value!r}")
        return value

    @field_validator("verdict")
    @classmethod
    def validate_verdict(cls, value: str) -> str:
        if value not in COMPARISON_VERDICTS:
            raise ValueError(f"unknown comparison verdict: {value!r}")
        return value


class QualificationComparisonReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = COMPARISON_SCHEMA_VERSION
    compared_at: datetime
    baseline_source: str
    target_source: str
    baseline_inference_modes: tuple[str, ...] = ()
    target_inference_modes: tuple[str, ...] = ()
    comparisons: tuple[RoleQualificationComparison, ...] = ()
    summary: dict[str, int] = Field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


def load_qualification_artifact(
    source: str | Path | dict[str, Any] | list[Any] | ModelProfile | QualificationRun,
) -> tuple[ModelProfile, ...]:
    """Load and validate qualification artifact(s) from a file path, dict, list, or model object."""
    if isinstance(source, ModelProfile):
        return (source,)
    if isinstance(source, QualificationRun):
        return source.models

    if isinstance(source, (str, Path)):
        file_path = Path(source)
        if not file_path.exists():
            raise ValueError(f"qualification artifact path does not exist: {file_path}")
        if file_path.is_file() and file_path.stat().st_size > MAX_ARTIFACT_SIZE_BYTES:
            raise ValueError(
                f"qualification artifact exceeds maximum allowed size ({MAX_ARTIFACT_SIZE_BYTES} bytes): {file_path}"
            )

        # Attempt reading as QualificationRun first, then ModelProfile
        try:
            run = read_run_json(file_path)
            return run.models
        except Exception:
            pass

        try:
            profile = read_profile_json(file_path)
            return (profile,)
        except Exception:
            pass

        # Try parsing as JSON list
        try:
            raw_text = file_path.read_text(encoding="utf-8")
            raw_data = json.loads(raw_text)
            if isinstance(raw_data, list):
                return tuple(ModelProfile.model_validate(item) for item in raw_data)
        except Exception as exc:
            raise ValueError(
                f"failed to parse qualification artifact at {file_path}: {exc}"
            ) from exc

        raise ValueError(f"unrecognized qualification artifact format at {file_path}")

    if isinstance(source, dict):
        if "models" in source:
            return QualificationRun.model_validate(source).models
        if "model" in source and "provider" in source and "roles" in source:
            return (ModelProfile.model_validate(source),)
        raise ValueError("dictionary input does not match QualificationRun or ModelProfile schema")

    if isinstance(source, (list, tuple)):
        return tuple(ModelProfile.model_validate(item) for item in source)

    raise ValueError(f"unsupported qualification artifact source type: {type(source).__name__}")


def compare_qualification_artifacts(
    baseline_source: str | Path | dict[str, Any] | list[Any] | ModelProfile | QualificationRun,
    target_source: str | Path | dict[str, Any] | list[Any] | ModelProfile | QualificationRun,
    *,
    compared_at: datetime | None = None,
    roles: tuple[str, ...] | list[str] | None = None,
    model_names: tuple[str, ...] | list[str] | None = None,
    baseline_source_name: str = "baseline",
    target_source_name: str = "target",
) -> QualificationComparisonReport:
    """Compare baseline and target qualification reports and produce structured comparison output."""
    compared_time = compared_at or datetime.now(UTC)
    baseline_profiles = load_qualification_artifact(baseline_source)
    target_profiles = load_qualification_artifact(target_source)

    baseline_modes = tuple(sorted(set(p.inference_mode for p in baseline_profiles)))
    target_modes = tuple(sorted(set(p.inference_mode for p in target_profiles)))

    b_map: dict[tuple[str, str], ModelProfile] = {
        (p.model, p.provider): p for p in baseline_profiles
    }
    t_map: dict[tuple[str, str], ModelProfile] = {(p.model, p.provider): p for p in target_profiles}

    all_keys = sorted(set(b_map.keys()) | set(t_map.keys()))
    if model_names:
        allowed_models = set(model_names)
        all_keys = [k for k in all_keys if k[0] in allowed_models]

    allowed_roles = set(roles) if roles else None

    comparisons: list[RoleQualificationComparison] = []
    warnings: list[str] = []

    for model, provider in all_keys:
        b_prof = b_map.get((model, provider))
        t_prof = t_map.get((model, provider))

        if b_prof is None and t_prof is not None:
            candidate_roles = sorted(t_prof.roles.keys())
            if allowed_roles:
                candidate_roles = [r for r in candidate_roles if r in allowed_roles]
            for role in candidate_roles:
                t_role = t_prof.roles.get(role)
                comparisons.append(
                    RoleQualificationComparison(
                        model=model,
                        provider=provider,
                        role=role,
                        comparable=False,
                        verdict=VERDICT_INSUFFICIENT_EVIDENCE,
                        reason=f"model '{model}' ({provider}) is missing in baseline report",
                        baseline_inference_mode=None,
                        target_inference_mode=t_prof.inference_mode,
                        baseline_qualification=None,
                        target_qualification=t_role.qualification if t_role else None,
                        baseline_score=None,
                        target_score=t_role.score if t_role else None,
                        missing_or_incomplete_evidence=(
                            f"model '{model}' missing in baseline report",
                        ),
                    )
                )
            continue

        if t_prof is None and b_prof is not None:
            candidate_roles = sorted(b_prof.roles.keys())
            if allowed_roles:
                candidate_roles = [r for r in candidate_roles if r in allowed_roles]
            for role in candidate_roles:
                b_role = b_prof.roles.get(role)
                comparisons.append(
                    RoleQualificationComparison(
                        model=model,
                        provider=provider,
                        role=role,
                        comparable=False,
                        verdict=VERDICT_INSUFFICIENT_EVIDENCE,
                        reason=f"model '{model}' ({provider}) is missing in target report",
                        baseline_inference_mode=b_prof.inference_mode,
                        target_inference_mode=None,
                        baseline_qualification=b_role.qualification if b_role else None,
                        target_qualification=None,
                        baseline_score=b_role.score if b_role else None,
                        target_score=None,
                        missing_or_incomplete_evidence=(
                            f"model '{model}' missing in target report",
                        ),
                    )
                )
            continue

        assert b_prof is not None and t_prof is not None

        # Check model-level comparability
        non_comp_reasons: list[str] = []
        if b_prof.inference_mode != t_prof.inference_mode:
            non_comp_reasons.append(
                f"mismatched inference modes: baseline is '{b_prof.inference_mode}' while target is '{t_prof.inference_mode}' "
                "(fixture evidence cannot be numerically compared to real model evidence)"
            )

        if b_prof.qualification_policy_version != t_prof.qualification_policy_version:
            non_comp_reasons.append(
                f"mismatched qualification policy versions: baseline='{b_prof.qualification_policy_version}' vs target='{t_prof.qualification_policy_version}'"
            )

        if b_prof.evaluation_suite_version != t_prof.evaluation_suite_version:
            non_comp_reasons.append(
                f"mismatched evaluation suite versions: baseline='{b_prof.evaluation_suite_version}' vs target='{t_prof.evaluation_suite_version}'"
            )

        if b_prof.evaluation_suite_digest != t_prof.evaluation_suite_digest:
            non_comp_reasons.append(
                f"mismatched evaluation suite digests: baseline='{b_prof.evaluation_suite_digest}' vs target='{t_prof.evaluation_suite_digest}'"
            )

        candidate_roles = sorted(set(b_prof.roles.keys()) | set(t_prof.roles.keys()))
        if allowed_roles:
            candidate_roles = [r for r in candidate_roles if r in allowed_roles]

        if non_comp_reasons:
            combined_reason = "; ".join(non_comp_reasons)
            for role in candidate_roles:
                b_role = b_prof.roles.get(role)
                t_role = t_prof.roles.get(role)
                comparisons.append(
                    RoleQualificationComparison(
                        model=model,
                        provider=provider,
                        role=role,
                        comparable=False,
                        verdict=VERDICT_NON_COMPARABLE,
                        reason=combined_reason,
                        baseline_inference_mode=b_prof.inference_mode,
                        target_inference_mode=t_prof.inference_mode,
                        baseline_qualification=b_role.qualification if b_role else None,
                        target_qualification=t_role.qualification if t_role else None,
                        baseline_score=b_role.score if b_role else None,
                        target_score=t_role.score if t_role else None,
                    )
                )
            continue

        # Perform comparable role evaluations
        for role in candidate_roles:
            b_role = b_prof.roles.get(role)
            t_role = t_prof.roles.get(role)

            if b_role is None and t_role is not None:
                comparisons.append(
                    RoleQualificationComparison(
                        model=model,
                        provider=provider,
                        role=role,
                        comparable=True,
                        verdict=VERDICT_INSUFFICIENT_EVIDENCE,
                        reason=f"role '{role}' missing in baseline report",
                        baseline_inference_mode=b_prof.inference_mode,
                        target_inference_mode=t_prof.inference_mode,
                        baseline_qualification=None,
                        target_qualification=t_role.qualification,
                        baseline_score=None,
                        target_score=t_role.score,
                        missing_or_incomplete_evidence=(
                            f"role '{role}' missing in baseline report",
                        ),
                    )
                )
                continue

            if t_role is None and b_role is not None:
                comparisons.append(
                    RoleQualificationComparison(
                        model=model,
                        provider=provider,
                        role=role,
                        comparable=True,
                        verdict=VERDICT_INSUFFICIENT_EVIDENCE,
                        reason=f"role '{role}' missing in target report",
                        baseline_inference_mode=b_prof.inference_mode,
                        target_inference_mode=t_prof.inference_mode,
                        baseline_qualification=b_role.qualification,
                        target_qualification=None,
                        baseline_score=b_role.score,
                        target_score=None,
                        missing_or_incomplete_evidence=(f"role '{role}' missing in target report",),
                    )
                )
                continue

            assert b_role is not None and t_role is not None

            # Check if either role is not_evaluated
            b_not_eval = b_role.qualification == QualificationLevel.NOT_EVALUATED.value
            t_not_eval = t_role.qualification == QualificationLevel.NOT_EVALUATED.value

            if b_not_eval or t_not_eval:
                evidence_issues: list[str] = []
                if b_not_eval:
                    evidence_issues.append(f"baseline role '{role}' is not_evaluated")
                if t_not_eval:
                    evidence_issues.append(f"target role '{role}' is not_evaluated")
                comparisons.append(
                    RoleQualificationComparison(
                        model=model,
                        provider=provider,
                        role=role,
                        comparable=True,
                        verdict=VERDICT_INSUFFICIENT_EVIDENCE,
                        reason="; ".join(evidence_issues),
                        baseline_inference_mode=b_prof.inference_mode,
                        target_inference_mode=t_prof.inference_mode,
                        baseline_qualification=b_role.qualification,
                        target_qualification=t_role.qualification,
                        baseline_score=b_role.score,
                        target_score=t_role.score,
                        missing_or_incomplete_evidence=_scrub_str_tuple(evidence_issues),
                    )
                )
                continue

            # Calculate score delta
            b_score = b_role.score
            t_score = t_role.score
            if b_score is not None and t_score is not None:
                delta = round(t_score - b_score, 4)
                meaningful = abs(delta) >= SCORE_DELTA_THRESHOLD
            else:
                delta = None
                meaningful = False

            # Gate comparisons
            b_gates = {g.key: g for g in b_role.gates}
            t_gates = {g.key: g for g in t_role.gates}
            all_gate_keys = sorted(set(b_gates.keys()) | set(t_gates.keys()))

            mandatory_changes: list[str] = []
            previously_passing_failed: list[str] = []
            newly_passing: list[str] = []

            for key in all_gate_keys:
                bg = b_gates.get(key)
                tg = t_gates.get(key)
                bg_pass = bg.passed if bg else None
                tg_pass = tg.passed if tg else None
                is_mandatory = (bg.mandatory if bg else False) or (tg.mandatory if tg else False)

                if bg_pass is True and tg_pass is False:
                    previously_passing_failed.append(key)
                elif bg_pass in (False, None) and tg_pass is True:
                    newly_passing.append(key)

                if is_mandatory and bg_pass != tg_pass:
                    mandatory_changes.append(f"{key} (mandatory: {bg_pass} -> {tg_pass})")

            # Evidence completeness issues
            evidence_issues = []
            b_unavail = b_role.evidence.get("unavailable_cases", [])
            t_unavail = t_role.evidence.get("unavailable_cases", [])
            if b_unavail:
                evidence_issues.append(f"baseline unavailable cases: {b_unavail}")
            if t_unavail:
                evidence_issues.append(f"target unavailable cases: {t_unavail}")

            b_unmeas_mand = [
                g.key for g in b_role.gates if g.mandatory and g.status == "not_evaluated"
            ]
            t_unmeas_mand = [
                g.key for g in t_role.gates if g.mandatory and g.status == "not_evaluated"
            ]
            if b_unmeas_mand:
                evidence_issues.append(f"baseline unmeasured mandatory gates: {b_unmeas_mand}")
            if t_unmeas_mand:
                evidence_issues.append(f"target unmeasured mandatory gates: {t_unmeas_mand}")

            # Determine verdict
            # Note: LEVEL_RANK values are 0 (qualified), 1 (conditional), 2 (unqualified).
            # Lower rank integer means BETTER qualification.
            b_level = b_role.qualification
            t_level = t_role.qualification
            b_rank = LEVEL_RANK.get(QualificationLevel(b_level), 3)
            t_rank = LEVEL_RANK.get(QualificationLevel(t_level), 3)

            failed_mand_regressed = any(
                (k in b_gates and b_gates[k].mandatory and b_gates[k].passed is True)
                and (k in t_gates and t_gates[k].passed is False)
                for k in previously_passing_failed
            )

            passed_mand_improved = any(
                (k in t_gates and t_gates[k].mandatory and t_gates[k].passed is True)
                and (k not in b_gates or b_gates[k].passed is not True)
                for k in newly_passing
            )

            if (
                t_rank > b_rank
                or failed_mand_regressed
                or (t_rank == b_rank and delta is not None and delta <= -SCORE_DELTA_THRESHOLD)
            ):
                verdict = VERDICT_REGRESSED
                reasons = []
                if t_rank > b_rank:
                    reasons.append(f"qualification level dropped from {b_level} to {t_level}")
                if failed_mand_regressed:
                    reasons.append(
                        f"previously passing mandatory gate(s) failed: {previously_passing_failed}"
                    )
                if t_rank == b_rank and delta is not None and delta <= -SCORE_DELTA_THRESHOLD:
                    reasons.append(
                        f"score decreased from {b_score:.3f} to {t_score:.3f} ({delta:+.3f})"
                    )
                reason = "; ".join(reasons)
            elif (
                t_rank < b_rank
                or passed_mand_improved
                or (t_rank == b_rank and delta is not None and delta >= SCORE_DELTA_THRESHOLD)
            ):
                verdict = VERDICT_IMPROVED
                reasons = []
                if t_rank < b_rank:
                    reasons.append(f"qualification level improved from {b_level} to {t_level}")
                if passed_mand_improved:
                    reasons.append(f"newly passing mandatory gate(s): {newly_passing}")
                if t_rank == b_rank and delta is not None and delta >= SCORE_DELTA_THRESHOLD:
                    reasons.append(
                        f"score increased from {b_score:.3f} to {t_score:.3f} ({delta:+.3f})"
                    )
                reason = "; ".join(reasons)
            else:
                verdict = VERDICT_UNCHANGED
                reason = f"qualification level ({b_level}) and gates remained unchanged"
                if delta is not None:
                    reason += f" (score delta: {delta:+.3f})"

            comparisons.append(
                RoleQualificationComparison(
                    model=model,
                    provider=provider,
                    role=role,
                    comparable=True,
                    verdict=verdict,
                    reason=reason,
                    baseline_inference_mode=b_prof.inference_mode,
                    target_inference_mode=t_prof.inference_mode,
                    baseline_qualification=b_level,
                    target_qualification=t_level,
                    baseline_score=b_score,
                    target_score=t_score,
                    score_delta=delta,
                    meaningful_score_change=meaningful,
                    mandatory_gate_changes=_scrub_str_tuple(mandatory_changes),
                    previously_passing_failed_gates=_scrub_str_tuple(previously_passing_failed),
                    newly_passing_gates=_scrub_str_tuple(newly_passing),
                    missing_or_incomplete_evidence=_scrub_str_tuple(evidence_issues),
                )
            )

    # Sort comparisons deterministically by model, provider, role
    comparisons.sort(key=lambda c: (c.model, c.provider, c.role))

    # Calculate summary counts
    summary: dict[str, int] = {v: 0 for v in COMPARISON_VERDICTS}
    for comp in comparisons:
        summary[comp.verdict] = summary.get(comp.verdict, 0) + 1

    return QualificationComparisonReport(
        compared_at=compared_time,
        baseline_source=str(baseline_source_name),
        target_source=str(target_source_name),
        baseline_inference_modes=baseline_modes,
        target_inference_modes=target_modes,
        comparisons=tuple(comparisons),
        summary=summary,
        warnings=_scrub_str_tuple(warnings),
    )


def write_comparison_json(path: str | Path, report: QualificationComparisonReport) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target


def read_comparison_json(path: str | Path) -> QualificationComparisonReport:
    return QualificationComparisonReport.model_validate_json(Path(path).read_text(encoding="utf-8"))


def render_comparison_markdown(report: QualificationComparisonReport) -> str:
    """Render human-readable Markdown comparison report."""
    lines = [
        "# Historical AI Model Qualification Comparison",
        "",
        f"- compared_at: `{report.compared_at.isoformat()}`",
        f"- baseline_source: `{report.baseline_source}` (modes: `{list(report.baseline_inference_modes)}`)",
        f"- target_source: `{report.target_source}` (modes: `{list(report.target_inference_modes)}`)",
        "",
        "## Summary",
        "",
        "| verdict | count |",
        "| --- | --- |",
    ]
    for verdict in COMPARISON_VERDICTS:
        count = report.summary.get(verdict, 0)
        lines.append(f"| `{verdict}` | {count} |")

    lines += [
        "",
        "## Role Comparisons",
        "",
        "| model | provider | role | verdict | baseline level | target level | baseline score | target score | score delta | reason |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]

    for c in report.comparisons:
        b_level = c.baseline_qualification or "n/a"
        t_level = c.target_qualification or "n/a"
        b_score = f"{c.baseline_score:.3f}" if c.baseline_score is not None else "n/a"
        t_score = f"{c.target_score:.3f}" if c.target_score is not None else "n/a"
        delta = f"{c.score_delta:+.3f}" if c.score_delta is not None else "n/a"
        lines.append(
            f"| `{c.model}` | `{c.provider}` | `{c.role}` | **{c.verdict}** | "
            f"{b_level} | {t_level} | {b_score} | {t_score} | {delta} | {c.reason} |"
        )

    lines.append("")

    # Detailed gate and evidence diffs
    has_details = any(
        c.mandatory_gate_changes
        or c.previously_passing_failed_gates
        or c.newly_passing_gates
        or c.missing_or_incomplete_evidence
        for c in report.comparisons
    )

    if has_details:
        lines += ["## Detailed Gate & Evidence Changes", ""]
        for c in report.comparisons:
            if not (
                c.mandatory_gate_changes
                or c.previously_passing_failed_gates
                or c.newly_passing_gates
                or c.missing_or_incomplete_evidence
            ):
                continue
            lines.append(f"### `{c.model}` (`{c.provider}`) — role `{c.role}` ({c.verdict})")
            lines.append("")
            if c.previously_passing_failed_gates:
                lines.append(
                    f"- **Previously passing gates that now fail**: {list(c.previously_passing_failed_gates)}"
                )
            if c.newly_passing_gates:
                lines.append(f"- **Newly passing gates**: {list(c.newly_passing_gates)}")
            if c.mandatory_gate_changes:
                lines.append(f"- **Mandatory gate changes**: {list(c.mandatory_gate_changes)}")
            if c.missing_or_incomplete_evidence:
                lines.append(
                    f"- **Missing or incomplete evidence**: {list(c.missing_or_incomplete_evidence)}"
                )
            lines.append("")

    return "\n".join(lines)


def write_comparison_markdown(path: str | Path, report: QualificationComparisonReport) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_comparison_markdown(report), encoding="utf-8")
    return target


__all__ = [
    "COMPARISON_SCHEMA_VERSION",
    "COMPARISON_VERDICTS",
    "MAX_ARTIFACT_SIZE_BYTES",
    "SCORE_DELTA_THRESHOLD",
    "VERDICT_IMPROVED",
    "VERDICT_INSUFFICIENT_EVIDENCE",
    "VERDICT_NON_COMPARABLE",
    "VERDICT_REGRESSED",
    "VERDICT_UNCHANGED",
    "QualificationComparisonReport",
    "RoleQualificationComparison",
    "compare_qualification_artifacts",
    "load_qualification_artifact",
    "read_comparison_json",
    "render_comparison_markdown",
    "write_comparison_json",
    "write_comparison_markdown",
]
