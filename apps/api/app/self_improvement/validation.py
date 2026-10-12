"""Validation projections into the existing evidence/diagnosis engine."""

from itertools import islice

from app.development_validation.planner import digest
from app.development_validation.repair import build_repair_evidence
from app.models.self_improvement import SourceProvenance
from app.self_improvement.adapters import observation


class ValidationExecutionSource:
    def __init__(self, source_id, current_state, plan, receipts, verifier, *, now):
        self.source_id = source_id
        self.state, self.plan, self.receipts = current_state, plan, tuple(islice(receipts, 513))
        self.verifier, self.now = verifier, now

    def collect(self):
        report = build_repair_evidence(
            self.state, self.plan, self.receipts, self.verifier, now=self.now
        )
        source = SourceProvenance(
            source_type="validation_execution",
            source_id=self.source_id,
            digest=digest(
                {
                    "plan": self.plan.execution_plan_hash,
                    "state": self.state.model_dump(mode="json"),
                    "receipts": sorted(
                        digest(r.measurement.model_dump(mode="json")) for r in self.receipts
                    ),
                    "dispositions": [(f.gate, f.disposition) for f in report.findings],
                }
            ),
            schema_version="validation-repair-evidence-1",
            repo_sha=self.plan.code_state.head_sha,
            suite_version="validation-gates-1",
            suite_digest=digest([c.suite_digest for c in self.plan.commands]),
            policy_version="validation-collector-1",
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
