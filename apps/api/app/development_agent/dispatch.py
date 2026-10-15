"""Fenced protected evidence composed with native runtime checkpoints and outbox."""

from datetime import UTC, datetime, timedelta
from functools import wraps
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.agent_runtime.authorization import RuntimeActorContext
from app.agent_runtime.repository import RuntimeExecutionFence
from app.db.models import AuditEventRow, TaskRow
from app.development_agent import request as request_builder
from app.development_agent.context import RepositoryContextReader, refuse
from app.development_agent.repository import DevelopmentAdmissionRow, DevelopmentDispatchRow
from app.model_providers.contracts import ModelExecutionResponse, UsageQuality
from app.model_providers.tool_calls import decode_model_response
from app.models.agent_runtime import RecordCheckpointCommand
from app.models.development_agent import DevelopmentProposal
from app.models.development_dispatch import (
    AdmissionPlan,
    DevelopmentAdmission,
    DevelopmentDispatch,
    DevelopmentLimits,
    DispatchReservation,
)
from app.models.domain import Task
from app.self_build.policy import digest

POLICY_SOURCE_PATHS = (
    "development_agent/dispatch.py",
    "development_agent/repository.py",
    "development_agent/request.py",
    "development_agent/context.py",
    "development_agent/provider.py",
    "development_agent/proposals.py",
    "models/development_dispatch.py",
    "models/development_agent.py",
    "models/development_validation.py",
    "models/source_change.py",
    "models/context.py",
    "context/assembler.py",
    "context/security.py",
    "context/budget.py",
    "model_providers/contracts.py",
    "model_providers/tool_calls.py",
    "self_build/change_verifier.py",
    "tool_execution/filesystem.py",
)


def policy_digest():
    root = Path(__file__).resolve().parents[1]
    return digest(
        {
            "implementation": {
                name: (root / name).read_text(encoding="utf-8") for name in POLICY_SOURCE_PATHS
            }
        }
    )


POLICY_DIGEST = policy_digest()


def protected_storage(method):
    @wraps(method)
    def invoke(*args, **kwargs):
        failed = False
        try:
            return method(*args, **kwargs)
        except (SQLAlchemyError, ValueError, TypeError, AttributeError, KeyError):
            failed = True
        if failed:
            refuse("DEVELOPMENT_RECORD_INVALID")

    return invoke


