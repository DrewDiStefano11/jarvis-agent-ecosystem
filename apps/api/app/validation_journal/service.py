"""Fenced validation journal over existing audit/outbox and runtime checkpoints.

This service has no runner and is not exposed on an HTTP or model-tool route.
A production trusted state reader/collector is deliberately not configured.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.agent_runtime.authorization import RuntimeActorContext
from app.agent_runtime.repository import RuntimeExecutionFence
from app.core.errors import DomainError
from app.db.models import (
    AgentRuntimeAttemptRow,
    AuditEventRow,
    DevelopmentWorkspaceRow,
    SystemStateRow,
)
from app.development_validation.evidence import assess_receipts, build_execution_plan
from app.development_validation.planner import digest
from app.models.agent_runtime import AgentRunAttempt, RecordCheckpointCommand
from app.models.validation_journal import (
    JournalApproval,
    JournalEntry,
    JournalIntent,
    JournalOwner,
)
from app.validation_journal.reducer import ZERO_DIGEST, fold


def fail(code, message, status=409):
    raise DomainError(code, message, status)


class ValidationJournalService:
    def __init__(self, app, *, state_reader, receipt_verifier):
        self.app = app
        self.workspace = app.state.self_build_workspace_service
        self.repository = self.workspace.repository
        self.runtime = app.state.agent_runtime_service
        # Trusted host adapters, never values selected by model or request payload.
        self.state_reader = state_reader
        self.verifier = receipt_verifier

    def _context(self, actor, workspace_id, session, *, write):
        row = session.get(DevelopmentWorkspaceRow, workspace_id)
        if row is None:
            fail("VALIDATION_WORKSPACE_NOT_FOUND", "Workspace was not found.", 404)
        self.workspace.authorize(actor, row.task_id, session, write=write)
        if row.state != "reserved":
            fail("VALIDATION_OWNER_INACTIVE", "Workspace ownership requires recovery.")
        policy = self.workspace.policies.get(row.repository_id)
        reason = self.repository.reason(session, row, digest(policy.model_dump()))
        if reason is not None:
            fail("VALIDATION_OWNER_INACTIVE", "Workspace ownership requires recovery.")
        self.repository.contract(row)
        run = self.repository.runtime(session, row.runtime_run_id)
        if run.state != "running" or not run.active_attempt_id:
            fail("VALIDATION_ATTEMPT_REQUIRED", "A running owned attempt is required.")
        if self.repository.executor(session, run) != row.worker_id:
            fail("VALIDATION_EXECUTOR_MISMATCH", "Workspace executor differs from runtime.")
        attempt_row = session.get(AgentRuntimeAttemptRow, (run.active_attempt_id, run.run_id))
        try:
            attempt = AgentRunAttempt.model_validate_json(attempt_row.contract_json)
            active = (
                attempt.state.value == "running"
                and attempt.run_id == run.run_id
                and attempt.attempt_id == run.active_attempt_id
                and attempt.attempt_number == run.attempt_count
                and attempt.executor_reference == row.worker_id
            )
        except (AttributeError, ValueError):
            active = False
        if not active:
            fail(
                "VALIDATION_ATTEMPT_REQUIRED",
                "The active native attempt is not running or coherent.",
            )
        return row, run

    def _plan(self, actor, workspace_id, session, *, boundary="publication"):
        row, run = self._context(actor, workspace_id, session, write=False)
        state = self.state_reader(row, session)
        if (
            state.repository_identity != row.repository_identity
            or state.base_sha != row.plan_json["base_sha"]
        ):
            fail("VALIDATION_SOURCE_MISMATCH", "Trusted source identity differs from workspace.")
        return row, run, build_execution_plan(state, boundary=boundary)

    def preview(self, actor, workspace_id):
        with self.repository.sessions() as session:
            return self._plan(actor, workspace_id, session)[2]

    def _operator(self, actor, workspace_id, session):
        access = getattr(self.app.state, "remote_control_service", None)
        if access is None:
            fail("VALIDATION_OPERATOR_UNAVAILABLE", "Authenticated operator access is required.")
        access.access.authorize_in_session(actor, "control", session)
        row, run = self._context(actor, workspace_id, session, write=True)
        if actor.actor_id in {row.actor_id, run.agent_id}:
            fail("VALIDATION_SELF_APPROVAL_DENIED", "Executor cannot approve validation.", 403)
        if access.access.actor_id != actor.actor_id:
            fail("VALIDATION_OPERATOR_REQUIRED", "Use the configured operator identity.", 403)

    def _emit(self, session, actor, task_id, kind, payload):
        self.repository.leases._add_event(
            session,
            "development_validation." + kind,
            "Validation journal authority or evidence",
            task_id=task_id,
            actor_identity_id=actor.actor_id,
            payload=payload,
        )
        session.flush()
        state = session.get(SystemStateRow, 1)
        return session.scalar(
            select(AuditEventRow).where(
                AuditEventRow.event_session_id == state.event_session_id,
                AuditEventRow.sequence_number == state.current_sequence_number,
            )
        )

    def approve(self, operator, workspace_id, expected_plan_hash, *, valid_for_seconds=300):
        if type(valid_for_seconds) is not int or not 1 <= valid_for_seconds <= 3600:
            fail("VALIDATION_APPROVAL_INVALID", "Approval lifetime is invalid.")
        with self.repository.leases._write() as session:
            self._operator(operator, workspace_id, session)
            row, _, plan = self._plan(operator, workspace_id, session)
            if plan.execution_plan_hash != expected_plan_hash:
                fail("VALIDATION_PLAN_CHANGED", "Approve the exact validation plan.")
            if plan.validation.blocked_paths:
                fail(
                    "VALIDATION_SOURCE_SCOPE_BLOCKED",
                    "Validation source scope violates repository policy.",
                )
            expires = datetime.now(UTC) + timedelta(seconds=valid_for_seconds)
            event = self._emit(
                session,
                operator,
                row.task_id,
                "approved",
                {
                    "workspaceId": workspace_id,
                    "plan": plan.model_dump(mode="json"),
                    "expiresAt": expires.isoformat(),
                },
            )
            result = JournalApproval(
                approval_id=event.id,
                workspace_id=workspace_id,
                plan=plan,
                approved_by=operator.actor_id,
                expires_at=expires,
            )
        self.repository.leases.repository.refresh_event_cursor()
        return result

    def _fence(self, actor, workspace_id, request, session):
        row, run = self._context(actor, workspace_id, session, write=True)
        self.repository.leases._require_lease(
            session, row.task_id, request.worker_id, request.lease_token, datetime.now(UTC)
        )
        if (
            request.worker_id != row.worker_id
            or digest(request.lease_token) != row.lease_fingerprint
        ):
            fail("VALIDATION_OWNER_MISMATCH", "Validation requires the original workspace lease.")
        event = session.get(AuditEventRow, request.approval_id)
        try:
            payload = event.payload["payload"]
            expires = datetime.fromisoformat(payload["expiresAt"])
            valid = (
                event.event_type == "development_validation.approved"
                and event.task_id == row.task_id
                and payload["workspaceId"] == workspace_id
                and event.actor != actor.actor_id
                and expires.tzinfo is not None
                and expires > datetime.now(UTC)
            )
        except (AttributeError, KeyError, TypeError, ValueError):
            valid = False
        if not valid:
            fail(
                "VALIDATION_APPROVAL_REQUIRED",
                "A current exact operator approval is required.",
                403,
            )
        self._operator(
            RuntimeActorContext(actor_id=event.actor, stable_key="persisted-operator"),
            workspace_id,
            session,
        )
        _, _, plan = self._plan(actor, workspace_id, session)
        if (
            payload.get("plan") != plan.model_dump(mode="json")
            or request.expected_plan_hash != plan.execution_plan_hash
        ):
            fail("VALIDATION_PLAN_CHANGED", "Approved source state or validation policy changed.")
        return row, run, plan

    def _entries(self, session, task_id, journal_id):
        rows = list(
            session.scalars(
                select(AuditEventRow)
                .where(
                    AuditEventRow.task_id == task_id,
                    AuditEventRow.event_type == "development_validation.journal_entry",
                    AuditEventRow.payload["payload"]["journalId"].as_string() == journal_id,
                )
                .order_by(AuditEventRow.payload["payload"]["entry"]["sequence"].as_integer())
                .limit(129)
            )
        )
        try:
            return tuple(JournalEntry.model_validate(r.payload["payload"]["entry"]) for r in rows)
        except (KeyError, TypeError, ValueError):
            fail("VALIDATION_JOURNAL_INVALID", "Persisted validation history is malformed.")

    def _append(
        self,
        session,
        actor,
        task_id,
        entries,
        journal_id,
        kind,
        *,
        authorization_approval_id,
        **fields,
    ):
        prior = fold(entries) if entries else None
        payload = dict(
            journal_id=journal_id,
            sequence=1 if prior is None else prior.sequence + 1,
            previous_digest=ZERO_DIGEST if prior is None else prior.digest,
            kind=kind,
            timestamp=datetime.now(UTC),
            **fields,
        )
        provisional = JournalEntry(**payload, entry_digest=ZERO_DIGEST)
        entry = JournalEntry.model_validate(
            {
                **provisional.model_dump(mode="json"),
                "entry_digest": digest(
                    provisional.model_dump(mode="json", exclude={"entry_digest"})
                ),
            }
        )
        snapshot = fold((*entries, entry))
        self._emit(
            session,
            actor,
            task_id,
            "journal_entry",
            {
                "journalId": journal_id,
                "entry": entry.model_dump(mode="json"),
                "authorizationApprovalId": authorization_approval_id,
            },
        )
        return snapshot

    def prepare(self, actor, workspace_id, request):
        with self.repository.leases._write() as session:
            row, run, plan = self._fence(actor, workspace_id, request, session)
            owner = JournalOwner(
                workspace_id=workspace_id,
                task_id=row.task_id,
                runtime_run_id=run.run_id,
                attempt_id=run.active_attempt_id,
                worker_id=request.worker_id,
                approval_id=request.approval_id,
                lease_fingerprint=row.lease_fingerprint,
            )
            journal_id = (
                "validation-"
                + digest([workspace_id, plan.execution_plan_hash, run.active_attempt_id])[:40]
            )
            entries = self._entries(session, row.task_id, journal_id)
            intent = JournalIntent(journal_id=journal_id, owner=owner, plan=plan)
            if entries:
                snapshot = fold(entries)
                if (
                    snapshot.intent.plan != plan
                    or snapshot.intent.journal_id != journal_id
                    or snapshot.intent.owner.model_dump(exclude={"approval_id"})
                    != owner.model_dump(exclude={"approval_id"})
                ):
                    fail("VALIDATION_JOURNAL_CONFLICT", "Existing intent has different authority.")
            else:
                snapshot = self._append(
                    session,
                    actor,
                    row.task_id,
                    entries,
                    journal_id,
                    "intent",
                    authorization_approval_id=request.approval_id,
                    intent=intent,
                )
        self.repository.leases.repository.refresh_event_cursor()
        return self.acknowledge(actor, workspace_id, request, snapshot)

    def _checkpoint(self, actor, snapshot):
        owner = snapshot.intent.owner
        identifier = "checkpoint-" + snapshot.intent.journal_id + "-" + str(snapshot.sequence)
        matches = [
            c
            for c in self.runtime.checkpoints_authorized(owner.runtime_run_id, actor)
            if c.checkpoint_id == identifier
        ]
        metadata = dict(
            journalId=snapshot.intent.journal_id,
            journalSequence=snapshot.sequence,
            executionPlanHash=snapshot.intent.plan.execution_plan_hash,
        )
        if matches and (
            len(matches) != 1
            or matches[0].run_id != owner.runtime_run_id
            or matches[0].attempt_id != owner.attempt_id
            or matches[0].integrity_digest != "sha256:" + snapshot.digest
            or matches[0].state_reference != "validation-journal:" + snapshot.intent.journal_id
            or matches[0].resume_cursor != str(snapshot.sequence)
            or matches[0].metadata != metadata
        ):
            fail("VALIDATION_CHECKPOINT_INVALID", "Native checkpoint differs from durable intent.")
        return (matches[0] if matches else None), identifier, metadata

    def acknowledge(self, actor, workspace_id, request, snapshot):
        with self.repository.leases._write() as session:
            row, run, plan = self._fence(actor, workspace_id, request, session)
            owner = snapshot.intent.owner
            current = fold(self._entries(session, row.task_id, snapshot.intent.journal_id))
            if (
                current != snapshot
                or owner.workspace_id != workspace_id
                or owner.runtime_run_id != run.run_id
                or owner.attempt_id != run.active_attempt_id
                or owner.worker_id != request.worker_id
                or owner.lease_fingerprint != digest(request.lease_token)
                or snapshot.intent.plan != plan
            ):
                fail(
                    "VALIDATION_OWNER_MISMATCH",
                    "Checkpoint requires exact persisted journal owner.",
                )
        existing, identifier, metadata = self._checkpoint(actor, snapshot)
        if existing is None:
            owner = snapshot.intent.owner
            runtime = self.runtime.read_run_authorized(owner.runtime_run_id, actor)

            def guard(session):
                row, run, plan = self._fence(actor, workspace_id, request, session)
                current = fold(self._entries(session, row.task_id, snapshot.intent.journal_id))
                if (
                    current != snapshot
                    or run.active_attempt_id != owner.attempt_id
                    or plan != snapshot.intent.plan
                ):
                    fail("VALIDATION_JOURNAL_CONFLICT", "Journal changed before acknowledgement.")

            self.runtime.handle_authorized(
                RecordCheckpointCommand(
                    run_id=owner.runtime_run_id,
                    command_id="ack-" + identifier,
                    expected_run_version=runtime.version,
                    timestamp=datetime.now(UTC),
                    checkpoint_id=identifier,
                    attempt_id=owner.attempt_id,
                    state_reference="validation-journal:" + snapshot.intent.journal_id,
                    integrity_digest="sha256:" + snapshot.digest,
                    resume_cursor=str(snapshot.sequence),
                    checkpoint_metadata=metadata,
                ),
                actor,
                execution_fence=RuntimeExecutionFence(
                    task_id=owner.task_id,
                    worker_id=request.worker_id,
                    lease_token=request.lease_token,
                ),
                commit_guard=guard,
            )
        if self._checkpoint(actor, snapshot)[0] is None:
            fail("VALIDATION_CHECKPOINT_INVALID", "Native checkpoint acknowledgement is missing.")
        with self.repository.leases._write() as session:
            row, run, plan = self._fence(actor, workspace_id, request, session)
            current = fold(self._entries(session, row.task_id, snapshot.intent.journal_id))
            if (
                current != snapshot
                or run.active_attempt_id != snapshot.intent.owner.attempt_id
                or plan != snapshot.intent.plan
                or workspace_id != snapshot.intent.owner.workspace_id
            ):
                fail("VALIDATION_JOURNAL_CONFLICT", "Journal authority changed before return.")
        return snapshot

    def read(self, actor, workspace_id, journal_id):
        # History remains readable after lease expiry or runtime terminalization.
        with self.repository.sessions() as session:
            row = session.get(DevelopmentWorkspaceRow, workspace_id)
            if row is None:
                fail("VALIDATION_WORKSPACE_NOT_FOUND", "Workspace was not found.", 404)
            self.workspace.authorize(actor, row.task_id, session)
            entries = self._entries(session, row.task_id, journal_id)
            if not entries:
                fail("VALIDATION_JOURNAL_NOT_FOUND", "Validation journal was not found.", 404)
            snapshot = fold(entries)
            if snapshot.intent.owner.workspace_id != workspace_id:
                fail("VALIDATION_OWNER_MISMATCH", "Journal belongs to another workspace.")
            return snapshot

    def advance(
        self,
        actor,
        workspace_id,
        request,
        journal_id,
        *,
        expected_sequence,
        kind,
        gate=None,
        command_run_id=None,
        receipt=None,
    ):
        if kind not in {
            "step_intent",
            "step_started",
            "step_result",
            "step_uncertain",
            "finalized",
        }:
            fail("VALIDATION_TRANSITION_INVALID", "Unsupported validation transition.")
        if type(expected_sequence) is not int or not 1 <= expected_sequence < 128:
            fail("VALIDATION_TRANSITION_INVALID", "Expected journal sequence is invalid.")
        if receipt is not None:
            self.verifier.verify(receipt)
        fields = dict(gate=gate, command_run_id=command_run_id, receipt=receipt)
        with self.repository.leases._write() as session:
            row, run, plan = self._fence(actor, workspace_id, request, session)
            entries = self._entries(session, row.task_id, journal_id)
            if not entries:
                fail("VALIDATION_JOURNAL_NOT_FOUND", "Validation journal was not found.", 404)
            current = fold(entries)
            owner = current.intent.owner
            if (
                owner.workspace_id != workspace_id
                or owner.task_id != row.task_id
                or owner.attempt_id != run.active_attempt_id
                or owner.runtime_run_id != run.run_id
                or owner.worker_id != request.worker_id
                or owner.lease_fingerprint != digest(request.lease_token)
                or current.intent.plan != plan
            ):
                fail("VALIDATION_OWNER_MISMATCH", "Journal authority differs from current owner.")
            if current.sequence == expected_sequence + 1:
                prior = entries[-1]
                if (prior.kind, prior.gate, prior.command_run_id, prior.receipt) != (
                    kind,
                    gate,
                    command_run_id,
                    receipt,
                ):
                    fail(
                        "VALIDATION_JOURNAL_CONFLICT", "Transition replay changes recorded content."
                    )
                result = current
            else:
                if current.sequence != expected_sequence:
                    fail("VALIDATION_JOURNAL_CONFLICT", "Journal sequence changed.")
                if self._checkpoint(actor, current)[0] is None:
                    fail(
                        "VALIDATION_RECOVERY_REQUIRED",
                        "Acknowledge durable intent before advancing.",
                    )
                result = self._append(
                    session,
                    actor,
                    row.task_id,
                    entries,
                    journal_id,
                    kind,
                    authorization_approval_id=request.approval_id,
                    **fields,
                )
        self.repository.leases.repository.refresh_event_cursor()
        return self.acknowledge(actor, workspace_id, request, result)

    def reconcile(self, actor, workspace_id, request, journal_id):
        """Acknowledge crash gaps; unfinished side effects become uncertain, never passes.

        This method never re-executes a command or attaches an invented result.
        An already recorded signed result is retained exactly on replay.
        """
        snapshot = self.read(actor, workspace_id, journal_id)
        snapshot = self.acknowledge(actor, workspace_id, request, snapshot)
        if snapshot.steps and snapshot.steps[-1].state in {"intended", "started"}:
            step = snapshot.steps[-1]
            return self.advance(
                actor,
                workspace_id,
                request,
                journal_id,
                expected_sequence=snapshot.sequence,
                kind="step_uncertain",
                gate=step.gate,
                command_run_id=step.command_run_id,
            )
        return snapshot

    def assess(self, actor, workspace_id, journal_id, *, now, max_age_seconds=3600):
        """Assess final acknowledged history; this grants no execution authority."""
        snapshot = self.read(actor, workspace_id, journal_id)
        if snapshot.state != "finalized":
            fail("VALIDATION_JOURNAL_NOT_FINALIZED", "Final validation dispositions are required.")
        if self._checkpoint(actor, snapshot)[0] is None:
            fail(
                "VALIDATION_RECOVERY_REQUIRED",
                "Final validation history requires native acknowledgement.",
            )
        with self.repository.sessions() as session:
            row = session.get(DevelopmentWorkspaceRow, workspace_id)
            self.workspace.authorize(actor, row.task_id, session)
            current_state = self.state_reader(row, session)
        receipts = tuple(step.receipt for step in snapshot.steps if step.receipt is not None)
        return assess_receipts(
            current_state,
            snapshot.intent.plan,
            receipts,
            self.verifier,
            now=now,
            max_age_seconds=max_age_seconds,
        )
