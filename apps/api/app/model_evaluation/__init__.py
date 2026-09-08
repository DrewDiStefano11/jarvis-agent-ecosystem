"""Reusable local-model evaluation framework (Workstream A).

Evaluates models available through the existing local provider architecture
for AI Hub roles (manager, planning, capability inference, decomposition,
specialist execution, review, synthesis, structured JSON, correction/retry)
using deterministic cases with explicit expected properties.

Two provider modes: deterministic fixture (CI-safe, never real inference)
and installed-local models via the existing router (loopback only, no
downloads, no service startup, clear failures). See
``docs/autonomy-acceptance.md``.
"""

from app.model_evaluation.cases import (
    EvaluationCase,
    EvaluationRole,
    all_cases,
    case_by_id,
    reference_scripts,
)
from app.model_evaluation.expectations import OUTPUT_SCHEMAS, Expectation, ExpectationOutcome
from app.model_evaluation.providers import (
    FIXTURE_MODEL_NAME,
    FIXTURE_PROVIDER_NAME,
    EvalProvider,
    EvaluationUnavailableError,
    LocalRouterProvider,
    ScriptedFixtureProvider,
    build_local_provider,
    describe_identity,
)
from app.model_evaluation.report import (
    EVALUATION_SCHEMA_VERSION,
    CaseEvidence,
    EvaluationEvidence,
    EvaluationInference,
    read_evaluation_json,
    render_evaluation_summary,
    to_evidence,
    write_evaluation_json,
    write_evaluation_summary,
)
from app.model_evaluation.runner import (
    REPAIR_SUFFIX,
    AttemptRecord,
    CaseEvaluation,
    EvalFailureCode,
    EvaluationBounds,
    EvaluationReport,
    aggregate_metrics,
    build_request,
    context_sensitivity,
    run_evaluation,
    score_expectations,
)

__all__ = [
    "EVALUATION_SCHEMA_VERSION",
    "FIXTURE_MODEL_NAME",
    "FIXTURE_PROVIDER_NAME",
    "REPAIR_SUFFIX",
    "AttemptRecord",
    "CaseEvaluation",
    "CaseEvidence",
    "EvalFailureCode",
    "EvalProvider",
    "EvaluationBounds",
    "EvaluationCase",
    "EvaluationEvidence",
    "EvaluationInference",
    "EvaluationReport",
    "EvaluationRole",
    "EvaluationUnavailableError",
    "Expectation",
    "ExpectationOutcome",
    "LocalRouterProvider",
    "OUTPUT_SCHEMAS",
    "ScriptedFixtureProvider",
    "aggregate_metrics",
    "all_cases",
    "build_local_provider",
    "build_request",
    "case_by_id",
    "context_sensitivity",
    "describe_identity",
    "read_evaluation_json",
    "reference_scripts",
    "render_evaluation_summary",
    "run_evaluation",
    "score_expectations",
    "to_evidence",
    "write_evaluation_json",
    "write_evaluation_summary",
]
