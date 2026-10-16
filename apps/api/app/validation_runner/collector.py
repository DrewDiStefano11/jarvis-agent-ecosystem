"""Outside-candidate output capture with quarantined, nonqualifying receipts.

There is no production isolated telemetry adapter yet. This collector cannot
assert an exit, classify failure, or produce passing evidence. Captured output
is hashed and discarded; neither guest JSON nor stdout is treated as authority.
"""

import hmac
import json
from hashlib import sha256
from itertools import islice

from app.core.errors import DomainError
from app.models.validation_execution import ExecutionMeasurement, SignedReceipt


class QuarantinedEvidenceCollector:
    def __init__(self, *, collector_id, key_id, key_provider, authority_check):
        # Operator-owned service configuration; never provided by candidate tools.
        self.collector_id = collector_id
        self.key_id = key_id
        self._key_provider = key_provider
        self._guard = authority_check

    def capture(
        self,
        *,
        plan,
        owner,
        command,
        command_run_id,
        started_at,
        finished_at,
        stdout,
        stderr,
        capsule,
        journal_digest,
        native_inventory_digest,
        sequence=1,
    ):
        from app.development_validation.evidence import verify_plan
        from app.models.validation_execution import ResourceMeasurements

        plan = verify_plan(plan)
        from app.validation_runner.isolation import verify_capsule

        verify_capsule(
            capsule,
            plan,
            owner=owner,
            journal_digest=journal_digest,
            native_inventory_digest=native_inventory_digest,
        )
        if command not in plan.commands:
            raise ValueError("COLLECTOR_COMMAND_MISMATCH")
        self._guard()
        retained, truncated = 0, False

        def output_hash(chunks):
            nonlocal retained, truncated
            result = sha256()
            for index, chunk in enumerate(islice(chunks, 4097)):
                self._guard()
                if index == 4096 or type(chunk) is not bytes or len(chunk) > 65536:
                    raise ValueError("COLLECTOR_CHUNK_LIMIT")
                take = min(len(chunk), command.output_limit_bytes - retained)
                result.update(chunk[:take])
                retained += take
                truncated |= take != len(chunk)
                if truncated:
                    break
            else:
                return result.hexdigest()
            return result.hexdigest()

        stdout_digest, stderr_digest = output_hash(stdout), output_hash(stderr)
        measurement = ExecutionMeasurement(
            execution_plan_hash=plan.execution_plan_hash,
            validation_plan_hash=plan.validation.plan_hash,
            repository_identity=plan.code_state.repository_identity,
            code_state_hash=plan.validation.code_state_hash,
            command=command,
            task_id=owner.task_id,
            runtime_run_id=owner.runtime_run_id,
            attempt_id=owner.attempt_id,
            command_run_id=command_run_id,
            sequence=sequence,
            started_at=started_at,
            finished_at=finished_at,
            outcome="unmeasured",
            resources=ResourceMeasurements(
                wall_time_ms=round((finished_at - started_at).total_seconds() * 1000)
            ),
            stdout_digest=stdout_digest,
            stderr_digest=stderr_digest,
            output_bytes=retained,
            output_truncated=truncated,
            diagnostic_codes=("output_truncated",) if truncated else ("unknown",),
        )
        provisional = SignedReceipt(
            collector_id=self.collector_id,
            key_id=self.key_id,
            measurement=measurement,
            authentication_tag="0" * 64,
        )
        self._guard()
        try:
            key = self._key_provider()
            if type(key) is not bytes or not 32 <= len(key) <= 64:
                raise ValueError("invalid key")
        except Exception:
            raise DomainError(
                "COLLECTOR_KEY_UNAVAILABLE", "Collector credential unavailable.", 503
            ) from None
        payload = provisional.model_dump(mode="json", exclude={"authentication_tag"})
        tag = hmac.new(
            key,
            b"jarvis-validation-receipt-v1\0"
            + json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(),
            sha256,
        ).hexdigest()
        self._guard()
        return provisional.model_copy(update={"authentication_tag": tag})
