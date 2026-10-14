"""Independent exact-hash workspace lineage with immutable task budget origin."""

from datetime import UTC, datetime

from sqlalchemy import select

from app.development_agent.baseline_repository import DevelopmentBaselineRow
from app.development_agent.baselines import DevelopmentBaselineService
from app.development_agent.context import RepositoryContextReader, refuse
from app.development_agent.dispatch import DevelopmentDispatchService, protected_storage
from app.development_agent.lineage_repository import DevelopmentLineageRow
from app.development_agent.publication import read_anchor, verify_destination
from app.development_agent.repository import DevelopmentAdmissionRow, DevelopmentDispatchRow
from app.models.development_baseline import BaselineRecord
from app.models.development_dispatch import DevelopmentAdmission
from app.models.development_lineage import LineagePlan, LineageRecord
from app.self_build.policy import digest


class DevelopmentLineageService:
    def __init__(self, app):
        self.app = app
        self.dispatches = DevelopmentDispatchService(app)
        self.baselines = DevelopmentBaselineService(app)
        self.reader = RepositoryContextReader(app)

    def origin(self, session, actor, task_id):
        stored = session.get(DevelopmentAdmissionRow, task_id)
        if stored is None:
            refuse("DEVELOPMENT_ADMISSION_REQUIRED")
        workspace = self.dispatches.repository.lookup(
            session, stored.payload["plan"]["workspace_id"]
        )
        return self.dispatches.document(session, actor, workspace)

    def baseline(self, session, actor, baseline_id, *, depth=0):
        row = session.get(DevelopmentBaselineRow, baseline_id)
        if row is None:
            refuse("DEVELOPMENT_BASELINE_REQUIRED")
        snapshot = BaselineRecord.model_validate(row.payload).snapshot
        workspace = self.dispatches.repository.lookup(session, snapshot.context.workspace_id)
        parent, parent_digest = self.dispatches.document(session, actor, workspace, depth=depth)
        record = self.baselines.historical(session, actor, baseline_id, parent)
        return record.snapshot, parent, parent_digest

    def validate(self, session, actor, workspace, depth):
        row = session.get(DevelopmentLineageRow, workspace.id)
        if row is None:
            refuse("DEVELOPMENT_LINEAGE_REQUIRED")
        record = LineageRecord.model_validate(row.payload)
        plan, admission = record.plan, record.admission
        origin, origin_digest = self.origin(session, actor, row.task_id)
        baseline, parent, parent_digest = self.baseline(
            session, actor, admission.plan.baseline_id, depth=depth + 1
        )
        if admission.plan.publication_source is not None:
            anchor = read_anchor(
                self.app, actor, baseline, admission.plan.publication_source.reference
            )
            if anchor != admission.plan.publication_source:
                refuse("DEVELOPMENT_PUBLICATION_LINK_CONFLICT")
        previous = session.get(DevelopmentLineageRow, baseline.context.workspace_id)
        expected_ordinal = (previous.ordinal if previous is not None else 0) + 1
        current = admission.plan
        immutable = (
            "task_id",
            "task_fingerprint",
            "actor_id",
            "selection",
            "limits",
            "policy_digest",
        )
        creation, _, _ = self.dispatches.creation.intent(workspace)
        if (
            digest(row.payload) != row.integrity_digest
            or row.ordinal != plan.ordinal
            or plan.ordinal != expected_ordinal
            or plan.plan_hash != digest(plan.model_dump(mode="json", exclude={"plan_hash"}))
            or current.plan_hash != digest(current.model_dump(mode="json", exclude={"plan_hash"}))
            or plan.admission_plan != current
            or plan.origin_admission_digest != origin_digest
            or current.origin_admission_digest != origin_digest
            or plan.parent_admission_digest != parent_digest
            or baseline.snapshot_hash != current.baseline_snapshot_hash
            or baseline.task_id != current.task_id
            or any(getattr(current, key) != getattr(origin.plan, key) for key in immutable)
            or current.workspace_id != workspace.id
            or workspace.task_id != origin.plan.task_id
            or current.runtime_run_id != workspace.runtime_run_id
            or current.workspace_plan_hash
            != self.dispatches.repository.contract(workspace).plan.plan_hash
            or current.workspace_approval_id != workspace.approval_id
            or current.creation_operation_id != creation.operation_id
            or current.creation_plan_hash != creation.plan.plan_hash
            or current.attempt_id != creation.attempt_id
            or current.baseline_id != baseline.baseline_id
            or not set(current.repair_selection.paths).issubset(origin.plan.selection.paths)
            or admission.admitted_at != origin.admitted_at
            or admission.approved_by == current.actor_id
        ):
            refuse("DEVELOPMENT_LINEAGE_INVALID")
        self.dispatches.audit(
            session,
            admission.approval_id,
            workspace,
            "development.admitted",
            admission.approved_by,
            self.dispatches.approval_payload(current),
        )
        self.dispatches.audit(
            session,
            record.approval_audit_id,
            workspace,
            "development.lineage.approved",
            admission.approved_by,
            dict(
                planHash=plan.plan_hash,
                admissionDigest=digest(admission.model_dump(mode="json")),
                baselineId=current.baseline_id,
                baselineSnapshotHash=current.baseline_snapshot_hash,
            ),
        )
        return admission, digest(admission.model_dump(mode="json"))

    def plan(self, session, actor, context, authority, request):
        origin, origin_digest = self.origin(session, actor, context.task_id)
        baseline, parent, parent_digest = self.baseline(session, actor, request.baseline_id)
        self.dispatches.remaining_seconds(origin)
        anchor = (
            read_anchor(self.app, actor, baseline, request.publication_source)
            if request.publication_source is not None
            else None
        )
        verify_destination(baseline, context, anchor)
        rows = session.scalars(
            select(DevelopmentLineageRow)
            .where(DevelopmentLineageRow.task_id == context.task_id)
            .order_by(DevelopmentLineageRow.ordinal)
        ).all()
        if [row.ordinal for row in rows] != list(range(1, len(rows) + 1)):
            refuse("DEVELOPMENT_LINEAGE_INVALID")
        latest = rows[-1].workspace_id if rows else origin.plan.workspace_id
        if (
            baseline.context.workspace_id != latest
            or context.workspace_id == latest
            or baseline.task_id != context.task_id
            or baseline.actor_id != actor.actor_id
            or not set(request.repair_selection.paths).issubset(origin.plan.selection.paths)
            or context.selection != origin.plan.selection
            or context.code_state.changes
            or context.code_state.repository_identity
            != baseline.context.code_state.repository_identity
            or len(rows) >= origin.plan.limits.maximum_dispatches - 1
        ):
            refuse("DEVELOPMENT_CORRECTION_CONTEXT_INVALID")
        records = session.scalars(
            select(DevelopmentDispatchRow)
            .where(DevelopmentDispatchRow.task_id == context.task_id)
            .order_by(DevelopmentDispatchRow.ordinal)
        ).all()
        for stored in records:
            old_workspace = self.dispatches.repository.lookup(
                session, stored.payload["reservation"]["context"]["workspace_id"]
            )
            old_root, old_digest = self.dispatches.document(session, actor, old_workspace)
            dispatch = self.dispatches.dispatch_record(
                session, old_workspace, stored, old_root, old_digest
            )
            self.dispatches.require_acknowledged(actor, dispatch)
            if dispatch.response is None:
                refuse("DEVELOPMENT_DISPATCH_OUTCOME_UNKNOWN")
        if (
            len(records) >= origin.plan.limits.maximum_dispatches
            or (len(records) + 1) * origin.plan.limits.output_tokens_per_dispatch
            > origin.plan.limits.maximum_reserved_output_tokens
        ):
            refuse("DEVELOPMENT_BUDGET_EXHAUSTED")
        _, initial = self.dispatches.plan(session, actor, authority, context, origin.plan.limits)
        fields = initial.model_dump(mode="json", exclude={"plan_hash"})
        fields.update(
            origin_admission_digest=origin_digest,
            baseline_id=baseline.baseline_id,
            baseline_snapshot_hash=baseline.snapshot_hash,
            repair_selection=request.repair_selection.model_dump(mode="json"),
        )
        if anchor is not None:
            fields["publication_source"] = anchor.model_dump(mode="json")
        from app.models.development_dispatch import AdmissionPlan

        effective = AdmissionPlan(**fields, plan_hash=digest(fields))
        fields = dict(
            schema_version="1.0",
            ordinal=len(rows) + 1,
            origin_admission_digest=origin_digest,
            parent_admission_digest=parent_digest,
            admission_plan=effective.model_dump(mode="json"),
        )
        return origin, LineagePlan(**fields, plan_hash=digest(fields))

    @protected_storage
    def preview(self, actor, workspace_id, request):
        with self.dispatches.repository.sessions() as session:
            row = self.dispatches.repository.lookup(session, workspace_id)
            origin, _ = self.origin(session, actor, row.task_id)
        return self.reader.inspect(
            actor,
            workspace_id,
            request.authority,
            origin.plan.selection,
            finalize=lambda context, guard: self._preview(actor, context, request),
        )

    def _preview(self, actor, context, request):
        with self.dispatches.repository.sessions() as session:
            return self.plan(session, actor, context, request.authority, request)[1]

    @protected_storage
    def approve(self, operator, actor, workspace_id, request):
        with self.dispatches.repository.sessions() as session:
            existing = session.get(DevelopmentLineageRow, workspace_id)
            if existing is not None:
                record = LineageRecord.model_validate(existing.payload)
                if (
                    record.plan.plan_hash != request.expected_plan_hash
                    or record.admission.approved_by != operator.actor_id
                    or record.admission.plan.baseline_id != request.baseline_id
                    or record.admission.plan.repair_selection != request.repair_selection
                    or (
                        record.admission.plan.publication_source.reference
                        if record.admission.plan.publication_source
                        else None
                    )
                    != request.publication_source
                ):
                    refuse("DEVELOPMENT_PLAN_CHANGED")
                replay = True
            else:
                replay = False
            row = self.dispatches.repository.lookup(session, workspace_id)
            origin, _ = self.origin(session, actor, row.task_id)
        if replay:
            return self.recover(actor, workspace_id, request.authority)

        def finalize(context, guard):
            with self.dispatches.repository.leases._write() as session:
                row, _, _ = self.dispatches.creation.fence(
                    actor, workspace_id, request.authority, session
                )
                self.dispatches.creation.operator(operator, row, session)
                origin, plan = self.plan(session, actor, context, request.authority, request)
                if plan.plan_hash != request.expected_plan_hash:
                    refuse("DEVELOPMENT_PLAN_CHANGED")
                event = self.dispatches.creation.git.event(
                    session,
                    row,
                    operator,
                    "development.admitted",
                    self.dispatches.approval_payload(plan.admission_plan),
                )
                admission = DevelopmentAdmission(
                    plan=plan.admission_plan,
                    approval_id=event.id,
                    approved_by=operator.actor_id,
                    admitted_at=origin.admitted_at,
                )
                approval = self.dispatches.creation.git.event(
                    session,
                    row,
                    operator,
                    "development.lineage.approved",
                    dict(
                        planHash=plan.plan_hash,
                        admissionDigest=digest(admission.model_dump(mode="json")),
                        baselineId=request.baseline_id,
                        baselineSnapshotHash=plan.admission_plan.baseline_snapshot_hash,
                    ),
                )
                record = LineageRecord(
                    plan=plan,
                    admission=admission,
                    approval_audit_id=approval.id,
                    approved_at=datetime.now(UTC),
                )
                payload = record.model_dump(mode="json")
                session.add(
                    DevelopmentLineageRow(
                        workspace_id=workspace_id,
                        task_id=context.task_id,
                        ordinal=plan.ordinal,
                        payload=payload,
                        integrity_digest=digest(payload),
                    )
                )
                guard.in_transaction(session)
                self.dispatches.remaining_seconds(origin)
            return record

        self.reader.inspect(
            actor, workspace_id, request.authority, origin.plan.selection, finalize=finalize
        )
        self.dispatches.repository.leases.repository.refresh_event_cursor()
        return self.recover(actor, workspace_id, request.authority)

    @protected_storage
    def recover(self, actor, workspace_id, authority):
        with self.dispatches.repository.sessions() as session:
            row, _, _ = self.dispatches.creation.fence(actor, workspace_id, authority, session)
            admission, _ = self.dispatches.root(session, actor, row, authority)
            record = LineageRecord.model_validate(
                session.get(DevelopmentLineageRow, workspace_id).payload
            )
        self.dispatches.acknowledge(actor, authority, workspace_id, admission, root=True)
        return record