class DevelopmentDispatchService:
    """Internal only. Reserves evidence; it never calls a model or mutates source."""

    def __init__(self, app):
        self.app = app
        self.reader = RepositoryContextReader(app)
        self.creation = self.reader.creation
        self.repository = self.creation.repository
        self.runtime = self.creation.runtime

    def task(self, session, task_id):
        row = session.get(TaskRow, task_id)
        if row is None:
            refuse("DEVELOPMENT_TASK_REQUIRED")
        task = Task(
            id=row.id,
            title=row.title,
            description=row.description,
            request=row.original_request,
            projectId=row.project_id,
            parentTaskId=row.parent_task_id,
            createdBy=row.creator,
            createdAt=row.created_at,
            updatedAt=row.updated_at,
        )
        fingerprint = digest(
            dict(
                id=row.id,
                title=row.title,
                description=row.description,
                request=row.original_request,
                projectId=row.project_id,
                parentTaskId=row.parent_task_id,
            )
        )
        return task, fingerprint

    def plan(self, session, actor, authority, context, limits):
        row, _, attempt = self.creation.fence(actor, context.workspace_id, authority, session)
        _, fingerprint = self.task(session, row.task_id)
        payload = dict(
            schema_version="1.0",
            task_id=row.task_id,
            task_fingerprint=fingerprint,
            runtime_run_id=row.runtime_run_id,
            attempt_id=attempt,
            workspace_id=row.id,
            creation_operation_id=context.creation_operation_id,
            creation_plan_hash=context.creation_plan_hash,
            workspace_plan_hash=self.repository.contract(row).plan.plan_hash,
            workspace_approval_id=row.approval_id,
            actor_id=actor.actor_id,
            worker_id=authority.worker_id,
            lease_fingerprint=digest(authority.lease_token),
            initial_context_hash=context.context_hash,
            selection=context.selection,
            limits=limits,
            policy_digest=POLICY_DIGEST,
        )
        provisional = AdmissionPlan(**payload, plan_hash="0" * 64)
        return row, provisional.model_copy(
            update={"plan_hash": digest(provisional.model_dump(mode="json", exclude={"plan_hash"}))}
        )

    @protected_storage
    def preview(self, actor, workspace_id, authority, selection, limits):
        limits = DevelopmentLimits.model_validate(limits.model_dump(mode="json", warnings=False))

        def finalize(context, guard):
            with self.repository.sessions() as session:
                result = self.plan(session, actor, authority, context, limits)[1]
            guard()
            return result

        return self.reader.inspect(actor, workspace_id, authority, selection, finalize=finalize)

    def audit(self, session, identifier, row, event_type, actor_id, payload):
        event = session.get(AuditEventRow, identifier)
        if (
            event is None
            or event.event_type != event_type
            or event.task_id != row.task_id
            or event.actor != actor_id
            or event.payload.get("payload") != payload
        ):
            refuse("DEVELOPMENT_AUDIT_INVALID")

    def approval_payload(self, plan):
        return dict(planHash=plan.plan_hash, executionActorId=plan.actor_id)

    def root(self, session, actor, workspace_row, authority):
        stored = session.get(DevelopmentAdmissionRow, workspace_row.task_id)
        if stored is None:
            refuse("DEVELOPMENT_ADMISSION_REQUIRED")
        record = DevelopmentAdmission.model_validate(stored.payload)
        plan = record.plan
        _, fingerprint = self.task(session, workspace_row.task_id)
        if (
            digest(stored.payload) != stored.integrity_digest
            or plan.plan_hash != digest(plan.model_dump(mode="json", exclude={"plan_hash"}))
            or plan.task_id != stored.task_id
            or plan.task_fingerprint != fingerprint
            or plan.actor_id != actor.actor_id
            or plan.workspace_id != workspace_row.id
            or plan.runtime_run_id != workspace_row.runtime_run_id
            or plan.attempt_id
            != self.repository.runtime(session, workspace_row.runtime_run_id).active_attempt_id
            or plan.workspace_plan_hash != self.repository.contract(workspace_row).plan.plan_hash
            or plan.workspace_approval_id != workspace_row.approval_id
            or plan.worker_id != authority.worker_id
            or plan.lease_fingerprint != digest(authority.lease_token)
            or plan.policy_digest != POLICY_DIGEST
            or record.approved_by == actor.actor_id
        ):
            refuse("DEVELOPMENT_ADMISSION_CONFLICT")
        self.audit(
            session,
            record.approval_id,
            workspace_row,
            "development.admitted",
            record.approved_by,
            self.approval_payload(plan),
        )
        self.creation.operator(
            RuntimeActorContext(actor_id=record.approved_by, stable_key="persisted-operator"),
            workspace_row,
            session,
        )
        return record, stored.integrity_digest

    def root_metadata(self, record):
        return dict(
            schemaName="development-admission-1",
            taskId=record.plan.task_id,
            planHash=record.plan.plan_hash,
            approvalId=record.approval_id,
            phase="admitted",
        )

    def dispatch_metadata(self, record, phase):
        reservation = record.reservation
        result = dict(
            schemaName="development-dispatch-1",
            dispatchId=reservation.dispatch_id,
            taskId=reservation.context.task_id,
            ordinal=reservation.ordinal,
            requestDigest=reservation.request_digest,
            contextHash=reservation.context.context_hash,
            phase=phase,
            admissionDigest=reservation.admission_digest,
            auditId=reservation.audit_id,
        )
        if phase == "received":
            result.update(
                responseDigest=digest(record.response.model_dump(mode="json")),
                responseAuditId=record.response_audit_id,
            )
        return result

    def checkpoint(self, actor, run_id, attempt_id, slot, phase, protected_digest, metadata):
        identifier = f"checkpoint-development-{slot}-{phase}"
        state_reference = "development:" + slot
        matches = [
            checkpoint
            for checkpoint in self.runtime.checkpoints_authorized(run_id, actor)
            if checkpoint.checkpoint_id == identifier
        ]
        if matches and (
            len(matches) != 1
            or matches[0].run_id != run_id
            or matches[0].attempt_id != attempt_id
            or matches[0].state_reference != state_reference
            or matches[0].integrity_digest != "sha256:" + protected_digest
            or matches[0].resume_cursor != phase
            or matches[0].metadata != metadata
        ):
            refuse("DEVELOPMENT_CHECKPOINT_INVALID")
        return (matches[0] if matches else None), identifier

    def acknowledge(self, actor, authority, workspace_id, record, *, root=False):
        if root:
            plan = record.plan
            run_id, attempt = plan.runtime_run_id, plan.attempt_id
            slot, phase = digest(["admission", plan.task_id])[:40], "admitted"
            protected_digest = digest(record.model_dump(mode="json"))
            metadata = self.root_metadata(record)
        else:
            reservation = record.reservation
            if record.response is not None:
                self.require_reserved(actor, record)
            run_id, attempt = (
                reservation.context.runtime_run_id,
                reservation.context.runtime_attempt_id,
            )
            slot = reservation.dispatch_id
            phase = "received" if record.response is not None else "reserved"
            protected_digest = (
                digest(record.model_dump(mode="json"))
                if phase == "received"
                else digest(reservation.model_dump(mode="json"))
            )
            metadata = self.dispatch_metadata(record, phase)
        existing, identifier = self.checkpoint(
            actor,
            run_id,
            attempt,
            slot,
            phase,
            protected_digest,
            metadata,
        )
        if existing is None:
            snapshot = self.runtime.read_run_authorized(run_id, actor)

            def guard(session):
                row, _, current_attempt = self.creation.fence(
                    actor, workspace_id, authority, session
                )
                current_root, root_digest = self.root(session, actor, row, authority)
                if current_attempt != attempt:
                    refuse("DEVELOPMENT_OWNER_CHANGED")
                if root:
                    if current_root != record:
                        refuse("DEVELOPMENT_ADMISSION_CONFLICT")
                else:
                    stored = session.get(DevelopmentDispatchRow, slot)
                    current = self.dispatch_record(session, row, stored, current_root, root_digest)
                    current_digest = (
                        digest(current.model_dump(mode="json"))
                        if phase == "received"
                        else digest(current.reservation.model_dump(mode="json"))
                    )
                    if current_digest != protected_digest:
                        refuse("DEVELOPMENT_DISPATCH_CONFLICT")

            self.runtime.handle_authorized(
                RecordCheckpointCommand(
                    run_id=run_id,
                    command_id="ack-" + identifier,
                    expected_run_version=snapshot.version,
                    timestamp=datetime.now(UTC),
                    checkpoint_id=identifier,
                    attempt_id=attempt,
                    state_reference="development:" + slot,
                    integrity_digest="sha256:" + protected_digest,
                    resume_cursor=phase,
                    checkpoint_metadata=metadata,
                ),
                actor,
                execution_fence=RuntimeExecutionFence(
                    task_id=record.plan.task_id if root else record.reservation.context.task_id,
                    worker_id=authority.worker_id,
                    lease_token=authority.lease_token,
                ),
                commit_guard=guard,
            )
        if (
            self.checkpoint(actor, run_id, attempt, slot, phase, protected_digest, metadata)[0]
            is None
        ):
            refuse("DEVELOPMENT_CHECKPOINT_REQUIRED")

    @protected_storage
    def admit(
        self, operator, actor, workspace_id, authority, selection, limits, expected_plan_hash
    ):
        limits = DevelopmentLimits.model_validate(limits.model_dump(mode="json", warnings=False))

        def finalize(context, guard):
            with self.repository.leases._write() as session:
                row, plan = self.plan(session, actor, authority, context, limits)
                self.creation.operator(operator, row, session)
                if plan.plan_hash != expected_plan_hash:
                    refuse("DEVELOPMENT_PLAN_CHANGED")
                stored = session.get(DevelopmentAdmissionRow, row.task_id)
                if stored is not None:
                    record, _ = self.root(session, actor, row, authority)
                    if record.plan != plan or record.approved_by != operator.actor_id:
                        refuse("DEVELOPMENT_ADMISSION_CONFLICT")
                else:
                    event = self.creation.git.event(
                        session, row, operator, "development.admitted", self.approval_payload(plan)
                    )
                    record = DevelopmentAdmission(
                        plan=plan,
                        approval_id=event.id,
                        approved_by=operator.actor_id,
                        admitted_at=datetime.now(UTC),
                    )
                    session.add(
                        DevelopmentAdmissionRow(
                            task_id=row.task_id,
                            payload=record.model_dump(mode="json"),
                            integrity_digest=digest(record.model_dump(mode="json")),
                        )
                    )
                guard.in_transaction(session)
            return record

        record = self.reader.inspect(actor, workspace_id, authority, selection, finalize=finalize)
        self.repository.leases.repository.refresh_event_cursor()
        self.acknowledge(actor, authority, workspace_id, record, root=True)
        return record

    def reserve_payload(self, reservation):
        return dict(
            dispatchId=reservation.dispatch_id,
            ordinal=reservation.ordinal,
            requestDigest=reservation.request_digest,
            contextHash=reservation.context.context_hash,
            admissionDigest=reservation.admission_digest,
        )

    def dispatch_record(self, session, row, stored, root, root_digest):
        if stored is None:
            refuse("DEVELOPMENT_DISPATCH_REQUIRED")
        record = DevelopmentDispatch.model_validate(stored.payload)
        reservation = record.reservation
        context, request = reservation.context, reservation.request
        if (
            digest(stored.payload) != stored.integrity_digest
            or stored.id != reservation.dispatch_id
            or stored.ordinal != reservation.ordinal
            or stored.task_id != root.plan.task_id
            or reservation.admission_digest != root_digest
            or context.context_hash
            != digest(context.model_dump(mode="json", exclude={"context_hash"}))
            or context.task_id != root.plan.task_id
            or context.workspace_id != root.plan.workspace_id
            or context.runtime_run_id != root.plan.runtime_run_id
            or context.runtime_attempt_id != root.plan.attempt_id
            or context.creation_operation_id != root.plan.creation_operation_id
            or context.creation_plan_hash != root.plan.creation_plan_hash
            or not set(context.selection.paths).issubset(root.plan.selection.paths)
            or request.task_id != root.plan.task_id
            or request.correlation_id != stored.id
            or request.model != root.plan.limits.model
            or request.metadata != {"context_hash": context.context_hash}
            or reservation.request_digest != digest(request.model_dump(mode="json"))
            or request.max_output_tokens != root.plan.limits.output_tokens_per_dispatch
            or stored.id != digest([root.plan.task_id, reservation.command_id])
        ):
            refuse("DEVELOPMENT_DISPATCH_CONFLICT")
        self.audit(
            session,
            reservation.audit_id,
            row,
            "development.dispatch.reserved",
            root.plan.actor_id,
            self.reserve_payload(reservation),
        )
        if record.response is not None:
            self.audit(
                session,
                record.response_audit_id,
                row,
                "development.dispatch.received",
                root.plan.actor_id,
                dict(
                    dispatchId=stored.id,
                    responseDigest=digest(record.response.model_dump(mode="json")),
                ),
            )
            self.validate_response(record, root, record.response)
        return record

    def require_reserved(self, actor, record):
        reservation = record.reservation
        if (
            self.checkpoint(
                actor,
                reservation.context.runtime_run_id,
                reservation.context.runtime_attempt_id,
                reservation.dispatch_id,
                "reserved",
                digest(reservation.model_dump(mode="json")),
                self.dispatch_metadata(record, "reserved"),
            )[0]
            is None
        ):
            refuse("DEVELOPMENT_CHECKPOINT_REQUIRED")

    def require_acknowledged(self, actor, record, *, root=False):
        if root:
            values = (
                record.plan.runtime_run_id,
                record.plan.attempt_id,
                digest(["admission", record.plan.task_id])[:40],
                "admitted",
                digest(record.model_dump(mode="json")),
                self.root_metadata(record),
            )
        else:
            self.require_reserved(actor, record)
            reservation = record.reservation
            phase = "received" if record.response else "reserved"
            values = (
                reservation.context.runtime_run_id,
                reservation.context.runtime_attempt_id,
                reservation.dispatch_id,
                phase,
                digest(record.model_dump(mode="json"))
                if record.response
                else digest(reservation.model_dump(mode="json")),
                self.dispatch_metadata(record, phase),
            )
        if self.checkpoint(actor, *values)[0] is None:
            refuse("DEVELOPMENT_CHECKPOINT_REQUIRED")

    def remaining_seconds(self, root):
        remaining = (
            root.admitted_at
            + timedelta(seconds=root.plan.limits.maximum_seconds)
            - datetime.now(UTC)
        ).total_seconds()
        if remaining < 1:
            refuse("DEVELOPMENT_BUDGET_EXHAUSTED")
        return min(300, remaining)

    @protected_storage
    def reserve(self, actor, workspace_id, authority, selection, command_id):
        def finalize(context, guard):
            with self.repository.leases._write() as session:
                row, _, attempt = self.creation.fence(actor, workspace_id, authority, session)
                root, root_digest = self.root(session, actor, row, authority)
                self.require_acknowledged(actor, root, root=True)
                if attempt != root.plan.attempt_id or not set(context.selection.paths).issubset(
                    root.plan.selection.paths
                ):
                    refuse("DEVELOPMENT_ADMISSION_CONFLICT")
                dispatch_id = digest([row.task_id, command_id])
                existing = session.get(DevelopmentDispatchRow, dispatch_id)
                if existing is not None:
                    record = self.dispatch_record(session, row, existing, root, root_digest)
                    if record.reservation.context != context:
                        refuse("DEVELOPMENT_CONTEXT_STALE")
                    return record, False
                timeout = self.remaining_seconds(root)
                stored_records = session.scalars(
                    select(DevelopmentDispatchRow)
                    .where(DevelopmentDispatchRow.task_id == row.task_id)
                    .order_by(DevelopmentDispatchRow.ordinal)
                    .limit(9)
                ).all()
                records = [
                    self.dispatch_record(session, row, item, root, root_digest)
                    for item in stored_records
                ]
                if [item.reservation.ordinal for item in records] != list(
                    range(1, len(records) + 1)
                ):
                    refuse("DEVELOPMENT_RECORD_INVALID")
                for record in records:
                    self.require_acknowledged(actor, record)
                    if record.response is None:
                        refuse("DEVELOPMENT_DISPATCH_OUTCOME_UNKNOWN")
                limits = root.plan.limits
                if (
                    len(records) >= limits.maximum_dispatches
                    or (len(records) + 1) * limits.output_tokens_per_dispatch
                    > limits.maximum_reserved_output_tokens
                ):
                    refuse("DEVELOPMENT_BUDGET_EXHAUSTED")
                task, _ = self.task(session, row.task_id)
                request, manifest = request_builder.build_request(
                    self.app.state.context_assembler, task, context, root, dispatch_id, timeout
                )
                payload = dict(
                    dispatchId=dispatch_id,
                    ordinal=len(records) + 1,
                    requestDigest=digest(request.model_dump(mode="json")),
                    contextHash=context.context_hash,
                    admissionDigest=root_digest,
                )
                event = self.creation.git.event(
                    session, row, actor, "development.dispatch.reserved", payload
                )
                record = DevelopmentDispatch(
                    reservation=DispatchReservation(
                        dispatch_id=dispatch_id,
                        command_id=command_id,
                        ordinal=len(records) + 1,
                        admission_digest=root_digest,
                        context=context,
                        request=request,
                        context_manifest=manifest,
                        request_digest=payload["requestDigest"],
                        audit_id=event.id,
                        reserved_at=datetime.now(UTC),
                    )
                )
                session.add(
                    DevelopmentDispatchRow(
                        id=dispatch_id,
                        task_id=row.task_id,
                        ordinal=len(records) + 1,
                        payload=record.model_dump(mode="json"),
                        integrity_digest=digest(record.model_dump(mode="json")),
                    )
                )
                guard.in_transaction(session)
                self.remaining_seconds(root)
                return record, True

        record, fresh = self.reader.inspect(
            actor, workspace_id, authority, selection, finalize=finalize
        )
        self.repository.leases.repository.refresh_event_cursor()
        self.acknowledge(actor, authority, workspace_id, record)
        return record, fresh

    def validate_response(self, record, root, response):
        raw = response.model_dump(mode="json", warnings=False)
        counts = [raw.get(key) for key in ("input_tokens", "output_tokens", "total_tokens")]
        if (
            any(type(value) is not int or value < 0 for value in counts)
            or counts[2] != counts[0] + counts[1]
        ):
            refuse("DEVELOPMENT_RESPONSE_INVALID")
        response = ModelExecutionResponse.model_validate(
            response.model_dump(mode="json", warnings=False)
        )
        request = record.reservation.request
        if (
            response.provider != root.plan.limits.provider
            or response.model != request.model
            or response.task_id != request.task_id
            or response.correlation_id != request.correlation_id
            or response.tool_requests
            or response.usage_quality != UsageQuality.EXACT
            or response.finish_reason != "stop"
            or any(
                type(value) is not int or value < 0
                for value in (response.input_tokens, response.output_tokens, response.total_tokens)
            )
            or response.total_tokens != response.input_tokens + response.output_tokens
            or response.output_tokens > request.max_output_tokens
        ):
            refuse("DEVELOPMENT_RESPONSE_INVALID")
        import httpx

        proposal = DevelopmentProposal.model_validate(
            decode_model_response(
                httpx.Response(200, content=response.content.encode("utf-8")), tools_enabled=True
            )
        )
        if proposal.context_hash != record.reservation.context.context_hash:
            refuse("DEVELOPMENT_RESPONSE_INVALID")
        # Persist known provider evidence before native source-plan interpretation.
        # A semantically invalid edit is repairable within the same root allowance;
        # it is not an uncertain physical dispatch or filesystem authority.
        return response

    @protected_storage
    def receive(self, actor, workspace_id, authority, dispatch_id, response):
        with self.repository.sessions() as session:
            row, _, _ = self.creation.fence(actor, workspace_id, authority, session)
            root, root_digest = self.root(session, actor, row, authority)
            self.require_acknowledged(actor, root, root=True)
            current = self.dispatch_record(
                session, row, session.get(DevelopmentDispatchRow, dispatch_id), root, root_digest
            )
        response = self.validate_response(current, root, response)

        def finalize(context, guard):
            with self.repository.leases._write() as session:
                row, _, attempt = self.creation.fence(actor, workspace_id, authority, session)
                root, root_digest = self.root(session, actor, row, authority)
                self.require_acknowledged(actor, root, root=True)
                stored = session.get(DevelopmentDispatchRow, dispatch_id)
                previous = self.dispatch_record(session, row, stored, root, root_digest)
                if attempt != root.plan.attempt_id or previous.reservation.context != context:
                    refuse("DEVELOPMENT_OWNER_CHANGED")
                if previous.response is not None:
                    if previous.response != response:
                        refuse("DEVELOPMENT_DISPATCH_CONFLICT")
                    return previous
                self.remaining_seconds(root)
                self.require_acknowledged(actor, previous)
                event = self.creation.git.event(
                    session,
                    row,
                    actor,
                    "development.dispatch.received",
                    dict(
                        dispatchId=dispatch_id,
                        responseDigest=digest(response.model_dump(mode="json")),
                    ),
                )
                record = DevelopmentDispatch(
                    reservation=previous.reservation, response=response, response_audit_id=event.id
                )
                stored.payload = record.model_dump(mode="json")
                stored.integrity_digest = digest(stored.payload)
                guard.in_transaction(session)
                self.remaining_seconds(root)
            return record

        record = self.reader.inspect(
            actor,
            workspace_id,
            authority,
            current.reservation.context.selection,
            expected_hash=current.reservation.context.context_hash,
            finalize=finalize,
        )
        self.repository.leases.repository.refresh_event_cursor()
        self.acknowledge(actor, authority, workspace_id, record)
        return record

    @protected_storage
    def read(self, actor, workspace_id, authority, dispatch_id):
        with self.repository.sessions() as session:
            row, _, _ = self.creation.fence(actor, workspace_id, authority, session)
            root, root_digest = self.root(session, actor, row, authority)
            self.require_acknowledged(actor, root, root=True)
            record = self.dispatch_record(
                session, row, session.get(DevelopmentDispatchRow, dispatch_id), root, root_digest
            )
        self.require_acknowledged(actor, record)
        return record
