"""Pure deterministic analysis and conservative per-metric comparison."""

import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from app.models.self_improvement import (
    Analysis,
    Baseline,
    CandidateAttestation,
    Comparison,
    Criterion,
    ExperimentPlan,
    ImprovementHypothesis,
    Proposal,
    Weakness,
)


def digest(value) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def failed(o) -> bool:
    if not o.measured or o.expected is None:
        return False
    return o.actual < o.expected if o.direction == "higher" else o.actual > o.expected


def validate_hypothesis(hypothesis: ImprovementHypothesis, analysis: Analysis):
    weaknesses = {w.id: w for w in analysis.weaknesses}
    weakness = weaknesses.get(hypothesis.weakness_id)
    if weakness is None:
        raise ValueError("unknown weakness")
    available = {o.id for o in analysis.baseline.observations}
    referenced = set(hypothesis.supporting_evidence_ids + hypothesis.contradicting_evidence_ids)
    if not referenced <= available or not set(hypothesis.supporting_evidence_ids) <= set(
        weakness.evidence_ids
    ):
        raise ValueError("hypothesis evidence is nonexistent or does not support this weakness")
    if set(hypothesis.supporting_evidence_ids) & set(hypothesis.contradicting_evidence_ids):
        raise ValueError("supporting and contradicting evidence overlap")
    # Advisory text never alters deterministic findings, metrics or proposal criteria.
    return hypothesis


def analyze(baseline: Baseline) -> Analysis:
    groups = defaultdict(list)
    for o in baseline.observations:
        if failed(o):
            groups[(o.category, o.stage, o.role, o.model, o.provider, o.metric)].append(o)
    if len(groups) > 256:
        raise ValueError("too many weakness groups; reduce evidence window")
    complete = all(s.complete and s.repo_sha == baseline.repo_sha for s in baseline.sources)
    measured = tuple(o for o in baseline.observations if o.measured)
    if (
        complete
        and len(groups) * len(measured) + sum(len(items) for items in groups.values()) > 65_536
    ):
        raise ValueError("experiment criteria exceed output budget; reduce evidence window")
    # Share immutable criteria in memory; bound serialized copies across plans.
    regression = (
        tuple(
            Criterion(observation_id=o.id, target=o.actual, direction=o.direction) for o in measured
        )
        if groups and complete
        else ()
    )
    weaknesses, proposals = [], []
    for key, items in sorted(groups.items()):
        category, stage, role, _, _, metric = key
        evidence = tuple(sorted(o.id for o in items))
        subjects = len({(o.source_id, o.subject_id) for o in items})
        hard = any(o.hard_gate for o in items)
        severity = (
            "critical"
            if hard
            else "low"
            if category == "efficiency"
            else "high"
            if subjects >= 2
            else "medium"
        )
        weakness = Weakness(
            id=digest([baseline.id, key, evidence]),
            category=category,
            stage=stage,
            role=role,
            evidence_ids=evidence,
            frequency=len(items),
            affected_subjects=subjects,
            severity=severity,
            confidence="high" if complete and subjects >= 2 else "medium" if complete else "low",
            actionable=complete,
            repeated=subjects >= 2,
            reason=f"{len(items)} measured failures of {metric}; {subjects} distinct source/subjects; hard gate={hard}.",
        )
        weaknesses.append(weakness)
        mapping = {
            "reliability": "retry_policy",
            "model_role": "schema_validation",
            "planning": "decomposer_configuration",
            "execution": "test_coverage",
            "system_runtime": "runtime_configuration",
            "efficiency": "task_bounds",
        }
        candidates = {
            "reliability": "Make retries conditional on recorded transient failure codes; terminate permanent validation failures within the existing retry/call budget.",
            "model_role": "Add the recorded failing schema/gate requirements to this role's output contract prompt, with one valid format example; retain the existing validator and model parameters.",
            "planning": "Require a capability coverage and acyclic dependency checklist in the decomposer output contract; reject unsupported identifiers with the existing validator.",
            "execution": "Add a regression case reproducing the cited execution failure, then validate an isolated handler correction against the original unchanged acceptance suite.",
            "system_runtime": "Add an explicit bounded startup preflight for the cited unhealthy dependency; retain emergency stop and existing provider selection.",
            "efficiency": "Terminate repeated identical failed model requests within existing bounds rather than repeating an unchanged request.",
        }
        description = f"Target {stage}/{role}, measurement {metric}: {candidates[category]}"
        # Select per-source metric keys; repeated runs remain individually visible.
        primary = tuple(
            Criterion(observation_id=o.id, target=o.expected, direction=o.direction) for o in items
        )
        experiment = (
            ExperimentPlan(
                baseline_id=baseline.id,
                candidate_description=description,
                evaluation_sources=tuple(s.digest for s in baseline.sources),
                primary=primary,
                regression=regression,
            )
            if complete
            else None
        )
        proposals.append(
            Proposal(
                id=digest([weakness.id, "proposal-v1"]),
                baseline_id=baseline.id,
                weakness_id=weakness.id,
                evidence_ids=evidence,
                category=mapping[category],
                target_subsystem=stage,
                change_description=description,
                expected_effect=f"Bring every cited {metric} measurement to its recorded expectation; retain all baseline regression measurements.",
                risk="high" if hard else "medium",
                rollback="Restore the exact baseline code/configuration in a future operator-controlled sandbox; rerun the unchanged evaluation set.",
                priority=severity,
                priority_reasons=(
                    weakness.reason,
                    f"Evidence confidence={weakness.confidence}; implementation scope requires operator refinement; regression risk={'high' if hard else 'medium'}.",
                ),
                status="ready_for_review" if complete else "needs_evidence",
                experiment=experiment,
            )
        )
    return Analysis(baseline=baseline, weaknesses=tuple(weaknesses), proposals=tuple(proposals))


