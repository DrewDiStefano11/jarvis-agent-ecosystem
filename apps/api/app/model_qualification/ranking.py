"""Deterministic model comparison and role recommendation.

Ranking is evidence ordering, not a beauty contest. Ordering keys, applied in
order:

1. **qualification level** — qualified, conditional, unqualified, not_evaluated;
2. **mandatory-gate result** — passed before failed before unmeasured;
3. **role score** — descending (unmeasured scores last);
4. **deterministic tie breaker** — ``model`` then ``provider`` (lexicographic),
   never random and never dependent on input order.

Consequence: a model that failed a mandatory gate can never outrank a qualified
model because of a higher aggregate score.

Recommendations are emitted only for candidates that are ``qualified`` or
``conditional``. A role with no such candidate reports ``model=None`` and a
reason instead of recommending an unqualified model.
"""

from __future__ import annotations

from app.model_qualification.profile import (
    ModelProfile,
    QualificationRun,
    RankedCandidate,
    RoleComparison,
    RoleRecommendation,
)
from app.model_qualification.roles import QualificationRole, all_roles, role_names
from app.model_qualification.scoring import LEVEL_RANK, QualificationLevel

#: Levels that may be recommended for a role (evidence-backed, bounded use).
RECOMMENDABLE_LEVELS = (QualificationLevel.QUALIFIED, QualificationLevel.CONDITIONAL)


def level_rank(level: str | QualificationLevel) -> int:
    return LEVEL_RANK[QualificationLevel(level)]


def _mandatory_rank(mandatory_gates_passed: bool | None) -> int:
    if mandatory_gates_passed is True:
        return 0
    if mandatory_gates_passed is False:
        return 1
    return 2


def _score_key(score: float | None) -> float:
    """Descending score with unmeasured scores last."""
    return -(score if score is not None else -1.0)


def _sort_key(candidate: RankedCandidate) -> tuple[int, int, float, str, str]:
    return (
        level_rank(candidate.qualification),
        _mandatory_rank(candidate.mandatory_gates_passed),
        _score_key(candidate.score),
        candidate.model,
        candidate.provider,
    )


def candidates_for_role(
    role: QualificationRole, profiles: tuple[ModelProfile, ...]
) -> tuple[RankedCandidate, ...]:
    """Every profile carrying a verdict for ``role``, deterministically ordered."""
    collected: list[RankedCandidate] = []
    for profile in profiles:
        record = profile.roles.get(role.value)
        if record is None:
            continue
        collected.append(
            RankedCandidate(
                rank=0,
                model=profile.model,
                provider=profile.provider,
                inference_mode=profile.inference_mode,
                qualification=record.qualification,
                score=record.score,
                mandatory_gates_passed=record.mandatory_gates_passed,
                tie_breaker=f"model={profile.model};provider={profile.provider}",
            )
        )
    ordered = sorted(collected, key=_sort_key)
    return tuple(
        candidate.model_copy(update={"rank": index})
        for index, candidate in enumerate(ordered, start=1)
    )


def compare_roles(
    profiles: tuple[ModelProfile, ...], roles: tuple[QualificationRole, ...] | None = None
) -> tuple[RoleComparison, ...]:
    return tuple(
        RoleComparison(role=role.value, candidates=candidates_for_role(role, profiles))
        for role in (roles or all_roles())
        if role.value in role_names()
    )


def recommend_role(
    role: QualificationRole, candidates: tuple[RankedCandidate, ...]
) -> RoleRecommendation:
    """Recommend the best *usable* candidate, or none at all."""
    if not candidates:
        return RoleRecommendation(
            role=role.value, reason="no candidate was evaluated for this role"
        )
    best = candidates[0]
    if QualificationLevel(best.qualification) not in RECOMMENDABLE_LEVELS:
        return RoleRecommendation(
            role=role.value,
            reason=(
                f"no qualified or conditional candidate; best is "
                f"`{best.model}` ({best.qualification})"
            ),
        )
    detail = (
        "qualified"
        if best.qualification == QualificationLevel.QUALIFIED
        else "conditional (bounded use)"
    )
    runner_up = candidates[1] if len(candidates) > 1 else None
    suffix = (
        f"; next candidate `{runner_up.model}` ({runner_up.qualification})"
        if runner_up is not None
        else "; no other candidate evaluated"
    )
    return RoleRecommendation(
        role=role.value,
        model=best.model,
        provider=best.provider,
        inference_mode=best.inference_mode,
        qualification=best.qualification,
        score=best.score,
        reason=f"rank 1 of {len(candidates)} evaluated candidate(s): {detail}{suffix}",
    )


def build_recommendations(
    comparisons: tuple[RoleComparison, ...],
) -> tuple[RoleRecommendation, ...]:
    return tuple(
        recommend_role(QualificationRole(comparison.role), comparison.candidates)
        for comparison in comparisons
    )


def recommendation_map(recommendations: tuple[RoleRecommendation, ...]) -> dict[str, str | None]:
    """The conceptual ``{role: model}`` map (``None`` where nothing is usable)."""
    return {item.role: item.model for item in recommendations}


def run_from_profiles(
    profiles: tuple[ModelProfile, ...],
    *,
    generated_at,
    evaluation_suite_digest: str,
    repo_sha: str = "unknown",
    roles: tuple[QualificationRole, ...] | None = None,
    extra_warnings: tuple[str, ...] = (),
) -> QualificationRun:
    """Bundle profiles with comparisons and recommendations."""
    comparisons = compare_roles(profiles, roles)
    warnings: list[str] = list(extra_warnings)
    for profile in profiles:
        for warning in profile.warnings:
            if warning not in warnings:
                warnings.append(warning)
    return QualificationRun(
        generated_at=generated_at,
        evaluation_suite_digest=evaluation_suite_digest,
        repo_sha=repo_sha,
        inference_modes=tuple(sorted({profile.inference_mode for profile in profiles})),
        models=profiles,
        comparisons=comparisons,
        recommendations=build_recommendations(comparisons),
        warnings=tuple(warnings),
    )


__all__ = [
    "RECOMMENDABLE_LEVELS",
    "build_recommendations",
    "candidates_for_role",
    "compare_roles",
    "level_rank",
    "recommend_role",
    "recommendation_map",
    "run_from_profiles",
]
