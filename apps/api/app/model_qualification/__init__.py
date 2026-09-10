"""Bounded local-model qualification and role profiles (extends PR #64).

Pipeline::

    installed local models
      -> bounded evaluation suite (app.model_evaluation, PR #64)
      -> role-specific metrics      (app.model_qualification.metrics)
      -> qualification policy gates (app.model_qualification.policy)
      -> qualified / conditional / unqualified / not_evaluated
      -> deterministic role ranking (app.model_qualification.ranking)
      -> machine-readable profile + recommendation

The important boundary: **this package recommends, it does not assign.** It
never edits production provider defaults, agent models, manager authority,
team-selection rules, decomposition routing, coordinator routing, permissions,
or emergency stop. A later milestone may explicitly consume an approved
profile; nothing here does so automatically.

Two inference modes exist and are always labelled:

- ``fixture`` — deterministic scripted personas for CI (never real inference);
- ``installed_local`` — real installed models over the existing loopback
  provider architecture (no downloads, no service start, no remote fallback).

An unavailable provider or missing model yields ``not_evaluated`` with
``status="unavailable"``: never a failed quality score.
"""

from app.model_qualification.discovery import (
    DiscoveredModel,
    DiscoveryResult,
    ModelVerification,
    discover_local_models,
    verify_installed_model,
)
from app.model_qualification.fixtures import (
    FixturePersona,
    all_personas,
    persona,
    persona_names,
    personas_document,
)
from app.model_qualification.metrics import (
    CALL_LEVEL_FAILURES,
    EVALUATION_SUITE_VERSION,
    RoleMetrics,
    evaluation_suite_digest,
    role_metrics,
)
from app.model_qualification.policy import (
    QUALIFICATION_POLICY_VERSION,
    GateRule,
    RolePolicy,
    gates_for,
    policy_document,
    policy_for,
)
from app.model_qualification.profile import (
    FIXTURE_MODE,
    FIXTURE_WARNING,
    INSTALLED_LOCAL_MODE,
    PROFILE_SCHEMA_VERSION,
    ModelProfile,
    QualificationRun,
    RankedCandidate,
    RoleComparison,
    RoleRecommendation,
    RoleRecord,
    build_profile,
    read_profile_json,
    read_run_json,
    render_profile_markdown,
    write_profile_json,
    write_run_json,
)
from app.model_qualification.ranking import (
    build_recommendations,
    candidates_for_role,
    compare_roles,
    recommend_role,
    recommendation_map,
    run_from_profiles,
)
from app.model_qualification.roles import (
    ROLE_CONTRACTS,
    QualificationRole,
    RoleContract,
    all_roles,
    cases_for_role,
    cases_for_roles,
    expected_case_ids,
    parse_roles,
    role_contract,
    role_document,
    role_names,
)
from app.model_qualification.runner import (
    DEFAULT_BOUNDS,
    QualificationBounds,
    build_run,
    qualify_installed_models,
    run_fixture_qualification,
    run_installed_local_qualification,
    run_qualification,
)
from app.model_qualification.scoring import (
    LEVEL_RANK,
    GateResult,
    QualificationLevel,
    RoleAssessment,
    assess_role,
    evaluate_gates,
    role_score,
)

__all__ = [
    "CALL_LEVEL_FAILURES",
    "DEFAULT_BOUNDS",
    "EVALUATION_SUITE_VERSION",
    "FIXTURE_MODE",
    "FIXTURE_WARNING",
    "INSTALLED_LOCAL_MODE",
    "LEVEL_RANK",
    "PROFILE_SCHEMA_VERSION",
    "QUALIFICATION_POLICY_VERSION",
    "ROLE_CONTRACTS",
    "DiscoveredModel",
    "DiscoveryResult",
    "FixturePersona",
    "GateResult",
    "GateRule",
    "ModelProfile",
    "ModelVerification",
    "QualificationBounds",
    "QualificationLevel",
    "QualificationRole",
    "QualificationRun",
    "RankedCandidate",
    "RoleAssessment",
    "RoleComparison",
    "RoleContract",
    "RoleMetrics",
    "RolePolicy",
    "RoleRecommendation",
    "RoleRecord",
    "all_personas",
    "all_roles",
    "assess_role",
    "build_profile",
    "build_recommendations",
    "build_run",
    "candidates_for_role",
    "cases_for_role",
    "cases_for_roles",
    "compare_roles",
    "discover_local_models",
    "evaluate_gates",
    "evaluation_suite_digest",
    "expected_case_ids",
    "gates_for",
    "parse_roles",
    "persona",
    "persona_names",
    "personas_document",
    "policy_document",
    "policy_for",
    "qualify_installed_models",
    "read_profile_json",
    "read_run_json",
    "recommend_role",
    "recommendation_map",
    "render_profile_markdown",
    "role_contract",
    "role_document",
    "role_metrics",
    "role_names",
    "role_score",
    "run_fixture_qualification",
    "run_from_profiles",
    "run_installed_local_qualification",
    "run_qualification",
    "verify_installed_model",
    "write_profile_json",
    "write_run_json",
]
