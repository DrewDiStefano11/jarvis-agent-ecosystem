"""Local-model qualification tests: taxonomy, policy, gates, ranking, evidence.

All tests are deterministic and CI-safe. Fixture personas replay scripted
responses (inference_mode = ``fixture``); discovery tests use fake registries
so no Ollama service, download, or network access is ever required.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.model_evaluation.cases import EvaluationCase, all_cases, case_by_id
from app.model_evaluation.providers import EvaluationUnavailableError
from app.model_evaluation.runner import EvaluationBounds, run_evaluation
from app.model_providers.contracts import HealthStatus, ProviderHealth
from app.model_qualification.discovery import (
    DISCOVERED,
    EXECUTION_DISABLED,
    MODEL_INSTALLED,
    MODEL_UNAVAILABLE,
    NO_LOCAL_PROVIDER,
    PROVIDER_UNHEALTHY,
    REJECTED_REMOTE,
    UNKNOWN_PROVIDER,
    ModelVerification,
    discover_local_models,
    verify_installed_model,
)
from app.model_qualification.fixtures import UNPARSEABLE_RESPONSE
from app.model_qualification.metrics import RoleMetrics, evaluation_suite_digest, role_metrics
from app.model_qualification.policy import (
    QUALIFICATION_POLICY_VERSION,
    policy_document,
    policy_for,
)
from app.model_qualification.profile import (
    FIXTURE_MODE,
    FIXTURE_WARNING,
    INSTALLED_LOCAL_MODE,
    PROFILE_SCHEMA_VERSION,
    STATUS_SKIPPED,
    ModelProfile,
    QualificationRun,
    build_profile,
    read_profile_json,
    read_run_json,
    render_profile_markdown,
    write_profile_json,
    write_run_json,
)
from app.model_qualification.ranking import (
    candidates_for_role,
    compare_roles,
    recommendation_map,
    run_from_profiles,
)
from app.model_qualification.roles import (
    QualificationRole,
    all_roles,
    cases_for_role,
    expected_case_ids,
    parse_roles,
    role_contract,
)
from app.model_qualification.runner import (
    DEFAULT_BOUNDS,
    QualificationBounds,
    build_run,
    run_fixture_qualification,
    run_installed_local_qualification,
    run_qualification,
)
from app.model_qualification.scoring import (
    QualificationLevel,
    assess_role,
    evaluate_gates,
    role_score,
)

STAMP = datetime(2026, 1, 1, tzinfo=UTC)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


class StubProvider:
    """Scripted provider that can masquerade as installed-local or fixture."""

    is_local = True

    def __init__(
        self,
        scripts: dict[str, list[Any]],
        *,
        model_name: str = "stub-model",
        provider_name: str = "stub-local",
        inference_mode: str = INSTALLED_LOCAL_MODE,
    ) -> None:
        self._scripts = {key: list(value) for key, value in scripts.items()}
        self._counters: dict[str, int] = {}
        self.model_name = model_name
        self.name = provider_name
        self.inference_mode = inference_mode
        self.calls = 0

    async def generate(self, request) -> Any:
        from app.model_providers.contracts import ModelExecutionResponse, UsageQuality

        case_id = (request.task_id or "unknown").split(":")[0]
        index = self._counters.get(case_id, 0)
        script = self._scripts.get(case_id, [])
        self._counters[case_id] = index + 1
        self.calls += 1
        if index >= len(script):
            raise EvaluationUnavailableError("script_exhausted", f"no response for {case_id}")
        entry = script[index]
        if isinstance(entry, Exception):
            raise entry
        return ModelExecutionResponse(
            content=entry,
            provider=self.name,
            model=self.model_name,
            usage_quality=UsageQuality.UNKNOWN,
            latency_ms=1.0,
            task_id=request.task_id,
            correlation_id=request.correlation_id,
        )


def script_for(
    outputs: dict[str, str], cases: tuple[EvaluationCase, ...] | None = None
) -> dict[str, list[str]]:
    """Map case id -> one response, defaulting to the case reference output."""
    catalog = cases if cases is not None else all_cases()
    return {case.case_id: [outputs.get(case.case_id, case.reference_output)] for case in catalog}


async def profile_for(
    outputs: dict[str, str],
    *,
    model: str = "stub-model",
    inference_mode: str = INSTALLED_LOCAL_MODE,
    roles: list[str] | None = None,
    bounds: QualificationBounds | None = None,
    repetitions: int = 1,
    allow_repair: bool = False,
) -> ModelProfile:
    provider = StubProvider(script_for(outputs), model_name=model, inference_mode=inference_mode)
    return await run_qualification(
        provider,
        roles=roles,
        bounds=bounds,
        repetitions=repetitions,
        allow_repair=allow_repair,
        repo_sha="test-sha",
        evaluated_at=STAMP,
    )


async def persona_profile(name: str, **kwargs) -> ModelProfile:
    profiles = await run_fixture_qualification(
        personas=(name,), repo_sha="test-sha", evaluated_at=STAMP, **kwargs
    )
    return profiles[0]


def persona_profile_sync(name: str, **kwargs) -> ModelProfile:
    """Sync helper for tests that do not need an event loop."""
    import asyncio

    return asyncio.run(persona_profile(name, **kwargs))


def level_of(profile: ModelProfile, role: str) -> str:
    return profile.roles[role].qualification


# --------------------------------------------------------------------------
# 1-4: qualification levels
# --------------------------------------------------------------------------


async def test_qualified_fixture_model_qualifies_for_every_role() -> None:
    profile = await persona_profile("fixture-model/qualified")
    assert profile.status == "evaluated"
    assert profile.inference_mode == FIXTURE_MODE
    for role in all_roles():
        record = profile.roles[role.value]
        assert record.qualification == QualificationLevel.QUALIFIED.value
        assert record.mandatory_gates_passed is True
        assert record.score is not None and record.score >= policy_for(role).qualified_score


async def test_conditional_model_is_bounded_not_qualified() -> None:
    profile = await persona_profile("fixture-model/conditional")
    for role in all_roles():
        record = profile.roles[role.value]
        assert record.qualification == QualificationLevel.CONDITIONAL.value
        # mandatory gates still pass; an advisory gate (consistency) failed
        assert record.mandatory_gates_passed is True
        assert any(gate.status == "failed" and not gate.mandatory for gate in record.gates)
        assert record.weaknesses


async def test_unqualified_model_fails_mandatory_gates() -> None:
    profile = await persona_profile("fixture-model/unqualified")
    for role in all_roles():
        record = profile.roles[role.value]
        assert record.qualification == QualificationLevel.UNQUALIFIED.value
        assert any(gate.mandatory and gate.status == "failed" for gate in record.gates)


async def test_not_evaluated_model_has_no_quality_score() -> None:
    profile = await persona_profile("fixture-model/unavailable")
    assert profile.status == "unavailable"
    for role in all_roles():
        record = profile.roles[role.value]
        assert record.qualification == QualificationLevel.NOT_EVALUATED.value
        assert record.score is None
        assert record.mandatory_gates_passed is None
        assert "unavailable" in " ".join(record.reasons)


# --------------------------------------------------------------------------
# 5-10: gates and thresholds
# --------------------------------------------------------------------------


async def test_mandatory_gate_failure_blocks_qualification() -> None:
    """A weak decomposer stays unqualified for decomposer only."""
    profile = await persona_profile("fixture-model/weak-decomposer")
    assert level_of(profile, "decomposer") == QualificationLevel.UNQUALIFIED.value
    assert level_of(profile, "manager") == QualificationLevel.QUALIFIED.value
    gate = next(
        gate
        for gate in profile.roles["decomposer"].gates
        if gate.key == "decomposition_quality_min"
    )
    assert gate.mandatory is True
    assert gate.status == "failed"


async def test_high_score_cannot_hide_a_failed_mandatory_gate() -> None:
    """Excellent scores elsewhere still lose to one failed mandatory gate."""
    metrics = {
        "case_pass_rate": 0.95,
        "schema_validity_rate": 1.0,
        "structured_output_success_rate": 1.0,
        "malformed_response_rate": 0.25,  # mandatory max is 0.20
        "instruction_following_rate": 1.0,
        "synthesis_completeness": 1.0,
        "hallucination_rate": 0.0,
    }
    gates = evaluate_gates(QualificationRole.MANAGER, metrics)
    failed = [gate for gate in gates if gate.status == "failed"]
    assert [gate.key for gate in failed] == ["malformed_response_max"]
    assert role_score(QualificationRole.MANAGER, metrics) > 0.9

    complete = RoleMetrics(
        role=QualificationRole.MANAGER,
        expected_case_ids=("manager-basic", "synthesis-basic", "structured-strict"),
        evaluated_case_ids=("manager-basic", "synthesis-basic", "structured-strict"),
        unavailable_case_ids=(),
        missing_case_ids=(),
        metrics=metrics,
        operational={},
        failure_codes={},
        context_sensitivity={},
        stopped_early=False,
    )
    assessment = assess_role(QualificationRole.MANAGER, complete)
    assert assessment.qualification is QualificationLevel.UNQUALIFIED
    assert assessment.score is not None and assessment.score > 0.9


async def test_low_malformed_response_rate_passes() -> None:
    profile = await persona_profile("fixture-model/qualified")
    record = profile.roles["specialist"]
    gate = next(g for g in record.gates if g.key == "malformed_response_max")
    assert gate.observed == 0.0 and gate.status == "passed"


async def test_excessive_malformed_response_rate_fails() -> None:
    profile = await persona_profile("fixture-model/malformed-json")
    for role in all_roles():
        record = profile.roles[role.value]
        gate = next(g for g in record.gates if g.key == "malformed_response_max")
        assert gate.observed == 1.0
        assert gate.status == "failed"
        assert record.qualification == QualificationLevel.UNQUALIFIED.value


async def test_schema_validity_threshold_is_explicit() -> None:
    policy = policy_for(QualificationRole.REVIEWER)
    gate = next(g for g in policy.gates if g.key == "schema_validity_min")
    assert gate.threshold == 0.95 and gate.direction == "min"
    # just below the threshold fails, at the threshold passes
    assert gate.satisfied_by(0.949) is False
    assert gate.satisfied_by(0.95) is True
    assert gate.satisfied_by(None) is None


async def test_consistency_threshold_drives_conditional() -> None:
    profile = await persona_profile("fixture-model/conditional")
    record = profile.roles["planner"]
    gate = next(g for g in record.gates if g.key == "consistency_rate_min")
    assert gate.observed == 0.0 and gate.status == "failed" and gate.mandatory is False
    assert record.qualification == QualificationLevel.CONDITIONAL.value


# --------------------------------------------------------------------------
# 11-15: per-role qualification
# --------------------------------------------------------------------------


async def test_planner_qualification() -> None:
    qualified = await persona_profile("fixture-model/qualified")
    assert level_of(qualified, "planner") == QualificationLevel.QUALIFIED.value

    broken = await profile_for(
        {"planning-basic": case_by_id("planning-basic").adversarial_outputs["missing_milestone"]},
        roles=["planner"],
        inference_mode=FIXTURE_MODE,
    )
    assert level_of(broken, "planner") == QualificationLevel.UNQUALIFIED.value
    gate = next(g for g in broken.roles["planner"].gates if g.key == "instruction_following_min")
    assert gate.status == "failed"


async def test_decomposer_dag_quality_gate() -> None:
    profile = await persona_profile("fixture-model/weak-decomposer")
    record = profile.roles["decomposer"]
    assert record.qualification == QualificationLevel.UNQUALIFIED.value
    assert record.evidence["metrics"]["decomposition_quality"] is not None
    assert record.evidence["metrics"]["decomposition_quality"] < 0.75
    assert any("decomposition_quality_min" in item for item in record.weaknesses)


async def test_reviewer_qualification() -> None:
    qualified = await persona_profile("fixture-model/qualified")
    record = qualified.roles["reviewer"]
    assert record.qualification == QualificationLevel.QUALIFIED.value
    gate = next(g for g in record.gates if g.key == "defect_detection_quality_min")
    assert gate.observed == 1.0 and gate.mandatory is True

    weak = await persona_profile("fixture-model/weak-reviewer")
    assert level_of(weak, "reviewer") == QualificationLevel.UNQUALIFIED.value
    assert level_of(weak, "manager") == QualificationLevel.QUALIFIED.value
    review_gate = next(
        g for g in weak.roles["reviewer"].gates if g.key == "defect_detection_quality_min"
    )
    assert review_gate.status == "failed"


async def test_synthesis_qualification() -> None:
    qualified = await persona_profile("fixture-model/qualified")
    assert level_of(qualified, "synthesizer") == QualificationLevel.QUALIFIED.value

    dropped = await profile_for(
        {
            "synthesis-basic": case_by_id("synthesis-basic").adversarial_outputs["dropped_input"],
        },
        roles=["synthesizer"],
        inference_mode=FIXTURE_MODE,
    )
    record = dropped.roles["synthesizer"]
    assert record.qualification == QualificationLevel.UNQUALIFIED.value
    assert record.evidence["metrics"]["synthesis_completeness"] < 0.8


async def test_repair_retry_qualification() -> None:
    qualified = await persona_profile("fixture-model/qualified")
    assert level_of(qualified, "repair_retry") == QualificationLevel.QUALIFIED.value

    broken = await profile_for(
        {
            "correction-basic": case_by_id("correction-basic").adversarial_outputs["still_broken"],
        },
        roles=["repair_retry"],
        inference_mode=FIXTURE_MODE,
    )
    assert level_of(broken, "repair_retry") == QualificationLevel.UNQUALIFIED.value

    repair_dependent = await persona_profile("fixture-model/repair-dependent")
    record = repair_dependent.roles["repair_retry"]
    assert record.qualification == QualificationLevel.CONDITIONAL.value
    operational = record.evidence["operational"]
    assert operational["repair_count"] == 1
    assert record.evidence["metrics"]["repair_success_rate"] == 1.0
    assert record.evidence["metrics"]["repair_frequency"] > 0.34


# --------------------------------------------------------------------------
# 16-20: determinism and ranking
# --------------------------------------------------------------------------


def _without_latency(profile: ModelProfile) -> ModelProfile:
    """Strip measured wall-clock latency (the only non-deterministic signal)."""
    payload = profile.model_dump(mode="json")
    for record in payload["roles"].values():
        for key in ("latency_ms_mean", "latency_ms_p95", "latency_ms_max"):
            record["evidence"]["metrics"].pop(key, None)
            record["evidence"]["operational"].pop(key, None)
    return ModelProfile.model_validate(payload)


async def test_same_model_evaluated_twice_is_deterministic() -> None:
    first = await persona_profile("fixture-model/qualified")
    second = await persona_profile("fixture-model/qualified")
    # verdicts, gates, scores and evidence are identical run to run
    assert _without_latency(first) == _without_latency(second)
    assert {role: record.qualification for role, record in first.roles.items()} == {
        role: record.qualification for role, record in second.roles.items()
    }
    assert {role: record.score for role, record in first.roles.items()} == {
        role: record.score for role, record in second.roles.items()
    }


def _synthetic_profile(
    model: str, level: str, score: float | None, mandatory: bool | None = True
) -> ModelProfile:
    from app.model_qualification.scoring import RoleAssessment

    assessments = {}
    for role in all_roles():
        assessments[role] = RoleAssessment(
            role=role,
            qualification=QualificationLevel(level),
            score=score,
            mandatory_gates_passed=mandatory,
            gates=(),
            strengths=(),
            weaknesses=(),
            reasons=(level,),
            evaluated_case_ids=("case",),
            expected_case_ids=("case",),
            unavailable_case_ids=(),
            metrics={"case_pass_rate": score},
            operational={},
            failure_codes={},
        )
    return build_profile(
        provider="stub",
        model=model,
        inference_mode=FIXTURE_MODE,
        assessments=assessments,
        evaluated_at=STAMP,
        evaluation_suite_digest=evaluation_suite_digest(),
        repo_sha="test-sha",
    )


def test_two_models_tie_breaks_deterministically() -> None:
    profiles = (
        _synthetic_profile("model-b", "qualified", 0.9),
        _synthetic_profile("model-a", "qualified", 0.9),
    )
    candidates = candidates_for_role(QualificationRole.PLANNER, profiles)
    assert [item.model for item in candidates] == ["model-a", "model-b"]
    assert [item.rank for item in candidates] == [1, 2]
    reversed_profiles = tuple(reversed(profiles))
    assert [
        item.model for item in candidates_for_role(QualificationRole.PLANNER, reversed_profiles)
    ] == ["model-a", "model-b"]


def test_qualified_outranks_conditional_outranks_unqualified() -> None:
    profiles = (
        _synthetic_profile("weak", "unqualified", 0.99, mandatory=False),
        _synthetic_profile("ok", "conditional", 0.50),
        _synthetic_profile("good", "qualified", 0.76),
    )
    candidates = candidates_for_role(QualificationRole.MANAGER, profiles)
    assert [item.model for item in candidates] == ["good", "ok", "weak"]


def test_failed_mandatory_gate_never_outranks_a_qualified_model() -> None:
    profiles = (
        _synthetic_profile("failed-gate", "unqualified", 0.99, mandatory=False),
        _synthetic_profile("qualified", "qualified", 0.80, mandatory=True),
    )
    candidates = candidates_for_role(QualificationRole.SPECIALIST, profiles)
    assert candidates[0].model == "qualified"
    assert candidates[1].score == 0.99


# --------------------------------------------------------------------------
# 21-25: discovery, unavailability, remote rejection
# --------------------------------------------------------------------------


class FakeProvider:
    def __init__(
        self,
        name: str = "fake-ollama",
        *,
        is_local: bool = True,
        models: tuple[str, ...] = ("installed-model",),
        healthy: bool = True,
        default_model: str = "installed-model",
        list_error: Exception | None = None,
        available: bool | None = None,
    ) -> None:
        self.name = name
        self.is_local = is_local
        self.default_model = default_model
        self._models = models
        self._healthy = healthy
        self._list_error = list_error
        self._available = available

    async def list_models(self) -> tuple[str, ...] | None:
        if self._list_error is not None:
            raise self._list_error
        return self._models

    async def model_available(self, model: str) -> bool | None:
        if self._available is not None:
            return self._available
        return model in self._models

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self.name,
            healthy=self._healthy,
            status=HealthStatus.HEALTHY if self._healthy else HealthStatus.UNAVAILABLE,
            latency_ms=1.0,
            detail="healthy" if self._healthy else "offline",
        )


class FakeRegistry:
    def __init__(self, providers: list[FakeProvider]) -> None:
        self._providers = providers

    def list(self) -> list[FakeProvider]:
        return list(self._providers)

    async def health(self, providers) -> dict[str, ProviderHealth]:
        return {provider.name: await provider.health_check() for provider in providers}


@pytest.fixture
def local_only_settings(monkeypatch) -> Settings:
    monkeypatch.setenv("JARVIS_MODEL_EXECUTION_MODE", "local_only")
    monkeypatch.setenv("JARVIS_MODEL_OLLAMA_ENABLED", "true")
    monkeypatch.setenv("JARVIS_MODEL_OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    return Settings()


async def test_discovery_reports_disabled_execution(monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_MODEL_EXECUTION_MODE", "disabled")
    result = await discover_local_models(Settings())
    assert result.status == EXECUTION_DISABLED
    assert result.models == ()
    assert "local_only" in result.detail


async def test_discovery_lists_installed_models(monkeypatch, local_only_settings: Settings) -> None:
    monkeypatch.setattr(
        "app.model_qualification.discovery.build_provider_registry",
        lambda settings: FakeRegistry([FakeProvider(models=("alpha", "beta"))]),
    )
    result = await discover_local_models(local_only_settings)
    assert result.status == DISCOVERED
    assert [(item.provider, item.model) for item in result.models] == [
        ("fake-ollama", "alpha"),
        ("fake-ollama", "beta"),
    ]


async def test_discovery_reports_unhealthy_provider(
    monkeypatch, local_only_settings: Settings
) -> None:
    monkeypatch.setattr(
        "app.model_qualification.discovery.build_provider_registry",
        lambda settings: FakeRegistry([FakeProvider(healthy=False)]),
    )
    result = await discover_local_models(local_only_settings)
    assert result.status == PROVIDER_UNHEALTHY
    assert result.available is False
    assert result.providers[0].status == PROVIDER_UNHEALTHY


async def test_discovery_rejects_remote_providers(
    monkeypatch, local_only_settings: Settings
) -> None:
    monkeypatch.setattr(
        "app.model_qualification.discovery.build_provider_registry",
        lambda settings: FakeRegistry(
            [
                FakeProvider(name="remote-cloud", is_local=False, models=("gpt-whatever",)),
                FakeProvider(models=("local-only-model",)),
            ]
        ),
    )
    result = await discover_local_models(local_only_settings)
    assert result.providers[0].status == REJECTED_REMOTE
    assert "remote-cloud" not in [item.provider for item in result.models]


async def test_discovery_handles_malformed_provider_response(
    monkeypatch, local_only_settings: Settings
) -> None:
    """A malformed listing degrades to 'models unknown'; it never invents models."""
    monkeypatch.setattr(
        "app.model_qualification.discovery.build_provider_registry",
        lambda settings: FakeRegistry([FakeProvider(models=None, available=True)]),
    )
    result = await discover_local_models(local_only_settings)
    assert result.providers[0].status == "models_unknown"
    assert result.providers[0].models == ("installed-model",)


async def test_discovery_reports_provider_errors_without_crashing(
    monkeypatch, local_only_settings: Settings
) -> None:
    from app.model_providers.errors import MalformedProviderResponseError

    monkeypatch.setattr(
        "app.model_qualification.discovery.build_provider_registry",
        lambda settings: FakeRegistry(
            [
                FakeProvider(
                    list_error=MalformedProviderResponseError(
                        "malformed model list", provider="fake-ollama", model="x"
                    )
                )
            ]
        ),
    )
    result = await discover_local_models(local_only_settings)
    assert result.providers[0].status == "discovery_error"
    assert result.available is False


async def test_missing_requested_model_is_reported_not_substituted(
    monkeypatch, local_only_settings: Settings
) -> None:
    monkeypatch.setattr(
        "app.model_qualification.discovery.build_provider_registry",
        lambda settings: FakeRegistry([FakeProvider(models=("installed-model",))]),
    )
    verification = await verify_installed_model(local_only_settings, model="not-installed:7b")
    assert verification.available is False
    assert verification.status == MODEL_UNAVAILABLE
    assert "not-installed:7b" in verification.detail

    exact = await verify_installed_model(local_only_settings, model="installed-model")
    assert exact.available is True
    assert exact.status == MODEL_INSTALLED


async def test_unknown_or_remote_provider_name_is_rejected(
    monkeypatch, local_only_settings: Settings
) -> None:
    monkeypatch.setattr(
        "app.model_qualification.discovery.build_provider_registry",
        lambda settings: FakeRegistry(
            [FakeProvider(name="remote-cloud", is_local=False), FakeProvider()]
        ),
    )
    result = await discover_local_models(local_only_settings, provider_name="remote-cloud")
    assert result.status == UNKNOWN_PROVIDER
    assert result.providers[0].status == REJECTED_REMOTE
    assert result.models == ()


async def test_no_local_provider_is_reported(monkeypatch, local_only_settings: Settings) -> None:
    monkeypatch.setattr(
        "app.model_qualification.discovery.build_provider_registry",
        lambda settings: FakeRegistry([]),
    )
    result = await discover_local_models(local_only_settings)
    assert result.status == NO_LOCAL_PROVIDER
    assert result.available is False


async def test_remote_fallback_is_impossible() -> None:
    """Local evaluation preflight rejects remote providers outright."""
    from app.model_evaluation.providers import build_local_provider

    settings = Settings()
    with pytest.raises(EvaluationUnavailableError) as excinfo:
        await build_local_provider(settings, provider_name="remote-cloud")
    assert excinfo.value.code in {"execution_disabled", "unknown_or_remote_provider"}


async def test_installed_local_run_reports_unavailable_not_failed(monkeypatch) -> None:
    """An unavailable provider yields not_evaluated, never a quality verdict."""
    monkeypatch.setenv("JARVIS_MODEL_EXECUTION_MODE", "disabled")
    profile = await run_installed_local_qualification(
        Settings(), model="some-model", repo_sha="test-sha", evaluated_at=STAMP
    )
    assert profile.status == "unavailable"
    assert profile.inference_mode == INSTALLED_LOCAL_MODE
    assert set(profile.role_levels.values()) == {QualificationLevel.NOT_EVALUATED.value}
    assert all(record.score is None for record in profile.roles.values())


# --------------------------------------------------------------------------
# 26-31: provenance, versions, schema, recommendations
# --------------------------------------------------------------------------


async def test_fixture_results_are_clearly_labelled(tmp_path) -> None:
    profile = await persona_profile("fixture-model/qualified")
    assert profile.inference_mode == FIXTURE_MODE
    assert FIXTURE_WARNING in profile.warnings

    run = build_run((profile,), repo_sha="test-sha", generated_at=STAMP)
    markdown = render_profile_markdown((profile,), run)
    assert "inference_mode = fixture" in markdown
    assert FIXTURE_WARNING in markdown

    path = write_run_json(tmp_path / "run.json", run)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["inference_modes"] == ["fixture"]
    assert FIXTURE_WARNING in payload["warnings"]

    # a profile that claims installed_local but carries the fixture label is rejected
    with pytest.raises(ValidationError):
        ModelProfile(
            provider="fixture",
            model="x",
            inference_mode=INSTALLED_LOCAL_MODE,
            status=STATUS_SKIPPED,
            evaluated_at=STAMP,
            evaluation_suite_digest="digest",
            warnings=(FIXTURE_WARNING,),
        )

    # a fixture profile whose label was stripped from stored evidence is rejected
    unlabelled = json.loads(json.dumps(profile.model_dump(mode="json")))
    unlabelled["warnings"] = [item for item in unlabelled["warnings"] if item != FIXTURE_WARNING]
    (tmp_path / "unlabelled.json").write_text(json.dumps(unlabelled), encoding="utf-8")
    with pytest.raises(ValidationError):
        read_profile_json(tmp_path / "unlabelled.json")


async def test_installed_local_results_are_clearly_labelled() -> None:
    profile = await profile_for({}, inference_mode=INSTALLED_LOCAL_MODE)
    assert profile.inference_mode == INSTALLED_LOCAL_MODE
    assert FIXTURE_WARNING not in profile.warnings
    assert profile.status in {"evaluated", "partial"}


async def test_policy_and_suite_versions_are_recorded() -> None:
    profile = await persona_profile("fixture-model/qualified")
    assert profile.qualification_policy_version == QUALIFICATION_POLICY_VERSION
    assert profile.schema_version == PROFILE_SCHEMA_VERSION
    assert profile.evaluation_suite_version
    assert profile.evaluation_suite_digest == evaluation_suite_digest()
    document = policy_document()
    assert document["policy_version"] == QUALIFICATION_POLICY_VERSION
    assert set(document["roles"]) == {role.value for role in all_roles()}


def test_policy_document_is_machine_readable_and_explicit() -> None:
    document = policy_document()
    payload = json.loads(json.dumps(document))
    for _role, entry in payload["roles"].items():
        assert entry["minimum_score"] <= entry["qualified_score"]
        assert entry["weights"]
        gates = entry["mandatory_gates"] + entry["advisory_gates"]
        assert gates
        for gate in gates:
            assert gate["direction"] in {"min", "max"}
            assert isinstance(gate["threshold"], float)
            assert gate["description"]


def test_profile_schema_round_trip_and_rejects_corruption(tmp_path) -> None:
    profile = persona_profile_sync("fixture-model/qualified")
    path = write_profile_json(tmp_path / "profile.json", profile)
    assert read_profile_json(path) == profile

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["roles"]["not-a-role"] = payload["roles"]["planner"]
    (tmp_path / "bad-role.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValidationError):
        read_profile_json(tmp_path / "bad-role.json")

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["inference_mode"] = "cloud"
    (tmp_path / "bad-mode.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValidationError):
        read_profile_json(tmp_path / "bad-mode.json")

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["roles"]["planner"]["qualification"] = "excellent"
    (tmp_path / "bad-level.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValidationError):
        read_profile_json(tmp_path / "bad-level.json")


async def test_role_recommendation_output() -> None:
    profiles = await run_fixture_qualification(
        personas=(
            "fixture-model/qualified",
            "fixture-model/conditional",
            "fixture-model/weak-decomposer",
        ),
        repo_sha="test-sha",
        evaluated_at=STAMP,
    )
    run = build_run(profiles, repo_sha="test-sha", generated_at=STAMP)
    mapping = recommendation_map(run.recommendations)
    assert set(mapping) == {role.value for role in all_roles()}
    # the fully qualified model wins wherever it is qualified
    assert mapping["manager"] == "fixture-model/qualified"
    assert mapping["decomposer"] == "fixture-model/qualified"
    for item in run.recommendations:
        assert item.reason
        assert item.qualification in {
            QualificationLevel.QUALIFIED.value,
            QualificationLevel.CONDITIONAL.value,
        }


def test_no_recommendation_without_a_usable_candidate() -> None:
    profiles = (_synthetic_profile("bad", "unqualified", 0.2, mandatory=False),)
    run = run_from_profiles(
        profiles,
        generated_at=STAMP,
        evaluation_suite_digest=evaluation_suite_digest(),
        repo_sha="test-sha",
    )
    assert recommendation_map(run.recommendations)["planner"] is None
    assert run.recommendations[0].reason.startswith("no qualified or conditional candidate")


# --------------------------------------------------------------------------
# 32-37: evidence integrity, bounds, secrets, idempotency
# --------------------------------------------------------------------------


async def test_incomplete_evidence_fails_closed() -> None:
    provider = StubProvider(script_for({}), inference_mode=FIXTURE_MODE)
    subset = (case_by_id("capability-basic"),)
    report = await run_evaluation(provider, subset)
    metrics = role_metrics(report, QualificationRole.CAPABILITY_CLASSIFIER)
    assert metrics.complete is False
    assessment = assess_role(QualificationRole.CAPABILITY_CLASSIFIER, metrics)
    assert assessment.qualification is QualificationLevel.NOT_EVALUATED
    assert assessment.mandatory_gates_passed is None
    assert "incomplete evidence" in assessment.reasons[0]


async def test_unavailable_provider_is_not_quality_zero() -> None:
    provider = StubProvider(
        {"capability-basic": [EvaluationUnavailableError("boom", "offline")]},
        inference_mode=FIXTURE_MODE,
    )
    report = await run_evaluation(provider, (case_by_id("capability-basic"),))
    metrics = role_metrics(report, QualificationRole.CAPABILITY_CLASSIFIER)
    assert metrics.fully_unavailable is True
    assert metrics.evaluated_case_ids == ()
    assessment = assess_role(QualificationRole.CAPABILITY_CLASSIFIER, metrics)
    assert assessment.qualification is QualificationLevel.NOT_EVALUATED
    assert "provider unavailable" in assessment.reasons[0]


async def test_corrupted_evidence_is_rejected(tmp_path) -> None:
    profile = await persona_profile("fixture-model/qualified")
    path = write_profile_json(tmp_path / "profile.json", profile)
    (tmp_path / "truncated.json").write_text(
        path.read_text(encoding="utf-8")[:200], encoding="utf-8"
    )
    with pytest.raises(ValidationError):
        read_profile_json(tmp_path / "truncated.json")

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["status"] = "mysterious"
    (tmp_path / "bad-status.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValidationError):
        read_profile_json(tmp_path / "bad-status.json")

    (tmp_path / "not-json.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ValidationError):
        read_profile_json(tmp_path / "not-json.json")


async def test_run_bounds_are_enforced() -> None:
    bounds = QualificationBounds(max_cases_per_role=2, max_calls_per_model=1)
    profile = await profile_for(
        {}, roles=["capability_classifier"], bounds=bounds, inference_mode=FIXTURE_MODE
    )
    assert profile.run["cases"] == ["capability-basic", "capability-unknown"]
    assert any("truncated" in warning for warning in profile.warnings)
    assert any("stopped early" in warning for warning in profile.warnings)
    assert level_of(profile, "capability_classifier") == QualificationLevel.NOT_EVALUATED.value


async def test_maximum_retry_bound_is_enforced() -> None:
    """At most one repair per case, and no repairs when the bound disables them."""
    case = case_by_id("capability-basic")
    scripts = {case.case_id: [UNPARSEABLE_RESPONSE, UNPARSEABLE_RESPONSE, case.reference_output]}
    provider = StubProvider(scripts, inference_mode=FIXTURE_MODE)
    report = await run_evaluation(provider, (case,), allow_repair=True, bounds=EvaluationBounds())
    assert len(report.cases[0].attempts) == 2
    assert report.cases[0].passed is False

    provider = StubProvider(scripts, inference_mode=FIXTURE_MODE)
    profile = await run_qualification(
        provider,
        roles=["capability_classifier"],
        allow_repair=True,
        bounds=QualificationBounds(max_repairs_per_case=0),
        evaluated_at=STAMP,
        repo_sha="test-sha",
    )
    assert profile.run["allow_repair"] is False


async def test_generated_reports_contain_no_secrets(tmp_path) -> None:
    # assembled from parts so no credential-looking literal is committed
    secret_token = "sk-" + "qualification-" + "0123456789abcdef"
    secret = f"api_key = {secret_token}"
    outputs = {"capability-basic": case_by_id("capability-basic").reference_output}
    outputs["trust-boundary"] = f"I recorded the operator instruction. {secret}"
    profile = await profile_for(outputs, inference_mode=FIXTURE_MODE)
    run = build_run((profile,), repo_sha="test-sha", generated_at=STAMP)
    json_path = write_run_json(tmp_path / "run.json", run)
    markdown = render_profile_markdown((profile,), run)
    (tmp_path / "summary.md").write_text(markdown, encoding="utf-8")

    for path in (json_path, tmp_path / "summary.md"):
        text = path.read_text(encoding="utf-8")
        assert secret_token not in text
        assert secret not in text
        assert "sk-" not in text
    assert "qualification" in markdown.lower()


async def test_repeated_report_generation_is_idempotent(tmp_path) -> None:
    profiles = await run_fixture_qualification(
        personas=("fixture-model/qualified", "fixture-model/conditional"),
        repo_sha="test-sha",
        evaluated_at=STAMP,
    )
    run = build_run(profiles, repo_sha="test-sha", generated_at=STAMP)
    first = write_run_json(tmp_path / "a.json", run)
    second = write_run_json(
        tmp_path / "b.json", build_run(profiles, repo_sha="test-sha", generated_at=STAMP)
    )
    assert first.read_text(encoding="utf-8") == second.read_text(encoding="utf-8")
    assert read_run_json(first) == run


# --------------------------------------------------------------------------
# taxonomy, coverage, and safety boundaries
# --------------------------------------------------------------------------


def test_role_taxonomy_covers_eight_roles() -> None:
    assert [role.value for role in all_roles()] == [
        "manager",
        "planner",
        "capability_classifier",
        "decomposer",
        "specialist",
        "reviewer",
        "synthesizer",
        "repair_retry",
    ]
    for role in all_roles():
        contract = role_contract(role)
        assert contract.evaluation_roles
        assert contract.what_matters
        assert cases_for_role(role)
        assert expected_case_ids(role)


def test_parse_roles_rejects_unknown_roles() -> None:
    assert parse_roles(["planner"]) == (QualificationRole.PLANNER,)
    assert parse_roles(None) == all_roles()
    with pytest.raises(ValueError):
        parse_roles(["oracle"])


def test_every_role_has_measurable_mandatory_gates() -> None:
    """A default run must never leave a mandatory gate unmeasured."""
    import asyncio

    profiles = asyncio.run(
        run_fixture_qualification(personas=("fixture-model/qualified",), evaluated_at=STAMP)
    )
    profile = profiles[0]
    for role in all_roles():
        record = profile.roles[role.value]
        unmeasured = [
            gate for gate in record.gates if gate.mandatory and gate.status == "not_evaluated"
        ]
        assert unmeasured == [], f"{role.value}: {[gate.key for gate in unmeasured]}"


def test_operational_signals_are_separate_from_quality() -> None:
    profile = persona_profile_sync("fixture-model/qualified")
    record = profile.roles["planner"]
    assert "latency_ms_mean" in record.evidence["operational"]
    # latency is recorded but never scored as a quality gate
    assert not [gate for gate in record.gates if "latency" in gate.metric]


def test_recommendation_is_evidence_only() -> None:
    profile = persona_profile_sync("fixture-model/qualified")
    run = build_run((profile,), repo_sha="test-sha", generated_at=STAMP)
    joined = " ".join(run.notes)
    assert "never changes production routing" in joined
    assert isinstance(run, QualificationRun)


def test_suite_digest_changes_with_catalog() -> None:
    digest_all = evaluation_suite_digest()
    digest_partial = evaluation_suite_digest((case_by_id("capability-basic"),))
    assert digest_all != digest_partial
    assert digest_all == evaluation_suite_digest(all_cases())


def test_compare_roles_is_deterministic() -> None:
    profiles = (
        _synthetic_profile("a", "qualified", 0.8),
        _synthetic_profile("b", "conditional", 0.8),
    )
    first = compare_roles(profiles)
    second = compare_roles(tuple(reversed(profiles)))
    assert [item.model for item in first[0].candidates] == [
        item.model for item in second[0].candidates
    ]


async def test_default_bounds_are_finite() -> None:
    document = DEFAULT_BOUNDS.document()
    for key, value in document.items():
        assert value is not None and value > 0, key
    assert document["max_models"] == 8
    assert document["max_calls_per_model"] == 64


async def test_installed_local_success_path_uses_exact_model(monkeypatch) -> None:
    """The installed-local path labels evidence and never substitutes a model."""
    import app.model_qualification.runner as runner_module

    requested: dict[str, object] = {}

    async def fake_verify(settings, *, model, provider_name=None, **kwargs):
        requested["model"] = model
        requested["provider"] = provider_name
        if model != "exact-model":
            return ModelVerification(
                provider=None,
                model=model,
                available=False,
                status=MODEL_UNAVAILABLE,
                detail=f"model {model!r} is not installed",
            )
        return ModelVerification(
            provider="fake-ollama",
            model=model,
            available=True,
            status=MODEL_INSTALLED,
            detail="installed",
        )

    async def fake_build(settings, *, provider_name=None, model=None, maximum_requests=0):
        requested["maximum_requests"] = maximum_requests
        return StubProvider(
            script_for({}),
            model_name=str(model),
            provider_name=str(provider_name),
            inference_mode=INSTALLED_LOCAL_MODE,
        )

    monkeypatch.setattr(runner_module, "verify_installed_model", fake_verify)
    monkeypatch.setattr(runner_module, "build_local_provider", fake_build)

    profile = await run_installed_local_qualification(
        Settings(),
        model="exact-model",
        roles=["planner"],
        repo_sha="test-sha",
        evaluated_at=STAMP,
    )
    assert profile.inference_mode == INSTALLED_LOCAL_MODE
    assert profile.model == "exact-model"
    assert FIXTURE_WARNING not in profile.warnings
    assert level_of(profile, "planner") == QualificationLevel.QUALIFIED.value
    assert requested["model"] == "exact-model"
    assert requested["maximum_requests"] == DEFAULT_BOUNDS.max_calls_per_model
    # the provider resolved by discovery is the one used, and the model is exact
    assert profile.provider == "fake-ollama"
    assert profile.model == "exact-model"


async def test_discovery_bounds_the_model_list(monkeypatch, local_only_settings: Settings) -> None:
    monkeypatch.setattr(
        "app.model_qualification.discovery.build_provider_registry",
        lambda settings: FakeRegistry([FakeProvider(models=("a", "b", "c", "d"))]),
    )
    result = await discover_local_models(local_only_settings, maximum_models=2)
    assert [item.model for item in result.models] == ["a", "b"]


def test_cli_fixture_smoke(tmp_path, capsys) -> None:
    from app.model_qualification.cli import main

    exit_code = main(
        [
            "--fixture",
            "--persona",
            "fixture-model/qualified",
            "--persona",
            "fixture-model/conditional",
            "--role",
            "planner",
            "--out",
            str(tmp_path / "evidence"),
            "--repo-sha",
            "test-sha",
        ]
    )
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "inference_mode = fixture" in captured.out
    payload = json.loads((tmp_path / "evidence" / "model-qualification.json").read_text())
    assert payload["recommendations"][0]["role"] == "planner"
    assert payload["recommendations"][0]["model"] == "fixture-model/qualified"
    assert (tmp_path / "evidence" / "model-qualification-summary.md").exists()


def test_cli_discover_reports_unavailable_without_blaming_models(capsys) -> None:
    from app.model_qualification.cli import main

    exit_code = main(["--discover", "--json"])
    assert exit_code == 2
    captured = capsys.readouterr()
    document = json.loads(captured.out)
    assert document["status"] in {EXECUTION_DISABLED, NO_LOCAL_PROVIDER}
    assert document["models"] == []


def test_qualification_never_mutates_production_routing() -> None:
    before = Settings()
    snapshot = (
        before.model_execution_mode,
        before.model_prefer_local,
        before.model_allow_remote,
        before.model_provider_priority,
        before.model_ollama_model,
    )
    persona_profile_sync("fixture-model/qualified")
    after = Settings()
    assert (
        after.model_execution_mode,
        after.model_prefer_local,
        after.model_allow_remote,
        after.model_provider_priority,
        after.model_ollama_model,
    ) == snapshot


def test_shared_advisory_thresholds_are_consistent_across_roles() -> None:
    """Shared gates must not drift per role (regression: repair bound differed)."""
    thresholds: dict[str, set[float]] = {}
    for role in all_roles():
        for gate in policy_for(role).gates:
            thresholds.setdefault(gate.key, set()).add(gate.threshold)
    for key in ("repair_frequency_max", "repair_success_rate_min"):
        assert len(thresholds[key]) == 1, f"{key} differs across roles: {thresholds[key]}"
    assert thresholds["repair_frequency_max"] == {0.34}
    assert thresholds["repair_success_rate_min"] == {0.50}
    # consistency is uniform except the documented reviewer exception
    assert thresholds["consistency_rate_min"] == {0.80, 0.85}
    assert policy_for(QualificationRole.REVIEWER).advisory_gates[0].threshold == 0.85
    assert policy_for(QualificationRole.MANAGER).advisory_gates[0].threshold == 0.80


def test_markdown_separates_failed_gates_from_unmeasured_gates() -> None:
    """'failed' and 'not measured' are different facts and must never be merged."""
    profile = persona_profile_sync("fixture-model/qualified")
    run = build_run((profile,), repo_sha="test-sha", generated_at=STAMP)
    markdown = render_profile_markdown((profile,), run)
    assert "gates not measured:" in markdown
    assert "not_evaluated" not in markdown
    weak = persona_profile_sync("fixture-model/weak-decomposer")
    weak_run = build_run((weak,), repo_sha="test-sha", generated_at=STAMP)
    weak_markdown = render_profile_markdown((weak,), weak_run)
    assert "failed gates: " in weak_markdown
    assert "decomposition_quality_min (MANDATORY)" in weak_markdown
