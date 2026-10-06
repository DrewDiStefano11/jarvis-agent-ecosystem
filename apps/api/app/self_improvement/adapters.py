"""Allowlist projections of existing evidence, never raw provider text."""

from datetime import UTC, datetime
from typing import Protocol

from app.autonomy.evidence import AcceptanceEvidence
from app.model_evaluation.report import EvaluationEvidence
from app.model_qualification.profile import ModelProfile
from app.models.self_improvement import EvaluationObservation, SourceProvenance
from app.self_improvement.engine import digest

# Thresholds here only detect absolute recorded defects. Qualification gates use
# the original policy thresholds instead. Efficiency values are measured only.
METRICS = {
    "schema_validity_rate": ("higher", 1, "model_role"),
    "structured_output_success_rate": ("higher", 1, "model_role"),
    "instruction_following_rate": ("higher", 1, "model_role"),
    "malformed_response_rate": ("lower", 0, "reliability"),
    "hallucination_rate": ("lower", 0, "model_role"),
    "capability_classification_accuracy": ("higher", 1, "planning"),
    "decomposition_quality": ("higher", 1, "planning"),
    "synthesis_completeness": ("higher", 1, "model_role"),
    "repair_frequency": ("lower", None, "efficiency"),
    "repair_success_rate": ("higher", None, "reliability"),
    "latency_ms_mean": ("lower", None, "efficiency"),
    "latency_ms_p95": ("lower", None, "efficiency"),
    "latency_ms_max": ("lower", None, "efficiency"),
    "total_calls": ("lower", None, "efficiency"),
    "request_count": ("lower", None, "efficiency"),
    "repair_count": ("lower", None, "efficiency"),
    "malformed_response_count": ("lower", None, "reliability"),
    "bounded_instruction_rate": ("higher", 1, "execution"),
    "trust_boundary_rate": ("higher", 1, "execution"),
    "secret_pass_rate": ("higher", 1, "execution"),
}


class EvidenceSource(Protocol):
    """#63 can implement this without importing or changing this engine."""

    def collect(self) -> tuple[SourceProvenance, tuple[EvaluationObservation, ...]]: ...


def observation(
    source,
    timestamp,
    subject,
    stage,
    metric,
    actual,
    *,
    expected=None,
    direction="higher",
    category="reliability",
    role="unknown",
    model="unknown",
    provider="unknown",
    inference_mode="unknown",
    failure_code=None,
    hard_gate=False,
    task_id=None,
    checkpoint_id=None,
    context_id=None,
):
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    fields = dict(
        source_type=source.source_type,
        source_id=source.source_id,
        evidence_digest=source.digest,
        timestamp=timestamp,
        subject_id=subject,
        stage=stage,
        metric=metric,
        actual=actual,
        expected=expected,
        direction=direction,
        category=category,
        role=role,
        model=model,
        provider=provider,
        inference_mode=inference_mode,
        failure_code=failure_code,
        hard_gate=hard_gate,
        measured=actual is not None,
        task_id=task_id,
        checkpoint_id=checkpoint_id,
        context_id=context_id,
    )
    stable = {**fields, "timestamp": timestamp.isoformat()}
    return EvaluationObservation(id=digest(stable), **fields)


def metric_observations(
    source, timestamp, subject, stage, metrics, *, enforce_expectations=True, **identity
):
    result = []
    for name, (direction, expected, category) in METRICS.items():
        if name not in metrics:
            continue
        value = metrics[name]
        if value is not None and not isinstance(value, (int, float)):
            raise ValueError("metric must be numeric or missing")
        result.append(
            observation(
                source,
                timestamp,
                subject,
                stage,
                name,
                value,
                expected=expected if enforce_expectations else None,
                direction=direction,
                category=category,
                hard_gate=name
                in ("trust_boundary_rate", "secret_pass_rate", "bounded_instruction_rate"),
                **identity,
            )
        )
    return result


