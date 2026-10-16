"""Durable one-effect media intent/claim; never replay an uncertain attachment.

Reuses native approvals/leases, append-only audit, transactional outbox and runtime
checkpoints. No public importer, VM startup or passing execution receipt exists.
"""

from datetime import UTC, datetime

from sqlalchemy import select

from app.agent_runtime.repository import RuntimeExecutionFence
from app.db.models import AuditEventRow, SystemStateRow
from app.development_validation.planner import digest
from app.models.agent_runtime import RecordCheckpointCommand
from app.models.native_delivery import NativeDeliveryEntry, NativeDeliveryIntent
from app.models.validation_cleanup import StorageRegistration
from app.validation_journal.reducer import ZERO_DIGEST, fold
from app.validation_journal.service import fail
from app.validation_runner.isolation import verify_capsule


class NativeDeliveryJournal:
    def __init__(
        self, service, actor, workspace_id, request, journal_id, *, native_identity_verifier
    ):
        self.service, self.actor = service, actor
        self.workspace_id, self.request, self.journal_id = workspace_id, request, journal_id
        # Must authenticate original native creation records and intended host,
        # not enroll whatever filesystem/VM happens to exist at recovery time.
        self.native_verifier = native_identity_verifier

    def _validation(self, session):
        # SQLite _write uses BEGIN IMMEDIATE. On row-locking databases, acquire
        # the existing global sequence/stop row before history reads and appends.
        if (
            session.scalar(select(SystemStateRow).where(SystemStateRow.id == 1).with_for_update())
            is None
        ):
            fail("DELIVERY_RUNTIME_UNAVAILABLE", "Native runtime state is missing.")
        row, run, plan = self.service._fence(self.actor, self.workspace_id, self.request, session)
        snapshot = fold(self.service._entries(session, row.task_id, self.journal_id))
        if (
            snapshot.intent.owner.workspace_id != self.workspace_id
            or snapshot.intent.owner.runtime_run_id != run.run_id
            or snapshot.intent.owner.attempt_id != run.active_attempt_id
            or snapshot.intent.plan != plan
            or snapshot.intent.owner.lease_fingerprint != row.lease_fingerprint
            or snapshot.intent.owner.approval_id != self.request.approval_id
        ):
            fail(
                "DELIVERY_AUTHORITY_CHANGED",
                "Native delivery requires the original live authority.",
            )
        return row, snapshot, plan

    def _history(self, session, intent):
        rows = list(
            session.scalars(
                select(AuditEventRow)
                .where(
                    AuditEventRow.task_id == intent.registration.owner.task_id,
                    AuditEventRow.event_type == "development_validation.native_delivery",
                    AuditEventRow.payload["payload"]["effectId"].as_string() == intent.effect_id,
                )
                .order_by(AuditEventRow.sequence_number)
                .limit(5)
            )
        )
        entries, prior = [], ZERO_DIGEST
        transitions = {
            "intent": {"claimed"},
            "claimed": {"observed", "uncertain", "reconciled"},
            "uncertain": {"reconciled"},
            "observed": set(),
            "reconciled": set(),
        }
        try:
            for row in rows:
                entry = NativeDeliveryEntry.model_validate(row.payload["payload"]["entry"])
                if (
                    entry.effect_id != intent.effect_id
                    or entry.sequence != len(entries) + 1
                    or entry.previous_digest != prior
                    or digest(entry.model_dump(mode="json", exclude={"entry_digest"}))
                    != entry.entry_digest
                    or (not entries and (entry.kind != "intent" or entry.intent != intent))
                    or (entries and entry.kind not in transitions[entries[-1].kind])
                ):
                    raise ValueError("invalid native delivery history")
                entries.append(entry)
                prior = entry.entry_digest
        except (KeyError, TypeError, ValueError):
            fail("DELIVERY_HISTORY_INVALID", "Native delivery history failed integrity checks.")
        return tuple(entries)

    def _fence(self, session, intent):
        row, snapshot, plan = self._validation(session)
        if (
            intent.validation_journal_id != self.journal_id
            or intent.registration.owner != snapshot.intent.owner
            or intent.execution_plan_hash != plan.execution_plan_hash
            or intent.registration.binding.capsule_digest != intent.capsule_digest
        ):
            fail("DELIVERY_AUTHORITY_CHANGED", "Native delivery source or owner changed.")
        self.native_verifier(intent.registration, session)
        return row, snapshot

    def _append(self, session, intent, kind, *, observation_digest=None):
        history = self._history(session, intent)
        body = dict(
            effect_id=intent.effect_id,
            sequence=len(history) + 1,
            previous_digest=history[-1].entry_digest if history else ZERO_DIGEST,
            kind=kind,
            timestamp=datetime.now(UTC),
            intent=intent if kind == "intent" else None,
            observation_digest=observation_digest,
        )
        provisional = NativeDeliveryEntry(**body, entry_digest=ZERO_DIGEST)
        entry = NativeDeliveryEntry.model_validate(
            {
                **provisional.model_dump(mode="json"),
                "entry_digest": digest(
                    provisional.model_dump(mode="json", exclude={"entry_digest"})
                ),
            }
        )
        self.service._emit(
            session,
            self.actor,
            intent.registration.owner.task_id,
            "native_delivery",
            {"effectId": intent.effect_id, "entry": entry.model_dump(mode="json")},
        )
        # Re-read and validate the complete chain in the same transaction.
        return self._history(session, intent)[-1]

    def prepare(self, registration, capsule, plan, operation, image_digest, image_byte_count):
        registration = StorageRegistration.model_validate(registration.model_dump(mode="json"))
        verify_capsule(
            capsule,
            plan,
            owner=registration.owner,
            journal_digest=registration.journal_digest,
            native_inventory_digest=capsule.inventory_digest,
        )
        fields = dict(
            validation_journal_id=self.journal_id,
            registration=registration.model_dump(mode="json"),
            execution_plan_hash=plan.execution_plan_hash,
            capsule_digest=capsule.capsule_digest,
            operation=operation,
            image_digest=image_digest,
            image_byte_count=image_byte_count,
        )
        intent = NativeDeliveryIntent(effect_id="delivery-" + digest(fields)[:40], **fields)
        with self.service.repository.leases._write() as session:
            _, snapshot = self._fence(session, intent)
            origin = self.service._entries(session, registration.owner.task_id, self.journal_id)[0]
            if registration.journal_digest != origin.entry_digest:
                fail(
                    "DELIVERY_JOURNAL_CHANGED", "Capsule must bind the original validation intent."
                )
            if self.service._checkpoint(self.actor, snapshot)[0] is None:
                fail(
                    "DELIVERY_CHECKPOINT_REQUIRED",
                    "A validated source journal checkpoint is required.",
                )
            history = self._history(session, intent)
            if not history:
                self._append(session, intent, "intent")
        self.service.repository.leases.repository.refresh_event_cursor()
        self._acknowledge(intent)
        return intent

    def _checkpoint(self, intent, entry):
        owner = intent.registration.owner
        identifier = f"delivery-checkpoint-{intent.effect_id}-{entry.sequence}"
        metadata = {
            "effectId": intent.effect_id,
            "sequence": entry.sequence,
            "validationJournalId": self.journal_id,
            "executionPlanHash": intent.execution_plan_hash,
        }
        matches = [
            c
            for c in self.service.runtime.checkpoints_authorized(owner.runtime_run_id, self.actor)
            if c.checkpoint_id == identifier
        ]
        if matches and (
            len(matches) != 1
            or matches[0].attempt_id != owner.attempt_id
            or matches[0].integrity_digest != "sha256:" + entry.entry_digest
            or matches[0].state_reference != "native-delivery:" + intent.effect_id
            or matches[0].resume_cursor != str(entry.sequence)
            or matches[0].metadata != metadata
        ):
            fail("DELIVERY_CHECKPOINT_INVALID", "Native delivery checkpoint is incoherent.")
        return (matches[0] if matches else None), identifier, metadata

    def _acknowledge(self, intent):
        with self.service.repository.leases._write() as session:
            self._fence(session, intent)
            entry = self._history(session, intent)[-1]
        existing, identifier, metadata = self._checkpoint(intent, entry)
        if existing is None:
            owner = intent.registration.owner
            runtime = self.service.runtime.read_run_authorized(owner.runtime_run_id, self.actor)

            def guard(session):
                self._fence(session, intent)
                if self._history(session, intent)[-1] != entry:
                    fail("DELIVERY_HISTORY_CHANGED", "Native delivery changed before checkpoint.")

            self.service.runtime.handle_authorized(
                RecordCheckpointCommand(
                    run_id=owner.runtime_run_id,
                    command_id="ack-" + identifier,
                    expected_run_version=runtime.version,
                    timestamp=datetime.now(UTC),
                    checkpoint_id=identifier,
                    attempt_id=owner.attempt_id,
                    state_reference="native-delivery:" + intent.effect_id,
                    integrity_digest="sha256:" + entry.entry_digest,
                    resume_cursor=str(entry.sequence),
                    checkpoint_metadata=metadata,
                ),
                self.actor,
                execution_fence=RuntimeExecutionFence(
                    task_id=owner.task_id,
                    worker_id=self.request.worker_id,
                    lease_token=self.request.lease_token,
                ),
                commit_guard=guard,
            )
        if self._checkpoint(intent, entry)[0] is None:
            fail(
                "DELIVERY_CHECKPOINT_REQUIRED",
                "Native delivery checkpoint acknowledgment is missing.",
            )

    def require_acknowledged(self, intent):
        intent = NativeDeliveryIntent.model_validate(intent.model_dump(mode="json"))
        with self.service.repository.leases._write() as session:
            self._fence(session, intent)
            history = self._history(session, intent)
            if not history or self._checkpoint(intent, history[-1])[0] is None:
                fail(
                    "DELIVERY_CHECKPOINT_REQUIRED", "Persisted native intent needs acknowledgment."
                )

    def claim(self, intent):
        self.require_acknowledged(intent)
        with self.service.repository.leases._write() as session:
            self._fence(session, intent)
            history = self._history(session, intent)
            if history[-1].kind != "intent":
                fail(
                    "DELIVERY_RECONCILIATION_REQUIRED",
                    "Claimed delivery must be observed, never replayed.",
                )
            self._append(session, intent, "claimed")
        self.service.repository.leases.repository.refresh_event_cursor()
        self._acknowledge(intent)

    def complete(self, intent, observation):
        from app.validation_runner.media import verify_media_observation

        verify_media_observation(
            observation,
            intent.registration.binding,
            "attached" if intent.operation == "attach_media" else "detached",
            intent.image_digest,
            intent.image_byte_count,
        )
        with self.service.repository.leases._write() as session:
            self._fence(session, intent)
            if self._history(session, intent)[-1].kind != "claimed":
                fail("DELIVERY_RECONCILIATION_REQUIRED", "Native delivery needs its exact claim.")
            self._append(session, intent, "observed", observation_digest=digest(observation))
        self.service.repository.leases.repository.refresh_event_cursor()
        self._acknowledge(intent)

    def uncertain(self, intent):
        # If lease/permission loss prevents appending, the persisted claim already
        # means uncertain and remains unreplayable. Never hide the original error.
        try:
            with self.service.repository.leases._write() as session:
                self._fence(session, intent)
                if self._history(session, intent)[-1].kind == "claimed":
                    self._append(session, intent, "uncertain")
            self.service.repository.leases.repository.refresh_event_cursor()
        except Exception:
            return False
        return True
