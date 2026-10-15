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
    AssemblyLimits,
    DevelopmentAdmission,
    DevelopmentDispatch,
    DevelopmentLimits,
    DispatchFailure,
    DispatchReservation,
    EditingBinding,
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
    "development_agent/execution.py",
    "development_agent/config.py",
    "development_agent/review.py",
    "development_agent/baselines.py",
    "development_agent/baseline_repository.py",
    "development_agent/corrections.py",
    "development_agent/lineages.py",
    "development_agent/publication.py",
    "development_agent/lineage_repository.py",
    "models/development_dispatch.py",
    "models/development_agent.py",
    "models/development_validation.py",
    "models/source_change.py",
    "models/source_edit_planning.py",
    "models/development_handoff.py",
    "models/development_review.py",
    "models/development_baseline.py",
    "models/development_lineage.py",
    "models/development_publication.py",
    "models/source_mutation.py",
    "models/context.py",
    "models/domain.py",
    "context/assembler.py",
    "context/security.py",
    "context/budget.py",
    "model_providers/contracts.py",
    "model_providers/policy.py",
    "model_providers/tool_calls.py",
    "self_build/change_verifier.py",
    "source_edit_planning/service.py",
    "source_mutation/service.py",
    "source_change/planner.py",
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
            assembly_limits=AssemblyLimits.capture(self.app.state.context_assembler),
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

    def document(self, session, actor, workspace_row, *, depth=0):
        if depth > 7:
            refuse("DEVELOPMENT_LINEAGE_INVALID")
        stored = session.get(DevelopmentAdmissionRow, workspace_row.task_id)
        if stored is None:
            refuse("DEVELOPMENT_ADMISSION_REQUIRED")
        if stored.payload["plan"]["workspace_id"] != workspace_row.id:
            from app.development_agent.lineages import DevelopmentLineageService

            return DevelopmentLineageService(self.app).validate(
                session, actor, workspace_row, depth
            )
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
            or plan.workspace_plan_hash != self.repository.contract(workspace_row).plan.plan_hash
            or plan.workspace_approval_id != workspace_row.approval_id
            or plan.policy_digest != POLICY_DIGEST
            or plan.assembly_limits != AssemblyLimits.capture(self.app.state.context_assembler)
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
        return record, stored.integrity_digest

    def root(self, session, actor, workspace_row, authority):
        record, integrity = self.document(session, actor, workspace_row)
        plan = record.plan
        if (
            plan.attempt_id
            != self.repository.runtime(session, workspace_row.runtime_run_id).active_attempt_id
            or plan.worker_id != authority.worker_id
            or plan.lease_fingerprint != digest(authority.lease_token)
        ):
            refuse("DEVELOPMENT_ADMISSION_CONFLICT")
        self.creation.operator(
            RuntimeActorContext(actor_id=record.approved_by, stable_key="persisted-operator"),
            workspace_row,
            session,
        )
        return record, integrity

    def root_slot(self, record):
        parts = ["admission", record.plan.task_id]
        if record.plan.baseline_id is not None:
            parts.append(record.plan.workspace_id)
        return digest(parts)[:40]

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
        if phase in {"received", "planned"}:
            result.update(
                responseDigest=digest(record.response.model_dump(mode="json")),
                responseAuditId=record.response_audit_id,
            )
        if phase == "planned":
            result.update(
                editingRecordId=record.editing_binding.editing_record_id,
                editingPlanHash=record.editing_binding.editing_plan_hash,
                bindingAuditId=record.editing_binding.audit_id,
            )
        if phase == "failed":
            result.update(
                failureCategory=record.failure.category, failureAuditId=record.failure.audit_id
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
            slot, phase = self.root_slot(record), "admitted"
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
            phase = self.phase(record)
            protected_digest = self.phase_digest(record, phase)
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
                    current_digest = self.phase_digest(current, phase)
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

    def correction_baseline(self, session, root):
        if root.plan.baseline_id is None:
            return None
        from app.development_agent.lineages import DevelopmentLineageService

        actor = RuntimeActorContext(
            actor_id=root.plan.actor_id, stable_key="protected-development-owner"
        )
        return DevelopmentLineageService(self.app).baseline(session, actor, root.plan.baseline_id)[
            0
        ]

    def model_context_hash(self, session, root, context):
        baseline = self.correction_baseline(session, root)
        if baseline is None:
            return context.context_hash
        from app.development_agent.corrections import correction_context_hash

        return correction_context_hash(baseline, context, root.plan.repair_selection)

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
            or request.metadata != {"context_hash": self.model_context_hash(session, root, context)}
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
        if record.failure is not None:
            self.audit(
                session,
                record.failure.audit_id,
                row,
                "development.dispatch.failed",
                root.plan.actor_id,
                dict(dispatchId=stored.id, category=record.failure.category, outcome="unknown"),
            )
        if record.editing_binding is not None:
            binding = record.editing_binding
            editing = self.validate_editing(session, root, record, binding.editing_record_id)
            if (
                binding.editing_plan_hash != editing.plan.plan_hash
                or binding.response_digest != digest(record.response.model_dump(mode="json"))
            ):
                refuse("DEVELOPMENT_EDITING_PLAN_CONFLICT")
            self.audit(
                session,
                binding.audit_id,
                row,
                "development.dispatch.planned",
                root.plan.actor_id,
                dict(
                    dispatchId=stored.id,
                    editingRecordId=binding.editing_record_id,
                    editingPlanHash=binding.editing_plan_hash,
                    responseDigest=binding.response_digest,
                ),
            )
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
                self.root_slot(record),
                "admitted",
                digest(record.model_dump(mode="json")),
                self.root_metadata(record),
            )
        else:
            self.require_reserved(actor, record)
            reservation = record.reservation
            if record.response is not None:
                if (
                    self.checkpoint(
                        actor,
                        reservation.context.runtime_run_id,
                        reservation.context.runtime_attempt_id,
                        reservation.dispatch_id,
                        "received",
                        self.received_digest(record),
                        self.dispatch_metadata(record, "received"),
                    )[0]
                    is None
                ):
                    refuse("DEVELOPMENT_CHECKPOINT_REQUIRED")
            phase = self.phase(record)
            values = (
                reservation.context.runtime_run_id,
                reservation.context.runtime_attempt_id,
                reservation.dispatch_id,
                phase,
                self.phase_digest(record, phase),
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
                records = []
                for item in stored_records:
                    historical_row = self.repository.lookup(
                        session, item.payload["reservation"]["context"]["workspace_id"]
                    )
                    historical_root, historical_digest = self.document(
                        session, actor, historical_row
                    )
                    self.require_acknowledged(actor, historical_root, root=True)
                    records.append(
                        self.dispatch_record(
                            session, historical_row, item, historical_root, historical_digest
                        )
                    )
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
                baseline = self.correction_baseline(session, root)
                if baseline is not None and context.selection != root.plan.selection:
                    refuse("DEVELOPMENT_CORRECTION_CONTEXT_INVALID")
                request, manifest = request_builder.build_request(
                    self.app.state.context_assembler,
                    task,
                    context,
                    root,
                    dispatch_id,
                    timeout,
                    baseline=baseline,
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
                self.root(session, actor, row, authority)
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
        if proposal.context_hash != request.metadata["context_hash"]:
            refuse("DEVELOPMENT_RESPONSE_INVALID")
        # Persist known provider evidence before native source-plan interpretation.
        # A semantically invalid edit is repairable within the same root allowance;
        # it is not an uncertain physical dispatch or filesystem authority.
        return response

    @protected_storage
    def receive(self, actor, workspace_id, authority, dispatch_id, response):
        # A provider receipt acknowledges the original protected request. It is
        # never current filesystem evidence; source planning remeasures separately.
        with self.repository.leases._write() as session:
            row, _, _ = self.creation.fence(actor, workspace_id, authority, session)
            root, root_digest = self.root(session, actor, row, authority)
            self.require_acknowledged(actor, root, root=True)
            previous = self.dispatch_record(
                session, row, session.get(DevelopmentDispatchRow, dispatch_id), root, root_digest
            )
            self.require_reserved(actor, previous)
            response = self.validate_response(previous, root, response)
            if previous.failure is not None:
                refuse("DEVELOPMENT_DISPATCH_OUTCOME_UNKNOWN")
            if previous.response is not None:
                if previous.response != response:
                    refuse("DEVELOPMENT_DISPATCH_CONFLICT")
                record = previous
            else:
                self.remaining_seconds(root)
                row = self.repository.lookup(session, workspace_id)
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
                stored = session.get(DevelopmentDispatchRow, dispatch_id)
                stored.payload = record.model_dump(mode="json")
                stored.integrity_digest = digest(stored.payload)
                self.creation.fence(actor, workspace_id, authority, session)
                self.remaining_seconds(root)
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

    def received_digest(self, record):
        # Optional future phases do not rewrite the original received checkpoint.
        return digest(record.model_dump(mode="json", exclude={"failure", "editing_binding"}))

    def phase(self, record):
        if record.editing_binding is not None:
            return "planned"
        if record.failure is not None:
            return "failed"
        return "received" if record.response is not None else "reserved"

    def phase_digest(self, record, phase):
        if phase == "reserved":
            return digest(record.reservation.model_dump(mode="json"))
        if phase == "received":
            return self.received_digest(record)
        return digest(record.model_dump(mode="json"))

    @protected_storage
    def admission(self, actor, workspace_id, authority, *, advancing=False):
        with self.repository.sessions() as session:
            row, _, _ = self.creation.fence(actor, workspace_id, authority, session)
            root, _ = self.root(session, actor, row, authority)
            self.require_acknowledged(actor, root, root=True)
            if advancing:
                self.remaining_seconds(root)
            return root

    def check_in_session(
        self, actor, workspace_id, authority, dispatch_id, session, *, advancing=False
    ):
        row, _, _ = self.creation.fence(actor, workspace_id, authority, session)
        root, root_digest = self.root(session, actor, row, authority)
        self.require_acknowledged(actor, root, root=True)
        record = self.dispatch_record(
            session, row, session.get(DevelopmentDispatchRow, dispatch_id), root, root_digest
        )
        self.require_acknowledged(actor, record)
        if advancing:
            self.remaining_seconds(root)
        return root, record

    @protected_storage
    def check(self, actor, workspace_id, authority, dispatch_id, *, advancing=False):
        with self.repository.sessions() as session:
            return self.check_in_session(
                actor, workspace_id, authority, dispatch_id, session, advancing=advancing
            )

    @protected_storage
    def record_failure(self, actor, workspace_id, authority, dispatch_id, category):
        with self.repository.leases._write() as session:
            root, previous = self.check_in_session(
                actor, workspace_id, authority, dispatch_id, session
            )
            if previous.response is not None:
                refuse("DEVELOPMENT_DISPATCH_CONFLICT")
            if previous.failure is not None:
                if previous.failure.category != category:
                    refuse("DEVELOPMENT_DISPATCH_CONFLICT")
                record = previous
            else:
                row = self.repository.lookup(session, workspace_id)
                event = self.creation.git.event(
                    session,
                    row,
                    actor,
                    "development.dispatch.failed",
                    dict(dispatchId=dispatch_id, category=category, outcome="unknown"),
                )
                record = DevelopmentDispatch(
                    reservation=previous.reservation,
                    failure=DispatchFailure(
                        category=category, observed_at=datetime.now(UTC), audit_id=event.id
                    ),
                )
                stored = session.get(DevelopmentDispatchRow, dispatch_id)
                stored.payload = record.model_dump(mode="json")
                stored.integrity_digest = digest(stored.payload)
            self.creation.fence(actor, workspace_id, authority, session)
        self.repository.leases.repository.refresh_event_cursor()
        self.acknowledge(actor, authority, workspace_id, record)
        return record

    def plan_command(self, dispatch_id):
        return "development-plan-" + dispatch_id

    def validate_editing(self, session, root, record, editing_record_id):
        from app.development_agent.proposals import compile_proposal, mutation_observation
        from app.models.source_change import PlanReferences
        from app.source_change.planner import plan_source_changes
        from app.source_edit_planning.repository import SourceEditingPlanRow
        from app.source_edit_planning.service import SourceEditingPlanService

        stored = session.get(SourceEditingPlanRow, editing_record_id)
        if stored is None or record.response is None:
            refuse("DEVELOPMENT_EDITING_PLAN_REQUIRED")
        editing = SourceEditingPlanService.record(stored)
        context = record.reservation.context
        proposal = DevelopmentProposal.model_validate_json(record.response.content)
        references = PlanReferences(
            original_plan_hash=root.plan.workspace_plan_hash,
            authorization_reference=root.plan.workspace_approval_id,
        )
        baseline = self.correction_baseline(session, root)
        if baseline is None:
            intent = compile_proposal(
                proposal, context, references, allow_delete=root.plan.limits.allow_delete
            )
        else:
            from app.development_agent.corrections import compile_correction

            intent = compile_correction(
                proposal,
                baseline,
                context,
                root.plan.repair_selection,
                references,
                allow_delete=root.plan.limits.allow_delete,
                publication_source=root.plan.publication_source,
            )
        expected = plan_source_changes(
            intent, mutation_observation(context, (change.path for change in intent.changes))
        )
        if (
            editing.actor_id != root.plan.actor_id
            or editing.command_id != self.plan_command(record.reservation.dispatch_id)
            or editing.plan.workspace_id != root.plan.workspace_id
            or editing.plan.task_id != root.plan.task_id
            or editing.plan.runtime_run_id != root.plan.runtime_run_id
            or editing.plan.runtime_attempt_id != root.plan.attempt_id
            or editing.plan.workspace_plan_hash != root.plan.workspace_plan_hash
            or editing.plan.original_code_state != context.code_state
            or editing.plan.source_plan != expected
        ):
            refuse("DEVELOPMENT_EDITING_PLAN_CONFLICT")
        return editing

    @protected_storage
    def bind_editing(self, actor, workspace_id, authority, dispatch_id, editing_record_id):
        with self.repository.leases._write() as session:
            root, previous = self.check_in_session(
                actor, workspace_id, authority, dispatch_id, session, advancing=True
            )
            editing = self.validate_editing(session, root, previous, editing_record_id)
            if previous.editing_binding is not None:
                if previous.editing_binding.editing_record_id != editing_record_id:
                    refuse("DEVELOPMENT_EDITING_PLAN_CONFLICT")
                record = previous
            else:
                row = self.repository.lookup(session, workspace_id)
                payload = dict(
                    dispatchId=dispatch_id,
                    editingRecordId=editing_record_id,
                    editingPlanHash=editing.plan.plan_hash,
                    responseDigest=digest(previous.response.model_dump(mode="json")),
                )
                event = self.creation.git.event(
                    session, row, actor, "development.dispatch.planned", payload
                )
                record = DevelopmentDispatch(
                    reservation=previous.reservation,
                    response=previous.response,
                    response_audit_id=previous.response_audit_id,
                    editing_binding=EditingBinding(
                        editing_record_id=editing_record_id,
                        editing_plan_hash=editing.plan.plan_hash,
                        response_digest=payload["responseDigest"],
                        audit_id=event.id,
                    ),
                )
                stored = session.get(DevelopmentDispatchRow, dispatch_id)
                stored.payload = record.model_dump(mode="json")
                stored.integrity_digest = digest(stored.payload)
            self.creation.fence(actor, workspace_id, authority, session)
            self.remaining_seconds(root)
        self.repository.leases.repository.refresh_event_cursor()
        self.acknowledge(actor, authority, workspace_id, record)
        return record

    @protected_storage
    def editing(self, actor, workspace_id, authority, dispatch_id):
        with self.repository.sessions() as session:
            root, record = self.check_in_session(
                actor, workspace_id, authority, dispatch_id, session
            )
            if record.editing_binding is None:
                refuse("DEVELOPMENT_EDITING_PLAN_REQUIRED")
            return record, self.validate_editing(
                session, root, record, record.editing_binding.editing_record_id
            )

    @protected_storage
    def recover(self, actor, workspace_id, authority, dispatch_id):
        """Acknowledge persisted phases in order; no source read or provider recall."""
        with self.repository.sessions() as session:
            row, _, _ = self.creation.fence(actor, workspace_id, authority, session)
            root, root_digest = self.root(session, actor, row, authority)
            self.require_acknowledged(actor, root, root=True)
            record = self.dispatch_record(
                session, row, session.get(DevelopmentDispatchRow, dispatch_id), root, root_digest
            )
        reserved = DevelopmentDispatch(reservation=record.reservation)
        self.acknowledge(actor, authority, workspace_id, reserved)
        if record.response is not None:
            received = DevelopmentDispatch(
                reservation=record.reservation,
                response=record.response,
                response_audit_id=record.response_audit_id,
            )
            self.acknowledge(actor, authority, workspace_id, received)
        self.acknowledge(actor, authority, workspace_id, record)
        return self.read(actor, workspace_id, authority, dispatch_id)
