"""Append-only publication ledger with real runtime checkpoint acknowledgements."""

from datetime import UTC, datetime

from sqlalchemy import select

from app.agent_runtime.repository import RuntimeExecutionFence
from app.core.errors import DomainError
from app.db.models import AuditEventRow
from app.github_publication.lifecycle import ZERO, fold
from app.models.agent_runtime import RecordCheckpointCommand
from app.models.publication_lifecycle import PublicationEntry
from app.self_build.policy import digest

EVENT = "github.publication.entry"


def fail():
    raise DomainError(
        "GITHUB_PUBLICATION_JOURNAL_INVALID",
        "Publication history or checkpoint differs from protected intent.",
        409,
    )


class PublicationJournal:
    def __init__(self, service):
        self.service = service
        self.repository = service.native.repository
        self.runtime = service.native.preparation.runtime

    def entries(self, session, row, operation_id):
        events = list(
            session.scalars(
                select(AuditEventRow)
                .where(
                    AuditEventRow.event_type == EVENT,
                    AuditEventRow.task_id == row.task_id,
                    AuditEventRow.payload["payload"]["operationId"].as_string() == operation_id,
                )
                .order_by(AuditEventRow.payload["payload"]["entry"]["sequence"].as_integer())
                .limit(129)
            )
        )
        try:
            if not events:
                raise DomainError("GITHUB_PUBLICATION_NOT_FOUND", "Publication was not found.", 404)
            if len(events) > 128 or any(event.actor != row.actor_id for event in events):
                fail()
            entries = tuple(
                PublicationEntry.model_validate(event.payload["payload"]["entry"])
                for event in events
            )
            snapshot = fold(entries)
            if (
                snapshot.operation_id != operation_id
                or snapshot.plan.workspace_id != row.id
                or snapshot.plan.task_id != row.task_id
                or snapshot.plan.actor_id != row.actor_id
                or snapshot.plan.runtime_run_id != row.runtime_run_id
            ):
                fail()
            return entries
        except (AttributeError, KeyError, TypeError, ValueError):
            fail()

    def read(self, actor, workspace_id, operation_id):
        with self.repository.sessions() as session:
            row = self.repository.lookup(session, workspace_id)
            self.service.native.preparation.workspace.authorize(actor, row.task_id, session)
            return fold(self.entries(session, row, operation_id))

    def append(
        self, actor, request, operation_id, plan, kind, fs_guard, *, expected=None, **evidence
    ):
        """The service resolves current persisted approval, source and native owner.

        The caller's exact previous snapshot prevents concurrent sequence changes.
        All records enter existing audit/outbox in this same transaction. No
        checkpoint or effect success is invented by an audit-only transition.
        """
        with self.repository.leases._write() as session:
            row = self.service.live(actor, request, plan, session)
            if expected is None:
                exists = session.scalar(
                    select(AuditEventRow)
                    .where(
                        AuditEventRow.event_type == EVENT,
                        AuditEventRow.payload["payload"]["operationId"].as_string() == operation_id,
                    )
                    .limit(1)
                )
                if exists is not None or kind != "prepared":
                    fail()
                entries = ()
            else:
                entries = self.entries(session, row, operation_id)
                if fold(entries) != expected or expected.plan != plan:
                    fail()
            payload = (
                dict(
                    operation_id=operation_id,
                    sequence=len(entries) + 1,
                    previous_digest=ZERO if expected is None else expected.digest,
                    kind=kind,
                    plan=plan.model_dump(mode="json") if kind == "prepared" else None,
                    request_digest=None,
                    pr_number=None,
                    pr_url=None,
                    observation=None,
                    validation_confirmed_digest=None,
                    error_code=None,
                    retry_not_before=None,
                    timestamp=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                )
                | evidence
            )
            entry = PublicationEntry(**payload, entry_digest=digest(payload))
            following = fold((*entries, entry))
            fs_guard(check_authority=False)
            self.service.native.preparation.git.event(
                session,
                row,
                actor,
                EVENT,
                dict(
                    operationId=operation_id,
                    commandId=request.command_id,
                    approvalId=request.approval_id,
                    entry=entry.model_dump(mode="json"),
                ),
            )
            fs_guard(check_authority=False)
        self.repository.leases.repository.refresh_event_cursor()
        return following

    def checkpoint(self, actor, snapshot):
        identifier = f"checkpoint-{snapshot.operation_id}-{snapshot.sequence}"
        matches = [
            item
            for item in self.runtime.checkpoints_authorized(snapshot.plan.runtime_run_id, actor)
            if item.checkpoint_id == identifier
        ]
        metadata = dict(
            operationId=snapshot.operation_id,
            publicationPlanHash=snapshot.plan.plan_hash,
            sequence=snapshot.sequence,
            entryDigest=snapshot.digest,
        )
        if len(matches) > 1:
            fail()
        if matches:
            value = matches[0]
            if (
                value.run_id != snapshot.plan.runtime_run_id
                or value.attempt_id != snapshot.plan.attempt_id
                or value.integrity_digest != "sha256:" + snapshot.digest
                or value.resume_cursor != snapshot.state
                or value.state_reference != "github-publication:" + snapshot.operation_id
                or value.metadata != metadata
            ):
                fail()
        return (matches[0] if matches else None), identifier, metadata

    def acknowledge(self, actor, request, snapshot, fs_guard):
        existing, identifier, metadata = self.checkpoint(actor, snapshot)
        if existing is None:
            run = self.runtime.read_run_authorized(snapshot.plan.runtime_run_id, actor)

            def commit_guard(session):
                row = self.service.live(actor, request, snapshot.plan, session)
                if fold(self.entries(session, row, snapshot.operation_id)) != snapshot:
                    fail()
                fs_guard(check_authority=False)

            self.runtime.handle_authorized(
                RecordCheckpointCommand(
                    run_id=snapshot.plan.runtime_run_id,
                    command_id="ack-" + identifier,
                    expected_run_version=run.version,
                    timestamp=datetime.now(UTC),
                    checkpoint_id=identifier,
                    attempt_id=snapshot.plan.attempt_id,
                    state_reference="github-publication:" + snapshot.operation_id,
                    integrity_digest="sha256:" + snapshot.digest,
                    resume_cursor=snapshot.state,
                    checkpoint_metadata=metadata,
                ),
                actor,
                execution_fence=RuntimeExecutionFence(
                    task_id=snapshot.plan.task_id,
                    worker_id=request.worker_id,
                    lease_token=request.lease_token,
                ),
                commit_guard=commit_guard,
            )
        if self.checkpoint(actor, snapshot)[0] is None:
            fail()
        return snapshot
