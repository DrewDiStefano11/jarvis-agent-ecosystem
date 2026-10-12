"""Fail-closed bridge from trusted collector receipts to the deterministic planner.

Keys must belong to an operator-controlled collector outside candidate workspaces.
Signing authenticates collector provenance, not sandboxing or collector correctness.
No API/model import, key generation, subprocess or runner is provided here.
"""

import hmac
import json
from collections.abc import Mapping
from hashlib import sha256
from itertools import islice

from app.development_validation.planner import assess_validation, digest, plan_validation
from app.models.development_validation import ValidationAssessment, ValidationEvidence
from app.models.validation_execution import ExecutionPlan, SignedReceipt


def verify_plan(plan):
    plan = ExecutionPlan.model_validate(plan.model_dump(mode="json"))
    expected = plan_validation(
        plan.code_state,
        boundary=plan.validation.boundary,
        **plan.validation.derivation.model_dump(),
    )
    if plan.validation != expected:
        raise ValueError("execution plan does not match deterministic policy")
    if tuple(c.gate for c in plan.commands) != tuple(g.gate for g in expected.gates):
        raise ValueError("execution commands must exactly cover ordered validation gates")
    if (
        digest(plan.model_dump(mode="json", exclude={"execution_plan_hash"}))
        != plan.execution_plan_hash
    ):
        raise ValueError("execution plan digest mismatch")
    if plan != build_execution_plan(
        plan.code_state,
        boundary=plan.validation.boundary,
        **plan.validation.derivation.model_dump(),
    ):
        raise ValueError("execution commands differ from packaged policy")
    return plan


class ReceiptVerifier:
    """Inject trusted (collector_id, key_id) keys; never accept keys with evidence.

    A configured collector must independently capture state and measurements. The
    caller owns key distribution/revocation. There is deliberately no default key.
    """

    def __init__(self, keys: Mapping[tuple[str, str], bytes]):
        if any(type(key) is not bytes or len(key) < 32 for key in keys.values()):
            raise ValueError("collector keys require at least 256 bits")
        self._keys = dict(keys)

    def verify(self, receipt):
        # Revalidation rejects mutated model_copy payloads before authentication.
        receipt = SignedReceipt.model_validate(receipt.model_dump(mode="json"))
        key = self._keys.get((receipt.collector_id, receipt.key_id))
        if key is None:
            raise ValueError("untrusted validation collector")
        payload = receipt.model_dump(mode="json", exclude={"authentication_tag"})
        tag = hmac.new(
            key,
            b"jarvis-validation-receipt-v1\0"
            + json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(),
            sha256,
        ).hexdigest()
        if not hmac.compare_digest(tag, receipt.authentication_tag):
            raise ValueError("unauthenticated validation receipt")
        return receipt.measurement