class ArtifactSource:
    def __init__(self, kind, source_id, payload):
        self.kind, self.source_id, self.payload = kind, source_id, payload

    def collect(self):
        if self.kind == "ci_run":
            from app.self_improvement.ci import CIRunSource

            return CIRunSource(self.source_id, self.payload).collect()
        if (
            self.kind in ("autonomy_acceptance", "model_evaluation", "model_qualification")
            and self.payload.get("schema_version", "1.0") != "1.0"
        ):
            raise ValueError("unsupported evidence schema version")
        if self.kind == "autonomy_acceptance":
            return self.acceptance(AcceptanceEvidence.model_validate(self.payload))
        if self.kind == "model_evaluation":
            return self.evaluation(EvaluationEvidence.model_validate(self.payload))
        if self.kind == "model_qualification":
            return self.qualification(ModelProfile.model_validate(self.payload))
        if self.kind == "runtime_doctor":
            return self.doctor()
        raise ValueError("unsupported artifact evidence source")

    def base(self, payload, **fields):
        return SourceProvenance(
            source_type=self.kind, source_id=self.source_id, digest=digest(payload), **fields
        )

    def acceptance(self, record):
        source = self.base(
            record,
            schema_version=record.schema_version,
            repo_sha=record.repo_sha,
            case_ids=(record.scenario,),
            configuration_digest=digest(record.bounds),
        )
        identity = dict(
            model=record.inference.model,
            provider=record.inference.provider,
            inference_mode=record.inference.mode,
        )
        observations = [
            observation(
                source,
                record.ended_at,
                record.scenario,
                "autonomy",
                "scenario_pass",
                int(record.verdict == "pass"),
                expected=1,
                **identity,
            )
        ]
        for check in record.checks:
            observations.append(
                observation(
                    source,
                    record.ended_at,
                    record.scenario,
                    "autonomy",
                    "check:" + check.name,
                    int(check.passed),
                    expected=1,
                    **identity,
                )
            )
        for name, value in record.counts.model_dump().items():
            observations.append(
                observation(
                    source,
                    record.ended_at,
                    record.scenario,
                    "autonomy",
                    name,
                    value,
                    direction="lower"
                    if name in ("model_calls", "repairs", "retries", "failed_tasks")
                    else "higher",
                    category="efficiency",
                    **identity,
                )
            )
        if record.counts.tasks:
            observations.append(
                observation(
                    source,
                    record.ended_at,
                    record.scenario,
                    "autonomy",
                    "task_completion",
                    record.counts.completed_tasks / record.counts.tasks,
                    expected=1 if record.verdict == "fail" else None,
                    **identity,
                )
            )
        # Retry exhaustion is inferred only from an explicitly recorded event.
        for event in record.timeline:
            if event.event in ("retry_exhausted", "validation_failed", "tool_failed", "recovery"):
                observations.append(
                    observation(
                        source,
                        event.timestamp,
                        f"event:{event.seq}",
                        event.stage.value,
                        event.event,
                        1,
                        expected=0
                        if event.event != "recovery" and record.verdict == "fail"
                        else None,
                        direction="lower",
                        category="execution"
                        if event.event in ("validation_failed", "tool_failed")
                        else "reliability",
                        failure_code=event.event,
                        **identity,
                    )
                )
        return source, tuple(observations)

    def evaluation(self, record):
        source = self.base(
            record,
            schema_version=record.schema_version,
            repo_sha=record.repo_sha,
            case_ids=tuple(sorted(c.case_id for c in record.cases)),
            configuration_digest=digest([record.bounds, record.repetitions, record.allow_repair]),
        )
        identity = dict(
            model=record.inference.model,
            provider=record.inference.provider,
            inference_mode=record.inference.mode,
        )
        call_failures = ("provider_error", "timeout", "call_budget_exceeded")
        unavailable_cases = {
            case.case_id
            for case in record.cases
            if case.failure_codes and all(c in call_failures for c in case.failure_codes)
        }
        # #64 aggregate quality denominators can include unavailable calls. The
        # artifact lacks enough attempt data to recompute them; do not attribute
        # availability failures to model quality. Keep latency/call measurements.
        metrics = {
            name: None
            if unavailable_cases and definition[1] is not None
            else record.metrics.get(name)
            for name, definition in METRICS.items()
            if name in record.metrics
        }
        observations = metric_observations(
            source, record.ended_at, "aggregate", "evaluation", metrics, **identity
        )
        for case in record.cases:
            unavailable = bool(case.failure_codes) and all(
                c in ("provider_error", "timeout", "call_budget_exceeded")
                for c in case.failure_codes
            )
            observations.append(
                observation(
                    source,
                    record.ended_at,
                    case.case_id,
                    "evaluation",
                    "case_pass",
                    None if unavailable else int(case.passed),
                    expected=1,
                    category="model_role",
                    role=case.role,
                    **identity,
                )
            )
            if unavailable:
                observations.append(
                    observation(
                        source,
                        record.ended_at,
                        case.case_id,
                        "provider",
                        "provider_success",
                        0,
                        expected=1,
                        failure_code="provider_unavailable",
                        role=case.role,
                        **identity,
                    )
                )
            if case.latency_ms_max is not None:
                observations.append(
                    observation(
                        source,
                        record.ended_at,
                        case.case_id,
                        "evaluation",
                        "latency_ms",
                        case.latency_ms_max,
                        direction="lower",
                        category="efficiency",
                        role=case.role,
                        **identity,
                    )
                )
        return source, tuple(observations)

    def qualification(self, record):
        gates = {
            role: [(g.key, g.metric, g.direction, g.threshold, g.mandatory) for g in r.gates]
            for role, r in record.roles.items()
        }
        cases = tuple(
            sorted({c for r in record.roles.values() for c in r.evidence.get("cases", [])})
        )
        suite = record.evaluation_suite_digest
        # PR #66 uses a shortened digest: preserve it by hashing its exact value.
        source = self.base(
            record,
            schema_version=record.schema_version,
            repo_sha=record.repo_sha,
            suite_version=record.evaluation_suite_version,
            suite_digest=digest(suite),
            policy_version=record.qualification_policy_version,
            policy_digest=digest(gates),
            case_ids=cases,
            configuration_digest=digest(
                {k: record.run.get(k) for k in ("bounds", "cases", "repetitions", "allow_repair")}
            ),
            complete=bool(cases)
            and record.repo_sha != "unknown"
            and record.status == "evaluated"
            and set(record.run) >= {"bounds", "cases", "repetitions", "allow_repair"}
            and isinstance(record.run.get("bounds"), dict)
            and bool(record.run.get("bounds"))
            and bool(record.run.get("cases"))
            and len(suite) == 16
            and all(c in "0123456789abcdef" for c in suite)
            and all(
                set(r.evidence.get("evaluated_cases", [])) == set(r.evidence.get("cases", []))
                and r.role == key
                for key, r in record.roles.items()
            ),
        )
        identity = dict(
            model=record.model, provider=record.provider, inference_mode=record.inference_mode
        )
        observations = []
        for role, role_record in sorted(record.roles.items()):
            observations.extend(
                metric_observations(
                    source,
                    record.evaluated_at,
                    role,
                    "qualification",
                    role_record.evidence.get("metrics", {})
                    | role_record.evidence.get("operational", {}),
                    enforce_expectations=False,
                    role=role,
                    **identity,
                )
            )
            for gate in role_record.gates:
                direction = "higher" if gate.direction in ("min", ">=", "higher") else "lower"
                observations.append(
                    observation(
                        source,
                        record.evaluated_at,
                        role,
                        "qualification",
                        "gate:" + gate.key,
                        gate.observed,
                        expected=gate.threshold,
                        direction=direction,
                        category="model_role",
                        role=role,
                        hard_gate=gate.mandatory,
                        **identity,
                    )
                )
            call_failures = role_record.evidence.get("failure_codes", {})
            if any(
                call_failures.get(code, 0) > 0
                for code in ("provider_error", "timeout", "call_budget_exceeded")
            ):
                observations.append(
                    observation(
                        source,
                        record.evaluated_at,
                        role,
                        "provider",
                        "provider_success",
                        0,
                        expected=1,
                        failure_code="provider_unavailable",
                        role=role,
                        **identity,
                    )
                )
        return source, tuple(observations)

    def doctor(self):
        # Doctor is a dataclass artifact. Validate the complete bounded projection,
        # discard reasons, remediation, endpoints and identifiers entirely.
        payload = self.payload
        checks = payload.get("checks", [])
        if payload.get("schemaVersion") != 1 or not isinstance(checks, list) or len(checks) > 128:
            raise ValueError("unsupported/unbounded doctor artifact")
        timestamp = datetime.fromisoformat(payload["generatedAt"].replace("Z", "+00:00"))
        source = self.base(
            payload,
            schema_version="1",
            repo_sha=payload.get("gitSha") or "unknown",
            case_ids=tuple(sorted(c["name"] for c in checks)),
        )
        observations = []
        for check in checks:
            status = check["status"]
            if status not in ("healthy", "degraded", "blocked", "disabled", "unknown"):
                raise ValueError("unknown doctor status")
            actual = None if status in ("disabled", "unknown") else int(status == "healthy")
            observations.append(
                observation(
                    source,
                    timestamp,
                    check["name"],
                    check["group"],
                    "doctor_health",
                    actual,
                    expected=1,
                    category="system_runtime",
                    failure_code=status if actual == 0 else None,
                )
            )
        return source, tuple(observations)