def create_baseline(
    *, repo_sha, configuration_fingerprint, safety_fingerprint, sources, observations
):
    sources = tuple(sorted(sources, key=lambda s: (s.source_type, s.source_id)))
    observations = tuple(sorted(observations, key=lambda o: o.id))
    known = {o.metric for o in observations if o.measured}
    expected = {
        "scenario_pass",
        "task_completion",
        "retry_count",
        "retry_success",
        "malformed_response_rate",
        "schema_validity_rate",
        "hallucination_rate",
        "capability_classification_accuracy",
        "decomposition_quality",
        "synthesis_completeness",
        "reviewer_pass",
        "runtime_success",
        "recovery_count",
        "latency_ms",
        "provider_success",
    }
    payload = dict(
        repo_sha=repo_sha,
        configuration_fingerprint=configuration_fingerprint,
        safety_fingerprint=safety_fingerprint,
        evaluator_digest=digest(
            {
                str(p.name): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (
                    Path(__file__),
                    Path(__file__).with_name("adapters.py"),
                    Path(__file__).with_name("runtime.py"),
                    Path(__file__).parents[1] / "models" / "self_improvement.py",
                    Path(__file__).parents[1] / "model_evaluation" / "runner.py",
                    Path(__file__).parents[1] / "model_evaluation" / "expectations.py",
                    Path(__file__).parents[1] / "model_qualification" / "metrics.py",
                    Path(__file__).parents[1] / "model_qualification" / "policy.py",
                    Path(__file__).parents[1] / "model_qualification" / "scoring.py",
                    Path(__file__).parents[1] / "model_qualification" / "roles.py",
                    Path(__file__).parents[1] / "model_qualification" / "profile.py",
                    Path(__file__).parents[1] / "model_evaluation" / "cases.py",
                    Path(__file__).parents[1] / "model_evaluation" / "report.py",
                    Path(__file__).parents[1] / "model_providers" / "errors.py",
                )
            }
        ),
        sources=sources,
        observations=observations,
        missing_metrics=tuple(sorted(expected - known)),
    )
    stable = {
        k: [x.model_dump(mode="json") for x in v] if k in ("sources", "observations") else v
        for k, v in payload.items()
    }
    return Baseline(id=digest(stable), created_at=datetime.now(UTC), **payload)


def metric_key(o):
    # Source ID is a stable operator alias across runs, not the artifact digest.
    return (o.source_type, o.source_id, o.subject_id, o.stage, o.role, o.metric, o.inference_mode)


