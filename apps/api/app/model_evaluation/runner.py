"""Deterministic evaluation runner and metric aggregation.

Metric formulas (exact, no subjective scores):

- ``case_pass_rate`` = passed_cases / total_cases. A case passes when every
  expectation passes on every repetition (after at most one repair per
  validation failure when ``allow_repair`` is set; repairs apply to validation
  failures, never to call-level failures). Category rates use the final
  (post-repair) primary outcomes.
- ``structured_output_success_rate`` = json_parse_ok / json_cases, where
  json_cases are cases declaring an ``ExpectJsonParses`` expectation.
- ``schema_validity_rate`` = schema_ok / schema_cases over cases declaring
  ``ExpectSchemaValid``.
- ``instruction_following_rate`` = passed / total over ``instruction``
  expectations.
- ``capability_classification_accuracy`` = passed / total over
  ``classification`` expectations (exact normalized set match).
- ``decomposition_quality`` = mean ``ExpectGraphValid`` score (passed
  subchecks / 6) over decomposition cases; None when absent.
- ``synthesis_completeness`` = mean ``ExpectSynthesisCoverage`` score
  (covered-recall) over synthesis cases; None when absent.
- ``consistency_rate`` = repetition-identical cases / multi-repetition cases;
  None when every case ran once. Consistency compares primary (pre-repair)
  responses, so repairs never mask provider variance.
- ``hallucination_rate`` = 1 - (trust-id passes / trust-id total) over
  ``ExpectNoInventedIds`` expectations; None when absent.
- ``malformed_response_rate`` = json_parse_failures / json_cases.
- ``repair_frequency`` = repair_calls / total_calls; ``repair_success_rate`` =
  repaired_ok / repair_calls (None when no repairs).
- ``bounded_instruction_rate`` = passed / total over ``bounded`` expectations.
- ``trust_boundary_rate`` = passed / total over ``trust`` expectations.
- ``secret_pass_rate`` = passed / total over ``secret`` expectations.
- ``context_sensitivity``: per ``context_variant_of`` pair,
  ``small_pass - large_pass`` in {-1, 0, 1} (1 means the large context
  degraded the verdict).
- ``failure_codes``: histogram of :class:`EvalFailureCode` per executed call.
- Latency: mean/p95/max milliseconds over executed calls.
- Tokens: summed input/output totals where the provider reported them.

Runaway protection: ``max_calls`` bounds total provider calls (including
repairs), ``per_call_timeout_seconds`` bounds each call, ``max_output_chars``
bounds accepted responses. Violations end the run deterministically with a
machine-readable code; nothing retries forever.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.autonomy.events import scrub_text
from app.model_evaluation.cases import EvaluationCase, EvaluationRole
from app.model_evaluation.expectations import (
    OUTPUT_SCHEMAS,
    Expectation,
    ExpectationOutcome,
    ExpectGraphValid,
    ExpectJsonParses,
    ExpectNoInventedIds,
    ExpectSchemaValid,
    ExpectSynthesisCoverage,
    try_parse_json,
)
from app.model_evaluation.providers import EvalProvider
from app.model_providers.contracts import (
    MessageRole,
    ModelExecutionRequest,
    ModelMessage,
    ModelOutputSchema,
)

REPAIR_SUFFIX = (
    "\n\nYour previous response failed validation. Respond again, carefully "
    "following the output contract exactly."
)


class EvalFailureCode(StrEnum):
    OK = "ok"
    JSON_PARSE_ERROR = "json_parse_error"
    SCHEMA_INVALID = "schema_invalid"
    EXPECTATION_FAILED = "expectation_failed"
    PROVIDER_ERROR = "provider_error"
    TIMEOUT = "timeout"
    OUTPUT_TOO_LARGE = "output_too_large"
    CALL_BUDGET_EXCEEDED = "call_budget_exceeded"


@dataclass(frozen=True)
class ExpectationResult:
    name: str
    category: str
    passed: bool
    detail: str
    score: float | None = None


@dataclass
class AttemptRecord:
    repetition: int
    is_repair: bool
    failure_code: EvalFailureCode
    latency_ms: float
    output_chars: int
    parsed_ok: bool | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    content_digest: str = ""
    expectations: tuple[ExpectationResult, ...] = ()


@dataclass
class CaseEvaluation:
    case_id: str
    role: EvaluationRole
    passed: bool
    consistent: bool | None
    expectations: tuple[Expectation, ...] = ()
    context_variant_of: str | None = None
    attempts: list[AttemptRecord] = field(default_factory=list)


@dataclass(frozen=True)
class EvaluationBounds:
    max_calls: int = 200
    per_call_timeout_seconds: float = 120.0
    max_output_chars: int = 20000


@dataclass
class EvaluationReport:
    provider_name: str
    model_name: str
    inference_mode: str
    repetitions: int
    allow_repair: bool
    bounds: EvaluationBounds
    cases: list[CaseEvaluation] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    failure_codes: dict[str, int] = field(default_factory=dict)
    stopped_early: str | None = None


def build_request(case: EvaluationCase, *, repetition: int, repair: bool) -> ModelExecutionRequest:
    user_prompt = case.user_prompt + (REPAIR_SUFFIX if repair else "")
    output_schema = None
    if case.output_schema_name is not None:
        model = OUTPUT_SCHEMAS[case.output_schema_name]
        output_schema = ModelOutputSchema(
            name=case.output_schema_name, json_schema=model.model_json_schema()
        )
    return ModelExecutionRequest(
        messages=[
            ModelMessage(role=MessageRole.SYSTEM, content=case.system_prompt),
            ModelMessage(role=MessageRole.USER, content=user_prompt),
        ],
        output_schema=output_schema,
        prefer_no_reasoning=True,
        temperature=0.0,
        task_id=f"{case.case_id}:rep{repetition}:repair{int(repair)}",
        correlation_id=f"eval-{case.case_id}",
    )


def score_expectations(
    expectations: tuple[Expectation, ...], content: str
) -> tuple[tuple[ExpectationResult, ...], EvalFailureCode]:
    parsed, parses = try_parse_json(content)
    results: list[ExpectationResult] = []
    for expectation in expectations:
        outcome: ExpectationOutcome = expectation.check(content, parsed)
        results.append(
            ExpectationResult(
                name=expectation.name,
                category=expectation.category,
                passed=outcome.passed,
                detail=scrub_text(outcome.detail)[:400],
                score=outcome.score,
            )
        )
    if not parses and any(isinstance(item, ExpectJsonParses) for item in expectations):
        return tuple(results), EvalFailureCode.JSON_PARSE_ERROR
    if any(
        isinstance(item, ExpectSchemaValid) and not result.passed
        for item, result in zip(expectations, results, strict=True)
    ):
        return tuple(results), EvalFailureCode.SCHEMA_INVALID
    if all(result.passed for result in results):
        return tuple(results), EvalFailureCode.OK
    return tuple(results), EvalFailureCode.EXPECTATION_FAILED


async def _generate(
    provider: EvalProvider, request: ModelExecutionRequest, timeout_seconds: float
) -> tuple[str, float, int | None, int | None, EvalFailureCode]:
    started = time.perf_counter()
    try:
        response = await asyncio.wait_for(provider.generate(request), timeout=timeout_seconds)
    except TimeoutError:
        elapsed = (time.perf_counter() - started) * 1000
        return "", elapsed, None, None, EvalFailureCode.TIMEOUT
    except Exception:  # noqa: BLE001 - provider failures map to a failure code
        elapsed = (time.perf_counter() - started) * 1000
        return "", elapsed, None, None, EvalFailureCode.PROVIDER_ERROR
    elapsed = (time.perf_counter() - started) * 1000
    return (
        response.content,
        elapsed,
        response.input_tokens,
        response.output_tokens,
        EvalFailureCode.OK,
    )


async def run_evaluation(
    provider: EvalProvider,
    cases: tuple[EvaluationCase, ...],
    *,
    repetitions: int = 1,
    allow_repair: bool = False,
    bounds: EvaluationBounds | None = None,
) -> EvaluationReport:
    """Run every case and aggregate deterministic metrics."""
    active_bounds = bounds or EvaluationBounds()
    report = EvaluationReport(
        provider_name=provider.name,
        model_name=provider.model_name,
        inference_mode=provider.inference_mode,
        repetitions=repetitions,
        allow_repair=allow_repair,
        bounds=active_bounds,
    )
    total_calls = 0

    def record_failure(code: EvalFailureCode) -> None:
        report.failure_codes[code.value] = report.failure_codes.get(code.value, 0) + 1

    for case in cases:
        evaluation = CaseEvaluation(
            case_id=case.case_id,
            role=case.role,
            passed=True,
            consistent=None,
            expectations=case.expectations,
            context_variant_of=case.context_variant_of,
        )
        contents: list[str] = []
        for repetition in range(1, max(1, repetitions) + 1):
            if total_calls >= active_bounds.max_calls:
                report.stopped_early = EvalFailureCode.CALL_BUDGET_EXCEEDED.value
                evaluation.passed = False
                break
            total_calls += 1
            request = build_request(case, repetition=repetition, repair=False)
            content, latency, in_tokens, out_tokens, call_code = await _generate(
                provider, request, active_bounds.per_call_timeout_seconds
            )
            limit = min(active_bounds.max_output_chars, case.max_output_chars)
            if call_code == EvalFailureCode.OK and len(content) > limit:
                call_code = EvalFailureCode.OUTPUT_TOO_LARGE
                content = content[:limit]
            if call_code != EvalFailureCode.OK:
                record_failure(call_code)
                evaluation.attempts.append(
                    AttemptRecord(
                        repetition=repetition,
                        is_repair=False,
                        failure_code=call_code,
                        latency_ms=latency,
                        output_chars=len(content),
                        input_tokens=in_tokens,
                        output_tokens=out_tokens,
                    )
                )
                evaluation.passed = False
                continue
            contents.append(content.strip())
            _, parses = try_parse_json(content)
            scored, score_code = score_expectations(case.expectations, content)
            final_scored = scored
            final_code = score_code
            final_parsed_ok: bool | None = parses
            if score_code != EvalFailureCode.OK and allow_repair:
                if total_calls >= active_bounds.max_calls:
                    report.stopped_early = EvalFailureCode.CALL_BUDGET_EXCEEDED.value
                    evaluation.passed = False
                    break
                total_calls += 1
                repair_request = build_request(case, repetition=repetition, repair=True)
                (
                    repair_content,
                    repair_latency,
                    repair_in,
                    repair_out,
                    repair_code,
                ) = await _generate(
                    provider, repair_request, active_bounds.per_call_timeout_seconds
                )
                if repair_code == EvalFailureCode.OK and len(repair_content) > limit:
                    repair_code = EvalFailureCode.OUTPUT_TOO_LARGE
                    repair_content = repair_content[:limit]
                if repair_code == EvalFailureCode.OK:
                    _, repair_parses = try_parse_json(repair_content)
                    repaired_scored, repaired_code = score_expectations(
                        case.expectations, repair_content
                    )
                    evaluation.attempts.append(
                        AttemptRecord(
                            repetition=repetition,
                            is_repair=True,
                            failure_code=repaired_code,
                            latency_ms=repair_latency,
                            output_chars=len(repair_content),
                            parsed_ok=repair_parses,
                            input_tokens=repair_in,
                            output_tokens=repair_out,
                            expectations=repaired_scored,
                        )
                    )
                    record_failure(repaired_code)
                    if repaired_code == EvalFailureCode.OK:
                        final_scored = repaired_scored
                        final_code = repaired_code
                        final_parsed_ok = repair_parses
                else:
                    evaluation.attempts.append(
                        AttemptRecord(
                            repetition=repetition,
                            is_repair=True,
                            failure_code=repair_code,
                            latency_ms=repair_latency,
                            output_chars=len(repair_content),
                            input_tokens=repair_in,
                            output_tokens=repair_out,
                        )
                    )
                    record_failure(repair_code)
            evaluation.attempts.append(
                AttemptRecord(
                    repetition=repetition,
                    is_repair=False,
                    failure_code=final_code,
                    latency_ms=latency,
                    output_chars=len(content),
                    parsed_ok=final_parsed_ok,
                    input_tokens=in_tokens,
                    output_tokens=out_tokens,
                    expectations=final_scored,
                )
            )
            record_failure(final_code)
            if final_code != EvalFailureCode.OK:
                evaluation.passed = False
        if max(1, repetitions) > 1 and len(contents) == max(1, repetitions):
            evaluation.consistent = len(set(contents)) == 1
        report.cases.append(evaluation)
        if report.stopped_early:
            break
    report.metrics = aggregate_metrics(report)
    report.metrics["context_sensitivity"] = context_sensitivity(report)
    return report


def aggregate_metrics(report: EvaluationReport) -> dict[str, Any]:
    cases = report.cases
    total = len(cases)
    passed = sum(1 for case in cases if case.passed)
    primary = [attempt for case in cases for attempt in case.attempts if not attempt.is_repair]
    repairs = [attempt for case in cases for attempt in case.attempts if attempt.is_repair]

    def rate(passed_count: int, total_count: int) -> float | None:
        return passed_count / total_count if total_count else None

    json_cases = [
        case
        for case in cases
        if _declares(case, ExpectJsonParses) or _declares(case, ExpectSchemaValid)
    ]
    schema_cases = [case for case in cases if _declares(case, ExpectSchemaValid)]
    json_ok = sum(
        1 for case in json_cases for attempt in _primary_attempts(case) if attempt.parsed_ok is True
    )
    json_total = sum(len(_primary_attempts(case)) for case in json_cases)
    schema_ok = sum(
        1
        for case in schema_cases
        for attempt in _primary_attempts(case)
        if _schema_passed(case, attempt)
    )
    schema_total = sum(len(_primary_attempts(case)) for case in schema_cases)
    category = _category_rates(cases)
    graph_scores = _scores(cases, ExpectGraphValid)
    synthesis_scores = _scores(cases, ExpectSynthesisCoverage)
    invented_total, invented_passed = _invented_stats(cases)
    multi = [case for case in cases if case.consistent is not None]
    latencies = sorted(attempt.latency_ms for case in cases for attempt in case.attempts)
    input_tokens = sum(attempt.input_tokens or 0 for case in cases for attempt in case.attempts)
    output_tokens = sum(attempt.output_tokens or 0 for case in cases for attempt in case.attempts)
    tokens_reported = any(
        attempt.input_tokens is not None or attempt.output_tokens is not None
        for case in cases
        for attempt in case.attempts
    )
    total_calls = len(primary) + len(repairs)
    return {
        "total_cases": total,
        "passed_cases": passed,
        "case_pass_rate": rate(passed, total),
        "total_calls": total_calls,
        "structured_output_success_rate": rate(json_ok, json_total),
        "schema_validity_rate": rate(schema_ok, schema_total),
        "instruction_following_rate": category.get("instruction"),
        "capability_classification_accuracy": category.get("classification"),
        "decomposition_quality": _mean(graph_scores),
        "synthesis_completeness": _mean(synthesis_scores),
        "consistency_rate": rate(sum(1 for case in multi if case.consistent), len(multi)),
        "hallucination_rate": (
            None if invented_total == 0 else 1 - invented_passed / invented_total
        ),
        "malformed_response_rate": (
            None if json_total == 0 else (json_total - json_ok) / json_total
        ),
        "repair_frequency": (len(repairs) / total_calls) if total_calls else 0.0,
        "repair_success_rate": rate(
            sum(1 for attempt in repairs if attempt.failure_code == EvalFailureCode.OK),
            len(repairs),
        ),
        "bounded_instruction_rate": category.get("bounded"),
        "trust_boundary_rate": category.get("trust"),
        "secret_pass_rate": category.get("secret"),
        "structure_pass_rate": category.get("structure"),
        "latency_ms_mean": _mean(latencies),
        "latency_ms_p95": _percentile(latencies, 0.95),
        "latency_ms_max": max(latencies) if latencies else None,
        "input_tokens_total": input_tokens if tokens_reported else None,
        "output_tokens_total": output_tokens if tokens_reported else None,
        "tokens_reported": tokens_reported,
    }


def context_sensitivity(report: EvaluationReport) -> dict[str, int]:
    """Per paired variant: small_pass - large_pass in {-1, 0, 1}."""
    by_id = {case.case_id: case for case in report.cases}
    deltas: dict[str, int] = {}
    for case in report.cases:
        if case.context_variant_of is None:
            continue
        small = by_id.get(case.context_variant_of)
        if small is None:
            continue
        deltas[case.case_id] = int(small.passed) - int(case.passed)
    return deltas


def _declares(case: CaseEvaluation, kind: type[Expectation]) -> bool:
    return any(isinstance(item, kind) for item in case.expectations)


def _primary_attempts(case: CaseEvaluation) -> list[AttemptRecord]:
    return [attempt for attempt in case.attempts if not attempt.is_repair]


def _schema_passed(case: CaseEvaluation, attempt: AttemptRecord) -> bool:
    names = {item.name for item in case.expectations if isinstance(item, ExpectSchemaValid)}
    results = [result for result in attempt.expectations if result.name in names]
    return bool(results) and all(result.passed for result in results)


def _category_rates(cases: list[CaseEvaluation]) -> dict[str, float | None]:
    totals: dict[str, int] = {}
    passed: dict[str, int] = {}
    for case in cases:
        for attempt in _primary_attempts(case):
            for expectation in attempt.expectations:
                totals[expectation.category] = totals.get(expectation.category, 0) + 1
                if expectation.passed:
                    passed[expectation.category] = passed.get(expectation.category, 0) + 1
    return {
        category: (passed.get(category, 0) / total if total else None)
        for category, total in totals.items()
    }


def _scores(cases: list[CaseEvaluation], kind: type[Expectation]) -> list[float]:
    scores: list[float] = []
    for case in cases:
        names = {item.name for item in case.expectations if isinstance(item, kind)}
        for attempt in _primary_attempts(case):
            for expectation in attempt.expectations:
                if expectation.name in names and expectation.score is not None:
                    scores.append(expectation.score)
    return scores


def _invented_stats(cases: list[CaseEvaluation]) -> tuple[int, int]:
    total = 0
    passed = 0
    for case in cases:
        names = {item.name for item in case.expectations if isinstance(item, ExpectNoInventedIds)}
        for attempt in _primary_attempts(case):
            for expectation in attempt.expectations:
                if expectation.name in names:
                    total += 1
                    passed += int(expectation.passed)
    return total, passed


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(quantile * len(ordered))))
    return ordered[index]
