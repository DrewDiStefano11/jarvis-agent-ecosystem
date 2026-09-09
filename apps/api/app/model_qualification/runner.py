"""Bounded model-qualification runner.

Flow: ``installed local models`` → bounded evaluation suite → role metrics →
qualification policy → verdicts → profile → comparison → recommendation.

Everything is bounded (no unlimited loops, no sleep-based retries, no endless
repair attempts):

- models per invocation (:class:`QualificationBounds.max_models`)
- roles per invocation (``max_roles``)
- cases per role (``max_cases_per_role``)
- repair attempts per case (``max_repairs_per_case``, delegated to PR #64's
  runner, which performs at most one repair per failed validation)
- provider calls per model (``max_calls_per_model``)
- provider calls per invocation (``max_total_calls``)
- output size (``max_output_chars``) and prompt size (``max_context_chars``)
- per-call timeout (``per_call_timeout_seconds``) and overall deadline
  (``deadline_seconds``, checked between models)

Nothing here changes production routing: the runner only observes and reports.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import Settings
from app.model_evaluation.cases import EvaluationCase, all_cases
from app.model_evaluation.providers import (
    EvalProvider,
    EvaluationUnavailableError,
    build_local_provider,
)
from app.model_evaluation.runner import EvaluationBounds, EvaluationReport, run_evaluation
from app.model_qualification.discovery import verify_installed_model
from app.model_qualification.fixtures import FixturePersona, all_personas
from app.model_qualification.metrics import evaluation_suite_digest, role_metrics
from app.model_qualification.profile import (
    FIXTURE_MODE,
    INSTALLED_LOCAL_MODE,
    ModelProfile,
    QualificationRun,
    build_profile,
    unavailable_profile,
)
from app.model_qualification.roles import (
    QualificationRole,
    all_roles,
    cases_for_role,
    parse_roles,
)
from app.model_qualification.scoring import RoleAssessment, assess_role


class QualificationBounds(BaseModel):
    """Hard limits for one qualification invocation (documented, validated)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_models: int = Field(
        default=8, ge=1, le=64, description="Maximum models qualified per invocation."
    )
    max_roles: int = Field(default=8, ge=1, le=16, description="Maximum roles evaluated per model.")
    max_cases_per_role: int = Field(
        default=8, ge=1, le=64, description="Maximum evaluation cases executed per role."
    )
    max_repairs_per_case: int = Field(
        default=1,
        ge=0,
        le=1,
        description=(
            "Maximum repair attempts per case. PR #64's runner performs at most one "
            "repair per failed validation; repairs never apply to call-level failures."
        ),
    )
    max_calls_per_model: int = Field(
        default=64, ge=1, le=1024, description="Maximum provider calls per model."
    )
    max_total_calls: int = Field(
        default=256, ge=1, le=4096, description="Maximum provider calls per invocation."
    )
    max_output_chars: int = Field(
        default=20000, ge=100, le=200000, description="Maximum accepted response characters."
    )
    max_context_chars: int = Field(
        default=20000,
        ge=1000,
        le=200000,
        description="Maximum prompt characters per case; larger cases are skipped.",
    )
    per_call_timeout_seconds: float = Field(
        default=120.0, ge=1.0, le=600.0, description="Timeout for one provider call."
    )
    deadline_seconds: float = Field(
        default=900.0,
        ge=1.0,
        le=3600.0,
        description="Wall-clock deadline for one invocation, checked between models.",
    )

    def evaluation_bounds(self) -> EvaluationBounds:
        return EvaluationBounds(
            max_calls=self.max_calls_per_model,
            per_call_timeout_seconds=self.per_call_timeout_seconds,
            max_output_chars=self.max_output_chars,
        )

    def document(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


DEFAULT_BOUNDS = QualificationBounds()


def _now() -> datetime:
    return datetime.now(UTC)


def _bound_cases(
    roles: tuple[QualificationRole, ...],
    cases: tuple[EvaluationCase, ...],
    bounds: QualificationBounds,
) -> tuple[tuple[EvaluationCase, ...], list[str]]:
    """Union of per-role cases, each role truncated to ``max_cases_per_role``."""
    warnings: list[str] = []
    kept_ids: list[str] = []
    for role in roles:
        role_cases = cases_for_role(role, cases)
        if len(role_cases) > bounds.max_cases_per_role:
            warnings.append(
                f"role {role.value}: {len(role_cases)} cases truncated to "
                f"{bounds.max_cases_per_role} by max_cases_per_role"
            )
            role_cases = role_cases[: bounds.max_cases_per_role]
        kept_ids.extend(case.case_id for case in role_cases)
    wanted = set(kept_ids)
    selected = tuple(case for case in cases if case.case_id in wanted)
    return selected, warnings


def _skip_oversized_cases(
    cases: tuple[EvaluationCase, ...], bounds: QualificationBounds
) -> tuple[tuple[EvaluationCase, ...], list[str]]:
    warnings: list[str] = []
    kept: list[EvaluationCase] = []
    for case in cases:
        size = len(case.system_prompt) + len(case.user_prompt)
        if size > bounds.max_context_chars:
            warnings.append(
                f"case {case.case_id} skipped: prompt {size} chars exceeds "
                f"max_context_chars {bounds.max_context_chars}"
            )
            continue
        kept.append(case)
    return tuple(kept), warnings


async def run_qualification(
    provider: EvalProvider,
    *,
    roles: tuple[QualificationRole, ...] | list[str] | None = None,
    cases: tuple[EvaluationCase, ...] | None = None,
    bounds: QualificationBounds | None = None,
    repetitions: int = 1,
    allow_repair: bool = False,
    repo_sha: str = "unknown",
    evaluated_at: datetime | None = None,
    extra_warnings: tuple[str, ...] = (),
) -> ModelProfile:
    """Qualify one provider/model for the requested roles under hard bounds."""
    active_bounds = bounds or DEFAULT_BOUNDS
    catalog = cases if cases is not None else all_cases()
    requested = parse_roles(list(roles) if roles is not None else None)
    warnings: list[str] = list(extra_warnings)

    if len(requested) > active_bounds.max_roles:
        warnings.append(
            f"{len(requested)} roles truncated to {active_bounds.max_roles} by max_roles"
        )
        requested = requested[: active_bounds.max_roles]

    selected, selection_warnings = _bound_cases(requested, catalog, active_bounds)
    warnings.extend(selection_warnings)
    selected, size_warnings = _skip_oversized_cases(selected, active_bounds)
    warnings.extend(size_warnings)

    if not selected:
        return build_profile(
            provider=provider.name,
            model=provider.model_name,
            inference_mode=provider.inference_mode,
            assessments={},
            evaluated_at=evaluated_at or _now(),
            evaluation_suite_digest=evaluation_suite_digest(catalog),
            repo_sha=repo_sha,
            run={"bounds": active_bounds.document(), "cases": [], "repetitions": repetitions},
            warnings=tuple(warnings + ["no evaluation case selected"]),
        )

    report: EvaluationReport = await run_evaluation(
        provider,
        selected,
        repetitions=max(1, repetitions),
        allow_repair=allow_repair and active_bounds.max_repairs_per_case > 0,
        bounds=active_bounds.evaluation_bounds(),
    )

    if report.stopped_early:
        warnings.append(f"run stopped early: {report.stopped_early}")

    assessments: dict[QualificationRole, RoleAssessment] = {}
    for role in requested:
        assessments[role] = assess_role(role, role_metrics(report, role, cases=selected))

    digest = evaluation_suite_digest(catalog)
    return build_profile(
        provider=provider.name,
        model=provider.model_name,
        inference_mode=provider.inference_mode,
        assessments=assessments,
        evaluated_at=evaluated_at or _now(),
        evaluation_suite_digest=digest,
        repo_sha=repo_sha,
        run={
            "bounds": active_bounds.document(),
            "cases": [case.case_id for case in selected],
            "repetitions": max(1, repetitions),
            "allow_repair": allow_repair and active_bounds.max_repairs_per_case > 0,
            "stopped_early": report.stopped_early,
            "failure_codes": dict(report.failure_codes),
        },
        warnings=tuple(warnings),
    )


async def run_fixture_qualification(
    *,
    personas: tuple[str, ...] | None = None,
    roles: tuple[QualificationRole, ...] | list[str] | None = None,
    bounds: QualificationBounds | None = None,
    repo_sha: str = "unknown",
    evaluated_at: datetime | None = None,
) -> tuple[ModelProfile, ...]:
    """Qualify deterministic fixture personas (CI-safe; never real inference)."""
    active_bounds = bounds or DEFAULT_BOUNDS
    available = all_personas()
    names = tuple(personas) if personas else tuple(available)
    if len(names) > active_bounds.max_models:
        names = names[: active_bounds.max_models]
    profiles: list[ModelProfile] = []
    for name in names:
        item: FixturePersona = available[name]
        profiles.append(
            await run_qualification(
                item.provider(),
                roles=roles,
                bounds=active_bounds,
                repetitions=item.repetitions,
                allow_repair=item.allow_repair,
                repo_sha=repo_sha,
                evaluated_at=evaluated_at,
                extra_warnings=(f"fixture persona: {item.summary}",),
            )
        )
    return tuple(profiles)


async def run_installed_local_qualification(
    settings: Settings,
    *,
    model: str,
    provider_name: str | None = None,
    roles: tuple[QualificationRole, ...] | list[str] | None = None,
    bounds: QualificationBounds | None = None,
    repetitions: int = 1,
    allow_repair: bool = False,
    repo_sha: str = "unknown",
    evaluated_at: datetime | None = None,
) -> ModelProfile:
    """Qualify one exact installed-local model, or report it unavailable.

    Rules: local provider only, loopback only, exact requested model, no remote
    fallback, no download, no service start. Unavailability is reported as a
    profile with ``status="unavailable"`` and ``not_evaluated`` roles — never as
    a failed quality score.
    """
    active_bounds = bounds or DEFAULT_BOUNDS
    requested = parse_roles(list(roles) if roles is not None else None)
    stamp = evaluated_at or _now()
    digest = evaluation_suite_digest()

    verification = await verify_installed_model(settings, model=model, provider_name=provider_name)
    if not verification.available:
        return unavailable_profile(
            provider=provider_name or "unresolved",
            model=model,
            roles=requested,
            evaluated_at=stamp,
            evaluation_suite_digest=digest,
            repo_sha=repo_sha,
            reason=f"{verification.status}: {verification.detail}",
        )
    assert verification.provider is not None
    try:
        provider = await build_local_provider(
            settings,
            provider_name=verification.provider,
            model=model,
            maximum_requests=active_bounds.max_calls_per_model,
        )
    except EvaluationUnavailableError as exc:
        return unavailable_profile(
            provider=verification.provider,
            model=model,
            roles=requested,
            evaluated_at=stamp,
            evaluation_suite_digest=digest,
            repo_sha=repo_sha,
            reason=f"{exc.code}: {exc}",
        )
    return await run_qualification(
        provider,
        roles=requested,
        bounds=active_bounds,
        repetitions=repetitions,
        allow_repair=allow_repair,
        repo_sha=repo_sha,
        evaluated_at=stamp,
    )


async def qualify_installed_models(
    settings: Settings,
    *,
    models: tuple[str, ...],
    provider_name: str | None = None,
    roles: tuple[QualificationRole, ...] | list[str] | None = None,
    bounds: QualificationBounds | None = None,
    repetitions: int = 1,
    allow_repair: bool = False,
    repo_sha: str = "unknown",
    evaluated_at: datetime | None = None,
) -> tuple[tuple[ModelProfile, ...], tuple[str, ...]]:
    """Qualify several installed models under one shared invocation budget."""
    active_bounds = bounds or DEFAULT_BOUNDS
    requested = parse_roles(list(roles) if roles is not None else None)
    stamp = evaluated_at or _now()
    started = time.monotonic()
    profiles: list[ModelProfile] = []
    warnings: list[str] = []
    limited = tuple(models)[: active_bounds.max_models]
    if len(models) > active_bounds.max_models:
        warnings.append(
            f"{len(models)} models truncated to {active_bounds.max_models} by max_models"
        )
    for model in limited:
        elapsed = time.monotonic() - started
        if elapsed > active_bounds.deadline_seconds:
            profiles.append(
                unavailable_profile(
                    provider=provider_name or "unresolved",
                    model=model,
                    roles=requested,
                    evaluated_at=stamp,
                    evaluation_suite_digest=evaluation_suite_digest(),
                    repo_sha=repo_sha,
                    reason=(
                        f"deadline_seconds exceeded after {elapsed:.1f}s "
                        f"(limit {active_bounds.deadline_seconds:.1f}s)"
                    ),
                )
            )
            warnings.append(f"deadline exceeded before model {model!r}")
            continue
        profiles.append(
            await run_installed_local_qualification(
                settings,
                model=model,
                provider_name=provider_name,
                roles=requested,
                bounds=active_bounds,
                repetitions=repetitions,
                allow_repair=allow_repair,
                repo_sha=repo_sha,
                evaluated_at=stamp,
            )
        )
    return tuple(profiles), tuple(warnings)


def build_run(
    profiles: tuple[ModelProfile, ...],
    *,
    roles: tuple[QualificationRole, ...] | None = None,
    repo_sha: str = "unknown",
    generated_at: datetime | None = None,
    extra_warnings: tuple[str, ...] = (),
) -> QualificationRun:
    """Bundle profiles with deterministic comparisons and recommendations."""
    from app.model_qualification.ranking import run_from_profiles

    return run_from_profiles(
        profiles,
        generated_at=generated_at or _now(),
        evaluation_suite_digest=evaluation_suite_digest(),
        repo_sha=repo_sha,
        roles=roles or all_roles(),
        extra_warnings=extra_warnings,
    )


__all__ = [
    "DEFAULT_BOUNDS",
    "FIXTURE_MODE",
    "INSTALLED_LOCAL_MODE",
    "QualificationBounds",
    "build_run",
    "qualify_installed_models",
    "run_fixture_qualification",
    "run_installed_local_qualification",
    "run_qualification",
]