def compare_baselines(
    before: Baseline, after: Baseline, proposal: Proposal, attestation: CandidateAttestation
) -> Comparison:
    plan = proposal.experiment
    reasons = []
    if plan is None or plan.baseline_id != before.id or proposal.baseline_id != before.id:
        reasons.append(
            "Missing experiment or wrong baseline; criteria must come from persisted proposal."
        )
    if before.evaluator_version != after.evaluator_version:
        reasons.append("Evaluator version changed.")
    if before.evaluator_digest != after.evaluator_digest:
        reasons.append("Evaluator implementation digest changed; direct comparison invalid.")
    if before.safety_fingerprint != after.safety_fingerprint:
        reasons.append(
            "Safety configuration changed: limits, permissions or routing may have changed."
        )
    if (
        after.repo_sha != attestation.candidate_repo_sha
        or after.configuration_fingerprint != attestation.candidate_configuration_fingerprint
    ):
        reasons.append("Candidate identity does not match operator attestation.")
    old_sources = {(s.source_type, s.source_id): s for s in before.sources}
    new_sources = {(s.source_type, s.source_id): s for s in after.sources}
    if old_sources.keys() != new_sources.keys():
        reasons.append("Evidence sources were added or removed.")
    for key, old in old_sources.items():
        new = new_sources.get(key)
        if new is None:
            continue
        if not old.complete or not new.complete:
            reasons.append(f"Incomplete provenance: {key}.")
        if old.repo_sha != before.repo_sha or new.repo_sha != after.repo_sha:
            reasons.append(f"Source repository identity does not match baseline: {key}.")
        for field in (
            "schema_version",
            "suite_version",
            "suite_digest",
            "policy_version",
            "policy_digest",
            "case_ids",
            "configuration_digest",
        ):
            if getattr(old, field) != getattr(new, field):
                reasons.append(f"Changed {field}: {key}; direct comparison invalid.")
    old_metrics = {metric_key(o): o for o in before.observations}
    new_metrics = {metric_key(o): o for o in after.observations}
    if len(old_metrics) != len(before.observations) or len(new_metrics) != len(after.observations):
        reasons.append("Ambiguous duplicate metric identities.")
    improved, regressed, unchanged, newly, missing = [], [], [], [], []
    for key in sorted(old_metrics.keys() | new_metrics.keys()):
        old, new = old_metrics.get(key), new_metrics.get(key)
        label = "/".join(key)
        if (
            old is not None
            and new is not None
            and (old.model, old.provider) != (new.model, new.provider)
        ):
            reasons.append(f"Changed model/provider identity: {label}; direct comparison invalid.")
            continue
        if (
            old is not None
            and new is not None
            and (old.direction, old.expected, old.hard_gate)
            != (new.direction, new.expected, new.hard_gate)
        ):
            reasons.append(f"Changed metric definition/threshold/safety gate: {label}.")
            continue
        if new is not None and old is not None and not old.measured and not new.measured:
            unchanged.append(label)
        elif new is None or not new.measured:
            missing.append(label)
        elif old is None or not old.measured:
            newly.append(label)
            if failed(new):
                regressed.append(label)
            elif new.hard_gate and new.expected is None:
                reasons.append(f"Newly measured hard gate has no recorded expectation: {label}.")
        elif new.actual == old.actual:
            unchanged.append(label)
        elif (new.actual > old.actual) == (old.direction == "higher"):
            improved.append(label)
        else:
            regressed.append(label)
    if missing:
        reasons.append("Measurements disappeared; evidence cannot be suppressed.")
    gates = [getattr(attestation, check) for check in plan.required_checks] if plan else []
    if not gates or any(g is None for g in gates):
        reasons.append("Required safety/test/migration evidence is unmeasured.")
    if len(reasons) > 64:
        reasons = reasons[:63] + [
            "Further incompatibilities omitted; comparison remains inconclusive."
        ]
    decision = "inconclusive"
    if not reasons and plan:
        old_ids = {o.id: o for o in before.observations}

        def passes(c):
            old = old_ids.get(c.observation_id)
            new = new_metrics.get(metric_key(old)) if old else None
            return (
                new is not None
                and new.measured
                and (new.actual >= c.target if c.direction == "higher" else new.actual <= c.target)
            )

        if (
            any(g is False for g in gates)
            or regressed
            or not all(passes(c) for c in plan.regression)
        ):
            decision = "regressed"
        elif all(passes(c) for c in plan.primary) and improved:
            decision = "improved"
        else:
            decision = "neutral"
    payload = dict(
        before_id=before.id,
        after_id=after.id,
        proposal_id=proposal.id,
        decision=decision,
        improved=tuple(improved),
        regressed=tuple(regressed),
        unchanged=tuple(unchanged),
        newly_measured=tuple(newly),
        missing=tuple(missing),
        reasons=tuple(reasons),
    )
    return Comparison(id=digest([payload, attestation.model_dump(mode="json")]), **payload)