def assess_receipts(state, plan, receipts, verifier, *, now, max_age_seconds=3600):
    """Exact-state assessment only; invalid provenance fails the entire assessment.

    Caller supplies trusted clock and collector configuration. Expired receipts
    project as unmeasured; interruption/cancellation never become a successful exit.
    Replay and conflicting command identities are rejected before planner selection.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("assessment requires a trusted aware clock")
    if type(max_age_seconds) is not int or not 1 <= max_age_seconds <= 86400:
        raise ValueError("invalid evidence lifetime")
    plan = verify_plan(plan)
    receipts = tuple(islice(receipts, 513))
    if len(receipts) > 512:
        raise ValueError("validation receipt bound exceeded")
    projected, seen, sequences, latest = [], set(), set(), {}
    expired_gates, fresh_gates = set(), set()
    commands = {c.gate: c for c in plan.commands}
    requirements = {g.gate: g for g in plan.validation.gates}
    for receipt in receipts:
        m = verifier.verify(receipt)
        identity = (m.runtime_run_id, m.attempt_id, m.command_run_id)
        sequence = (m.runtime_run_id, m.attempt_id, m.sequence)
        if identity in seen or sequence in sequences:
            raise ValueError("duplicate execution receipt identity or sequence")
        seen.add(identity)
        sequences.add(sequence)
        if (
            m.execution_plan_hash != plan.execution_plan_hash
            or m.validation_plan_hash != plan.validation.plan_hash
            or m.repository_identity != plan.code_state.repository_identity
            or m.code_state_hash != plan.validation.code_state_hash
            or m.command != commands.get(m.command.gate)
        ):
            raise ValueError("receipt does not belong to approved execution plan")
        age = (now - m.finished_at).total_seconds()
        if age < 0:
            raise ValueError("receipt timestamp is in the future")
        # Authenticate and validate every receipt before selection. Within a
        # run/attempt/gate, collector sequence owns order even if the clock
        # regresses; expiration must never revive an earlier passing receipt.
        lineage = (m.runtime_run_id, m.attempt_id, m.command.gate)
        previous = latest.get(lineage)
        if previous is None or m.sequence > previous.sequence:
            latest[lineage] = m
    for m in latest.values():
        age = (now - m.finished_at).total_seconds()
        outcome = m.outcome if m.outcome in {"passed", "failed", "cancelled"} else "unmeasured"
        if age > max_age_seconds:
            expired_gates.add(m.command.gate)
            outcome = "unmeasured"
        else:
            fresh_gates.add(m.command.gate)
        gate = requirements[m.command.gate]
        projected.append(
            ValidationEvidence(
                command_run_id=m.command_run_id,
                gate=gate.gate,
                policy_digest=plan.validation.policy_digest,
                code_state_hash=m.code_state_hash,
                scope_fingerprint=gate.scope_fingerprint,
                targets=gate.targets,
                outcome=outcome,
                finished_at=m.finished_at,
                failure_kind=m.failure_kind if outcome == "failed" else None,
            )
        )
    # A changed source state must first obtain a new immutable execution plan.
    assessment = assess_validation(
        state, plan.validation, projected, **plan.validation.derivation.model_dump()
    )
    expired = expired_gates - fresh_gates
    return ValidationAssessment(
        ready=assessment.ready,
        missing=tuple(g for g in assessment.missing if g not in expired),
        failed=assessment.failed,
        stale=tuple(g.gate for g in plan.validation.gates if g.gate in expired),
        blocked_paths=assessment.blocked_paths,
    )


def build_execution_plan(state, **derivation):
    """Packaged adapter identities and budgets; candidate plans cannot change them.

    Adapter identifiers are contracts, not claims of implemented capabilities.
    All executable adapters remain unavailable until verified isolation is added.
    """
    from pathlib import Path

    from app.models import validation_execution, validation_runner
    from app.models.validation_execution import CommandDefinition
    from app.validation_runner import catalog

    validation = plan_validation(state, **derivation)
    policy = digest(
        {
            "version": "jarvis-validation-collector-v1",
            "implementation": sha256(
                Path(__file__).read_text(encoding="utf-8").encode()
                + Path(validation_execution.__file__).read_text(encoding="utf-8").encode()
                + Path(catalog.__file__).read_text(encoding="utf-8").encode()
                + Path(validation_runner.__file__).read_text(encoding="utf-8").encode()
            ).hexdigest(),
        }
    )
    commands = tuple(
        CommandDefinition(
            gate=g.gate,
            adapter=g.gate + ":v1",
            suite_digest=digest({"gate": g.gate, "targets": g.targets, "policy": policy}),
            policy_fingerprint=policy,
            timeout_seconds=3600 if g.gate == "backend_tests" else 900,
            output_limit_bytes=65536,
            memory_limit_bytes=2147483648,
        )
        for g in validation.gates
    )
    payload = dict(
        schema_version="1.0",
        code_state=state.model_dump(mode="json"),
        validation=validation.model_dump(mode="json"),
        commands=[c.model_dump(mode="json") for c in commands],
    )
    return ExecutionPlan(**payload, execution_plan_hash=digest(payload))
