"""Local-model evaluation tests: fixture controls, bounds, and preflight."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime

import pytest

from app.core.config import Settings
from app.model_evaluation.cases import (
    EvaluationRole,
    all_cases,
    case_by_id,
    reference_scripts,
)
from app.model_evaluation.providers import (
    EvalProvider,
    EvaluationUnavailableError,
    ScriptedFixtureProvider,
    build_local_provider,
)
from app.model_evaluation.report import read_evaluation_json, to_evidence, write_evaluation_json
from app.model_evaluation.runner import (
    EvalFailureCode,
    EvaluationBounds,
    run_evaluation,
)
from app.model_providers.contracts import ModelExecutionRequest, ModelExecutionResponse


async def test_fixture_positive_control_passes_every_case() -> None:
    provider = ScriptedFixtureProvider(reference_scripts(repetitions=3))
    report = await run_evaluation(provider, all_cases(), repetitions=3)
    assert report.metrics["case_pass_rate"] == 1.0
    assert report.metrics["consistency_rate"] == 1.0
    assert report.metrics["structured_output_success_rate"] == 1.0
    assert report.metrics["schema_validity_rate"] == 1.0
    assert report.metrics["capability_classification_accuracy"] == 1.0
    assert report.metrics["decomposition_quality"] == 1.0
    assert report.metrics["synthesis_completeness"] == 1.0
    assert report.metrics["hallucination_rate"] == 0.0
    assert report.metrics["malformed_response_rate"] == 0.0
    assert report.metrics["trust_boundary_rate"] == 1.0
    assert report.metrics["bounded_instruction_rate"] == 1.0
    assert report.metrics["secret_pass_rate"] == 1.0
    assert report.metrics["tokens_reported"] is False
    assert report.metrics["context_sensitivity"] == {"context-large": 0}
    assert report.inference_mode == "fixture"
    assert set(report.failure_codes) == {"ok"}


async def test_every_adversarial_output_fails_its_case() -> None:
    failures: list[str] = []
    for case in all_cases():
        for name, output in case.adversarial_outputs.items():
            provider = ScriptedFixtureProvider({case.case_id: [output]})
            report = await run_evaluation(provider, (case,))
            if report.metrics["case_pass_rate"] != 0.0:
                failures.append(f"{case.case_id}/{name}")
    assert failures == []


async def test_repair_path_recovers_and_is_measured() -> None:
    case = case_by_id("capability-basic")
    provider = ScriptedFixtureProvider({case.case_id: ["{broken json", case.reference_output]})
    report = await run_evaluation(provider, (case,), allow_repair=True)
    assert report.metrics["case_pass_rate"] == 1.0
    assert report.metrics["repair_frequency"] == 0.5
    assert report.metrics["repair_success_rate"] == 1.0
    assert report.cases[0].attempts[-1].failure_code == EvalFailureCode.OK


async def test_repair_disabled_keeps_malformed_failure() -> None:
    case = case_by_id("capability-basic")
    provider = ScriptedFixtureProvider({case.case_id: ["{broken json"]})
    report = await run_evaluation(provider, (case,), allow_repair=False)
    assert report.metrics["case_pass_rate"] == 0.0
    assert report.failure_codes.get("json_parse_error") == 1


async def test_provider_error_maps_to_failure_code() -> None:
    case = case_by_id("capability-basic")
    provider = ScriptedFixtureProvider(
        {case.case_id: [EvaluationUnavailableError("boom", "simulated provider failure")]}
    )
    report = await run_evaluation(provider, (case,))
    assert report.cases[0].attempts[0].failure_code == EvalFailureCode.PROVIDER_ERROR
    assert report.metrics["case_pass_rate"] == 0.0


async def test_fixture_exhaustion_fails_loudly() -> None:
    case = case_by_id("capability-basic")
    provider = ScriptedFixtureProvider({})
    report = await run_evaluation(provider, (case,))
    assert report.cases[0].attempts[0].failure_code == EvalFailureCode.PROVIDER_ERROR


async def test_call_timeout_maps_to_failure_code() -> None:
    class SlowProvider:
        name = "slow"
        model_name = "slow-1"
        inference_mode = "fixture"
        is_local = True

        async def generate(self, request: ModelExecutionRequest) -> ModelExecutionResponse:
            await asyncio.sleep(5)
            raise AssertionError("should have timed out")

    case = case_by_id("trust-boundary")
    provider: EvalProvider = SlowProvider()  # type: ignore[assignment]
    report = await run_evaluation(
        provider, (case,), bounds=EvaluationBounds(per_call_timeout_seconds=0.05)
    )
    assert report.cases[0].attempts[0].failure_code == EvalFailureCode.TIMEOUT


async def test_call_level_failures_do_not_inflate_structure_rates() -> None:
    """Timeouts/oversize responses count as structure failures, never successes."""

    class SlowProvider:
        name = "slow"
        model_name = "slow-1"
        inference_mode = "fixture"
        is_local = True

        async def generate(self, request: ModelExecutionRequest) -> ModelExecutionResponse:
            await asyncio.sleep(5)
            raise AssertionError("should have timed out")

    case = case_by_id("capability-basic")
    provider: EvalProvider = SlowProvider()  # type: ignore[assignment]
    report = await run_evaluation(
        provider, (case,), bounds=EvaluationBounds(per_call_timeout_seconds=0.05)
    )
    assert report.metrics["structured_output_success_rate"] == 0.0
    assert report.metrics["schema_validity_rate"] == 0.0
    assert report.metrics["malformed_response_rate"] == 1.0
    assert report.failure_codes == {EvalFailureCode.TIMEOUT.value: 1}


async def test_output_too_large_maps_to_failure_code() -> None:
    case = case_by_id("trust-boundary")
    provider = ScriptedFixtureProvider({case.case_id: ["x" * 500]})
    report = await run_evaluation(provider, (case,), bounds=EvaluationBounds(max_output_chars=100))
    assert report.cases[0].attempts[0].failure_code == EvalFailureCode.OUTPUT_TOO_LARGE


async def test_call_budget_stops_run_deterministically() -> None:
    provider = ScriptedFixtureProvider(reference_scripts())
    report = await run_evaluation(
        provider,
        all_cases(),
        bounds=EvaluationBounds(max_calls=1),
    )
    assert report.stopped_early == EvalFailureCode.CALL_BUDGET_EXCEEDED.value
    assert report.metrics["total_calls"] == 1


async def test_secret_bearing_response_fails_secret_expectation() -> None:
    case = case_by_id("capability-basic")
    tampered = case.reference_output.replace(
        "reasoning_summary", 'reasoning_summary", "leak": "api_key = sk-fake-0123456789abcdef'
    )
    provider = ScriptedFixtureProvider({case.case_id: [tampered]})
    report = await run_evaluation(provider, (case,))
    assert report.metrics["case_pass_rate"] == 0.0
    assert report.metrics["secret_pass_rate"] == 0.0


async def test_inconsistency_detected_across_repetitions() -> None:
    case = case_by_id("trust-boundary")
    provider = ScriptedFixtureProvider(
        {case.case_id: [case.reference_output, case.reference_output + " Extra sentence."]}
    )
    report = await run_evaluation(provider, (case,), repetitions=2)
    assert report.cases[0].consistent is False
    assert report.metrics["consistency_rate"] == 0.0


def test_evaluation_evidence_round_trip(tmp_path) -> None:
    async def run() -> None:
        provider = ScriptedFixtureProvider(reference_scripts())
        return await run_evaluation(provider, all_cases())

    report = asyncio.run(run())
    evidence = to_evidence(
        report, repo_sha="test-sha", started_at=datetime.now(UTC), ended_at=datetime.now(UTC)
    )
    assert evidence.inference.mode == "fixture"
    assert set(evidence.roles) == {role.value for role in EvaluationRole}
    target = write_evaluation_json(tmp_path / "eval.json", evidence)
    assert read_evaluation_json(target) == evidence
    assert len(evidence.cases) == len(all_cases())


async def test_local_preflight_rejects_disabled_execution(monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_MODEL_EXECUTION_MODE", "disabled")
    settings = Settings()
    assert settings.model_execution_mode == "disabled"
    with pytest.raises(EvaluationUnavailableError) as excinfo:
        await build_local_provider(settings)
    assert excinfo.value.code == "execution_disabled"


async def test_local_preflight_rejects_unknown_provider(monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_MODEL_EXECUTION_MODE", "local_only")
    settings = Settings()
    assert settings.model_execution_mode == "local_only"
    with pytest.raises(EvaluationUnavailableError) as excinfo:
        await build_local_provider(settings, provider_name="does-not-exist")
    assert excinfo.value.code in {"no_local_provider", "unknown_or_remote_provider"}


async def test_installed_local_model_evaluation_when_available() -> None:
    """Real local-model evaluation; skips unless explicitly requested.

    Set JARVIS_EVAL_LOCAL_MODEL=<model> (with JARVIS_MODEL_EXECUTION_MODE=
    local_only and JARVIS_MODEL_OLLAMA_ENABLED=true) to run installed-model
    inference. Never downloads models or starts services.
    """
    model = os.environ.get("JARVIS_EVAL_LOCAL_MODEL")
    if not model:
        pytest.skip("JARVIS_EVAL_LOCAL_MODEL is not set")
    settings = Settings()
    try:
        provider = await build_local_provider(
            settings,
            provider_name=os.environ.get("JARVIS_EVAL_LOCAL_PROVIDER"),
            model=model,
        )
    except EvaluationUnavailableError as exc:
        pytest.skip(f"local model unavailable: {exc}")
    subset = (
        case_by_id("capability-basic"),
        case_by_id("structured-strict"),
        case_by_id("trust-boundary"),
    )
    report = await run_evaluation(provider, subset)
    assert report.inference_mode == "installed_local"
    assert report.model_name == model
    assert report.metrics["total_calls"] == len(subset)
