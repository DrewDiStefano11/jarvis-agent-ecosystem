"""Validation projections into the existing evidence/diagnosis engine."""

from itertools import islice

from app.development_validation.planner import POLICY_VERSION, digest
from app.development_validation.repair import build_repair_evidence
from app.models.development_validation import CodeState
from app.models.self_improvement import SourceProvenance
from app.self_improvement.adapters import observation


class ValidationExecutionSource:
    """Trusted host adapter; approval reference must come from outside candidate input.

    Neither a receipt nor model-selected plan can supply its own approval hash.
    This is an internal advisory projection, not an HTTP authority endpoint.
    """

    def __init__(
        self,
        source_id,
        current_state,
        plan,
        receipts,
        verifier,
        *,
        now,
        expected_execution_plan_hash,
    ):
        self.source_id = source_id
        self.state, self.plan, self.receipts = current_state, plan, tuple(islice(receipts, 513))
        self.verifier, self.now = verifier, now
        self.expected_execution_plan_hash = expected_execution_plan_hash

    def collect(self):
        current_state = CodeState.model_validate(self.state.model_dump(mode="json"))
        report = build_repair_evidence(
            current_state,
            self.plan,
            self.receipts,
            self.verifier,
            now=self.now,
            expected_execution_plan_hash=self.expected_execution_plan_hash,
        )
        source = SourceProvenance(
            source_type="validation_execution",
            source_id=self.source_id,
            digest=digest(
                {
                    "plan": self.plan.execution_plan_hash,
                    "state": current_state.model_dump(mode="json"),
                    "receipts": sorted(
                        digest(self.verifier.verify(r).model_dump(mode="json"))
                        for r in self.receipts
                    ),
                    "dispositions": [(f.gate, f.disposition) for f in report.findings],
                }
            ),
            schema_version="validation-repair-evidence-1",
            repo_sha=self.plan.code_state.head_sha,
            suite_version="validation-gates-1",
            suite_digest=digest([c.suite_digest for c in self.plan.commands]),
            policy_version=POLICY_VERSION,
            policy_digest=self.plan.validation.policy_digest,
            case_ids=tuple(f.gate for f in report.findings),
            configuration_digest=None,
            # Actual tool/environment/sandbox provenance is not measured by this adapter.
            complete=False,
        )
        facts = tuple(
            observation(
                source,
                self.now,
                f.gate,
                "validation",
                "gate_success:" + f.gate,
                1 if f.disposition == "passed" else 0 if f.disposition == "failed" else None,
                expected=1,
                category="reliability"
                if f.recommendation in {"request_bounded_retry", "stop_retrying"}
                else "execution",
                role="validation_gate",
                inference_mode="runtime",
                failure_code=f.failure_kind if f.disposition == "failed" else None,
                hard_gate=True,
                task_id=f.reproduction.task_id if f.reproduction else None,
                context_id=report.code_state_hash,
            )
            for f in report.findings
        )
        return source, facts
