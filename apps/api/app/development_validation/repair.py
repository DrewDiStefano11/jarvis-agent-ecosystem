"""Authenticated failure reproduction and bounded advisory retry decisions."""

from itertools import islice

from app.development_validation.evidence import assess_receipts, verify_plan
from app.development_validation.planner import digest
from app.models.development_validation import CodeState
from app.models.validation_execution import SignedReceipt
from app.models.validation_repair import RepairEvidence, RepairFinding, ReproductionReference

TRANSIENT_KINDS = frozenset({"infrastructure", "ci_environment", "dependency", "watchdog"})
TRANSIENT_CODES = frozenset({"dependency_unavailable", "timeout"})


def build_repair_evidence(
    current_state,
    plan,
    receipts,
    verifier,
    *,
    now,
    expected_execution_plan_hash,
    retry_budget=2,
    max_age_seconds=3600,
):
    if type(retry_budget) is not int or not 0 <= retry_budget <= 2:
        raise ValueError("retry budget must be operator-bounded to at most two")
    plan = verify_plan(plan)
    current_state = CodeState.model_validate(current_state.model_dump(mode="json"))
    receipts = tuple(islice(receipts, 513))
    # Assessment rejects unauthenticated, replayed, wrong-plan or future receipts.
    assessment = assess_receipts(
        plan.code_state,
        plan,
        receipts,
        verifier,
        now=now,
        max_age_seconds=max_age_seconds,
        expected_execution_plan_hash=expected_execution_plan_hash,
    )
    if assessment.blocked_paths:
        raise ValueError("repair evidence cannot hide blocked repository policy")
    source_changed = (
        digest(current_state.model_dump(mode="json")) != plan.validation.code_state_hash
    )
    measurements = tuple(
        (SignedReceipt.model_validate(r.model_dump(mode="json")), verifier.verify(r))
        for r in receipts
    )
    findings = []
    for command in plan.commands:
        candidates = [(r, m) for r, m in measurements if m.command == command]
        remaining = retry_budget
        if not candidates:
            findings.append(
                RepairFinding(
                    gate=command.gate,
                    disposition="stale" if source_changed else "missing",
                    recommendation="revalidate" if source_changed else "gather_evidence",
                    retries_remaining=remaining,
                )
            )
            continue
        lineages = {}
        for signed, measured in candidates:
            lineage = (measured.runtime_run_id, measured.attempt_id)
            previous = lineages.get(lineage)
            if previous is None or measured.sequence > previous[1].sequence:
                lineages[lineage] = (signed, measured)
        failures = sum(m.outcome == "failed" for _, m in lineages.values())
        remaining = max(0, retry_budget - max(0, failures - 1))
        latest = max(m.finished_at for _, m in lineages.values())
        tied = [(r, m) for r, m in lineages.values() if m.finished_at == latest]
        # Failure wins tied outcomes; interruption/unknown wins a tied pass.
        priority = {"failed": 0, "interrupted": 1, "cancelled": 2, "unmeasured": 3, "passed": 4}
        r, m = min(
            tied,
            key=lambda pair: (priority[pair[1].outcome], digest(pair[1].model_dump(mode="json"))),
        )
        disposition = (
            "stale"
            if source_changed or (now - m.finished_at).total_seconds() > max_age_seconds
            else m.outcome
        )
        reference = ReproductionReference(
            repository_identity=plan.code_state.repository_identity,
            head_sha=plan.code_state.head_sha,
            base_sha=plan.code_state.base_sha,
            code_state_hash=m.code_state_hash,
            execution_plan_hash=m.execution_plan_hash,
            validation_plan_hash=m.validation_plan_hash,
            suite_digest=m.command.suite_digest,
            command_policy_fingerprint=m.command.policy_fingerprint,
            task_id=m.task_id,
            runtime_run_id=m.runtime_run_id,
            attempt_id=m.attempt_id,
            command_run_id=m.command_run_id,
            receipt_digest=digest(r.model_dump(mode="json")),
            stdout_digest=m.stdout_digest,
            stderr_digest=m.stderr_digest,
        )
        recommendation = (
            "none"
            if disposition == "passed"
            else "revalidate"
            if disposition == "stale"
            else "gather_evidence"
        )
        if disposition == "failed":
            transient = all(
                item.failure_kind in TRANSIENT_KINDS
                and bool(set(item.diagnostic_codes) & TRANSIENT_CODES)
                for _, item in tied
                if item.outcome == "failed"
            )
            recommendation = (
                "request_bounded_retry"
                if transient and remaining
                else "stop_retrying"
                if transient
                else "diagnose_failure"
            )
        findings.append(
            RepairFinding(
                gate=command.gate,
                disposition=disposition,
                failure_kind=m.failure_kind if disposition == "failed" else None,
                reproduction=reference,
                supporting_receipt_digests=tuple(
                    sorted(digest(item.model_dump(mode="json")) for item, _ in tied)
                ),
                recommendation=recommendation,
                retries_remaining=remaining,
            )
        )
    payload = dict(
        schema_version="1.0",
        execution_plan_hash=plan.execution_plan_hash,
        code_state_hash=plan.validation.code_state_hash,
        observed_at=now.isoformat(),
        source_changed=source_changed,
        findings=[f.model_dump(mode="json") for f in findings],
        execution_authorized=False,
        source_mutation_authorized=False,
    )
    provisional = RepairEvidence(**payload, report_digest="0" * 64)
    return RepairEvidence.model_validate(
        {
            **provisional.model_dump(mode="json"),
            "report_digest": digest(provisional.model_dump(mode="json", exclude={"report_digest"})),
        }
    )
