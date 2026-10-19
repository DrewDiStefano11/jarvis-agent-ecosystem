"""Fenced historical source snapshots for separately approved correction workspaces."""

from datetime import UTC, datetime

from app.agent_runtime.repository import RuntimeExecutionFence
from app.development_agent.baseline_repository import DevelopmentBaselineRow
from app.development_agent.context import RepositoryContextReader, refuse
from app.development_agent.dispatch import DevelopmentDispatchService, protected_storage
from app.development_agent.repository import DevelopmentDispatchRow
from app.models.agent_runtime import RecordCheckpointCommand
from app.models.development_baseline import (
    BaselineFile,
    BaselineRecord,
    BaselineSnapshot,
    BaselineSummary,
)
from app.models.source_mutation import ApplySourceMutation
from app.self_build.native_artifacts import mutation_artifacts
from app.self_build.policy import digest
from app.source_mutation.history import MutationHistoryReader
from app.source_mutation.inspection import MutationInspector
from app.source_mutation.repository import SourceMutationRow
from app.source_mutation.service import intent_digest


class DevelopmentBaselineService:
    def __init__(self, app):
        self.app = app
        self.dispatches = DevelopmentDispatchService(app)
        self.reader = RepositoryContextReader(app)
        self.history = MutationHistoryReader(app)
        self.inspector = MutationInspector(app)

    @staticmethod
    def identifier(dispatch_id):
        return digest(["development-baseline-v1", dispatch_id])

    @staticmethod
    def files(context, root):
        entries = {item.path: item for item in context.observation.entries}
        result = []
        for change in context.code_state.changes:
            if change.path not in root.plan.selection.paths:
                refuse("DEVELOPMENT_BASELINE_SCOPE_INCOMPLETE")
            content = None
            if change.after_hash is not None:
                entry = entries.get(change.path)
                if entry is None or entry.kind != "file" or entry.content is None:
                    refuse("DEVELOPMENT_BASELINE_SCOPE_INCOMPLETE")
                content = entry.content
            elif not root.plan.limits.allow_delete:
                refuse("DEVELOPMENT_DELETE_DENIED")
            result.append(BaselineFile(change=change, content=content))
        return tuple(result)

    def metadata(self, record):
        snapshot = record.snapshot
        return dict(
            schemaName="development-baseline-1",
            baselineId=snapshot.baseline_id,
            snapshotHash=snapshot.snapshot_hash,
            dispatchId=snapshot.dispatch_id,
            admissionDigest=snapshot.admission_digest,
            auditId=record.audit_id,
        )

    def checkpoint(self, actor, record):
        snapshot = record.snapshot
        return self.dispatches.checkpoint(
            actor,
            snapshot.context.runtime_run_id,
            snapshot.context.runtime_attempt_id,
            snapshot.baseline_id,
            "baseline",
            digest(record.model_dump(mode="json")),
            self.metadata(record),
        )

    def stored(self, session, actor, workspace_id, authority, baseline_id):
        row = session.get(DevelopmentBaselineRow, baseline_id)
        if row is None:
            refuse("DEVELOPMENT_BASELINE_REQUIRED")
        record = BaselineRecord.model_validate(row.payload)
        snapshot = record.snapshot
        root, dispatch = self.dispatches.check_in_session(
            actor,
            workspace_id,
            authority,
            snapshot.dispatch_id,
            session,
        )
        return self.validate_record(session, actor, workspace_id, root, dispatch, row, record)

    def historical(self, session, actor, baseline_id, root):
        row = session.get(DevelopmentBaselineRow, baseline_id)
        if row is None:
            refuse("DEVELOPMENT_BASELINE_REQUIRED")
        record = BaselineRecord.model_validate(row.payload)
        workspace_id = record.snapshot.context.workspace_id
        workspace = self.dispatches.repository.lookup(session, workspace_id)
        dispatch = self.dispatches.dispatch_record(
            session,
            workspace,
            session.get(DevelopmentDispatchRow, record.snapshot.dispatch_id),
            root,
            digest(root.model_dump(mode="json")),
        )
        self.dispatches.require_acknowledged(actor, root, root=True)
        self.dispatches.require_acknowledged(actor, dispatch)
        result = self.validate_record(session, actor, workspace_id, root, dispatch, row, record)
        if self.checkpoint(actor, result)[0] is None:
            refuse("DEVELOPMENT_CHECKPOINT_REQUIRED")
        return result

    def validate_record(self, session, actor, workspace_id, root, dispatch, row, record):
        snapshot = record.snapshot
        baseline_id = snapshot.baseline_id
        binding = dispatch.editing_binding
        context = snapshot.context
        source = self.history.read(actor, snapshot.editing_record_id)
        mutation = source.record
        if (
            digest(row.payload) != row.integrity_digest
            or baseline_id != snapshot.baseline_id
            or baseline_id != self.identifier(snapshot.dispatch_id)
            or row.task_id != snapshot.task_id
            or row.dispatch_id != snapshot.dispatch_id
            or snapshot.task_id != root.plan.task_id
            or snapshot.actor_id != root.plan.actor_id
            or snapshot.admission_digest != digest(root.model_dump(mode="json"))
            or binding is None
            or binding.editing_record_id != snapshot.editing_record_id
            or binding.editing_plan_hash != snapshot.editing_plan_hash
            or snapshot.snapshot_hash
            != digest(snapshot.model_dump(mode="json", exclude={"snapshot_hash"}))
            or context.context_hash
            != digest(context.model_dump(mode="json", exclude={"context_hash"}))
            or context.task_id != root.plan.task_id
            or context.workspace_id != root.plan.workspace_id
            or context.runtime_run_id != root.plan.runtime_run_id
            or context.runtime_attempt_id != root.plan.attempt_id
            or context.selection != root.plan.selection
            or context.creation_operation_id != root.plan.creation_operation_id
            or context.creation_plan_hash != root.plan.creation_plan_hash
            or snapshot.files != self.files(context, root)
            or snapshot.captured_at < snapshot.native_verification.source_inspection.measured_at
            or snapshot.native_verification.source_inspection.code_state != context.code_state
            or not snapshot.native_verification.source_inspection.matches_completed_mutation
            or snapshot.native_verification.source_inspection.checkpoint_id
            != mutation.checkpoint_id
            or snapshot.native_verification.source_inspection.operation_id != mutation.operation_id
            or snapshot.mutation_operation_id != mutation.operation_id
            or snapshot.mutation_checkpoint_id != mutation.checkpoint_id
            or snapshot.mutation_integrity_digest != intent_digest(mutation)
            or mutation.state != "complete"
            or not source.native_checkpoint_present
            or not source.journal_checkpoint_acknowledged
        ):
            refuse("DEVELOPMENT_BASELINE_CONFLICT")
        workspace = self.dispatches.repository.lookup(session, workspace_id)
        self.dispatches.audit(
            session,
            record.audit_id,
            workspace,
            "development.baseline.captured",
            actor.actor_id,
            dict(
                baselineId=baseline_id,
                snapshotHash=snapshot.snapshot_hash,
                dispatchId=snapshot.dispatch_id,
                mutationCheckpointId=snapshot.mutation_checkpoint_id,
            ),
        )
        return record

    @protected_storage
    def recover(self, actor, workspace_id, authority, baseline_id):
        with self.dispatches.repository.sessions() as session:
            record = self.stored(session, actor, workspace_id, authority, baseline_id)
        checkpoint, identifier = self.checkpoint(actor, record)
        if checkpoint is None:
            runtime = self.dispatches.runtime
            run = runtime.read_run_authorized(record.snapshot.context.runtime_run_id, actor)

            def guard(session):
                if self.stored(session, actor, workspace_id, authority, baseline_id) != record:
                    refuse("DEVELOPMENT_BASELINE_CONFLICT")

            runtime.handle_authorized(
                RecordCheckpointCommand(
                    run_id=record.snapshot.context.runtime_run_id,
                    command_id="ack-" + identifier,
                    expected_run_version=run.version,
                    timestamp=datetime.now(UTC),
                    checkpoint_id=identifier,
                    attempt_id=record.snapshot.context.runtime_attempt_id,
                    state_reference="development:" + baseline_id,
                    integrity_digest="sha256:" + digest(record.model_dump(mode="json")),
                    resume_cursor="baseline",
                    checkpoint_metadata=self.metadata(record),
                ),
                actor,
                execution_fence=RuntimeExecutionFence(
                    task_id=record.snapshot.task_id,
                    worker_id=authority.worker_id,
                    lease_token=authority.lease_token,
                ),
                commit_guard=guard,
            )
        if self.checkpoint(actor, record)[0] is None:
            refuse("DEVELOPMENT_CHECKPOINT_REQUIRED")
        return record

    @protected_storage
    def capture(self, actor, workspace_id, authority, dispatch_id, mutation_request):
        mutation_request = ApplySourceMutation.model_validate(
            mutation_request.model_dump(mode="json")
        )
        if (
            mutation_request.worker_id != authority.worker_id
            or mutation_request.lease_token != authority.lease_token
        ):
            refuse("DEVELOPMENT_MUTATION_LINEAGE_INVALID")
        baseline_id = self.identifier(dispatch_id)
        with self.dispatches.repository.sessions() as session:
            exists = session.get(DevelopmentBaselineRow, baseline_id) is not None
        if exists:
            # This is a historical replay, never a new assertion of current bytes.
            record = self.recover(actor, workspace_id, authority, baseline_id)
            if (
                mutation_request.editing_record_id != record.snapshot.editing_record_id
                or mutation_request.expected_plan_hash
                != record.snapshot.native_verification.source_inspection.mutation_plan_hash
            ):
                refuse("DEVELOPMENT_MUTATION_LINEAGE_INVALID")
            return record
        root, dispatch = self.dispatches.check(actor, workspace_id, authority, dispatch_id)
        _, editing = self.dispatches.editing(actor, workspace_id, authority, dispatch_id)
        mutation_request = ApplySourceMutation.model_validate(
            mutation_request.model_dump(mode="json")
        )
        if (
            mutation_request.editing_record_id != editing.record_id
            or mutation_request.worker_id != authority.worker_id
            or mutation_request.lease_token != authority.lease_token
        ):
            refuse("DEVELOPMENT_MUTATION_LINEAGE_INVALID")
        history = self.history.read(actor, editing.record_id)
        native = self.inspector.verify_artifacts(actor, mutation_request)
        artifacts = mutation_artifacts(self.app, actor, editing)
        if native.source_inspection.code_state != editing.plan.expected_code_state:
            refuse("DEVELOPMENT_SOURCE_STATE_CONFLICT")

        def finalize(context, guard):
            if context.code_state != native.source_inspection.code_state:
                refuse("DEVELOPMENT_SOURCE_STATE_CONFLICT")
            fields = dict(
                schema_version="1.0",
                baseline_id=baseline_id,
                admission_digest=digest(root.model_dump(mode="json")),
                dispatch_id=dispatch_id,
                task_id=root.plan.task_id,
                actor_id=actor.actor_id,
                editing_record_id=editing.record_id,
                editing_plan_hash=editing.plan.plan_hash,
                mutation_operation_id=history.record.operation_id,
                mutation_checkpoint_id=history.record.checkpoint_id,
                mutation_integrity_digest=intent_digest(history.record),
                context=context.model_dump(mode="json"),
                native_verification=native.model_dump(mode="json"),
                files=[item.model_dump(mode="json") for item in self.files(context, root)],
                captured_at=datetime.now(UTC),
                current_source_measured=False,
                execution_authorized=False,
            )
            snapshot = BaselineSnapshot(**fields, snapshot_hash="0" * 64)
            snapshot = snapshot.model_copy(
                update={
                    "snapshot_hash": digest(
                        snapshot.model_dump(mode="json", exclude={"snapshot_hash"})
                    )
                }
            )
            with self.dispatches.repository.leases._write() as session:
                live_root, live_dispatch = self.dispatches.check_in_session(
                    actor,
                    workspace_id,
                    authority,
                    dispatch_id,
                    session,
                )
                if (live_root, live_dispatch) != (root, dispatch):
                    refuse("DEVELOPMENT_BASELINE_CONFLICT")
                row, plan, _, _, _ = self.history.service.fence(actor, mutation_request, session)
                current = self.history.service.intent(
                    session.get(SourceMutationRow, editing.record_id), plan, session
                )
                if current != history.record:
                    refuse("DEVELOPMENT_BASELINE_CONFLICT")
                existing = session.get(DevelopmentBaselineRow, baseline_id)
                if existing is not None:
                    record = self.stored(session, actor, workspace_id, authority, baseline_id)
                    if record.snapshot.context != context:
                        refuse("DEVELOPMENT_BASELINE_CONFLICT")
                else:
                    event = self.dispatches.creation.git.event(
                        session,
                        row,
                        actor,
                        "development.baseline.captured",
                        dict(
                            baselineId=baseline_id,
                            snapshotHash=snapshot.snapshot_hash,
                            dispatchId=dispatch_id,
                            mutationCheckpointId=snapshot.mutation_checkpoint_id,
                        ),
                    )
                    record = BaselineRecord(snapshot=snapshot, audit_id=event.id)
                    payload = record.model_dump(mode="json")
                    if len(record.model_dump_json().encode()) > 16 * 1024 * 1024:
                        refuse("DEVELOPMENT_BASELINE_LIMIT")
                    session.add(
                        DevelopmentBaselineRow(
                            id=baseline_id,
                            dispatch_id=dispatch_id,
                            task_id=root.plan.task_id,
                            payload=payload,
                            integrity_digest=digest(payload),
                        )
                    )
                guard.in_transaction(session)
                self.history.service.fence(actor, mutation_request, session)
            return record

        self.reader.inspect(
            actor,
            workspace_id,
            authority,
            root.plan.selection,
            native_artifacts=artifacts.artifacts,
            finalize=finalize,
        )
        self.dispatches.repository.leases.repository.refresh_event_cursor()
        return self.recover(actor, workspace_id, authority, baseline_id)

    @staticmethod
    def summary(record):
        snapshot = record.snapshot
        return BaselineSummary(
            baseline_id=snapshot.baseline_id,
            snapshot_hash=snapshot.snapshot_hash,
            dispatch_id=snapshot.dispatch_id,
            task_id=snapshot.task_id,
            workspace_id=snapshot.context.workspace_id,
            mutation_checkpoint_id=snapshot.mutation_checkpoint_id,
            code_state_hash=digest(snapshot.context.code_state.model_dump(mode="json")),
            captured_at=snapshot.captured_at,
        )
