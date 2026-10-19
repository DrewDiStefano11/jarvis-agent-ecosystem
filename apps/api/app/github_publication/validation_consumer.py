"""Consume B's native journal; never collect, execute, sign or fabricate receipts."""

from datetime import UTC, datetime
from inspect import iscoroutine

from app.core.errors import DomainError


def fail(code):
    raise DomainError(code, "Genuine current Campaign B publication evidence is required.", 409)


def require_guard(guard, code):
    if not callable(guard):
        fail(code)
    result = guard()
    if result is not None:
        if iscoroutine(result):
            result.close()
        fail(code)


class PublicationValidationConsumer:
    def __init__(self, *, service=None, verified_boundary_guard=None):
        # Only trusted deployment composition may supply these B-owned adapters.
        # No API, environment or model payload can select a verifier or guard.
        self.service = service
        self.boundary_guard = verified_boundary_guard

    def require(self, actor, plan):
        if self.service is None or not callable(self.boundary_guard):
            fail("GITHUB_VALIDATION_ISOLATION_UNAVAILABLE")
        require_guard(self.boundary_guard, "GITHUB_VALIDATION_ISOLATION_UNAVAILABLE")
        snapshot = self.service.read(actor, plan.workspace_id, plan.validation_journal_id)
        execution = snapshot.intent.plan
        owner = snapshot.intent.owner
        state = execution.code_state
        if (
            snapshot.state != "finalized"
            or snapshot.digest != plan.validation_journal_digest
            or execution.validation.boundary != "publication"
            or execution.execution_plan_hash != plan.validation_execution_plan_hash
            or execution.validation.code_state_hash != plan.validation_code_state_hash
            or state.head_sha != plan.commit_sha
            or state.base_sha != plan.validation_base_sha
            or state.repository_identity != plan.repository_identity
            or owner.workspace_id != plan.workspace_id
            or owner.task_id != plan.task_id
            or owner.runtime_run_id != plan.runtime_run_id
            or owner.attempt_id != plan.attempt_id
        ):
            fail("GITHUB_VALIDATION_SCOPE_CHANGED")
        # B freshly resolves independent approval, native final checkpoint,
        # current source/policy/freshness and authenticated measured receipts.
        result = self.service.assess(
            actor, plan.workspace_id, plan.validation_journal_id, now=datetime.now(UTC)
        )
        require_guard(self.boundary_guard, "GITHUB_VALIDATION_ISOLATION_UNAVAILABLE")
        if result.ready is not True:
            fail("GITHUB_VALIDATION_PENDING")
        return snapshot
