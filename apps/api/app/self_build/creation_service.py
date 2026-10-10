"""Durable approved native checkout creation using runtime checkpoints and fences."""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from secrets import token_hex
from uuid import uuid4

from app.agent_runtime.authorization import RuntimeActorContext
from app.agent_runtime.repository import RuntimeExecutionFence
from app.core.errors import DomainError
from app.db.models import AuditEventRow
from app.models import workspace_creation as contracts
from app.models.agent_runtime import RecordCheckpointCommand
from app.models.workspace_creation import (
    WorkspaceCreationApproval,
    WorkspaceCreationPlan,
    WorkspaceCreationRecord,
)
from app.runtime_supervisor import windows_job
from app.self_build import checkout_driver, materialization, owned_process, workspace_namespace
from app.self_build.git_service import INSPECTION_POLICY_DIGEST
from app.self_build.policy import digest

CREATION_POLICY_DIGEST = sha256(
    (
        Path(__file__).read_text(encoding="utf-8")
        + Path(contracts.__file__).read_text(encoding="utf-8")
        + Path(owned_process.__file__).read_text(encoding="utf-8")
        + Path(checkout_driver.__file__).read_text(encoding="utf-8")
        + Path(materialization.__file__).read_text(encoding="utf-8")
        + Path(workspace_namespace.__file__).read_text(encoding="utf-8")
        + Path(windows_job.__file__).read_text(encoding="utf-8")
        + INSPECTION_POLICY_DIGEST
    ).encode()
).hexdigest()


def fail(code, message, status=409):
    raise DomainError(code, message, status)


def ownership(record, nonce):
    return dict(
        workspaceId=record.workspace_id,
        operationId=record.operation_id,
        planHash=record.plan.plan_hash,
        nonce=nonce,
    )


def intent_digest(record, nonce):
    payload = record.model_dump(mode="json", exclude={"checkpoint_id", "updated_at"})
    if payload["state"] == "finalizing":
        payload["state"] = "ready"
    return digest(
        {
            "record": payload,
            "nonce": nonce,
        }
    )


def persisted_intent_digest(payload, nonce):
    # Preserve the original serialized prepared schema from #93. New optional
    # fields/defaults must not invalidate historical records after an upgrade.
    state = {
        key: value for key, value in payload.items() if key not in {"checkpoint_id", "updated_at"}
    }
    if state["state"] == "finalizing":
        state["state"] = "ready"
    return digest(dict(record=state, nonce=nonce))


class WorkspaceCreationService:
    def __init__(self, app):
        self.app = app
        self.git = app.state.self_build_git_service
        self.workspace = self.git.workspace
        self.repository = self.git.repository
        self.runtime = app.state.agent_runtime_service

    def permission(self, actor, row, session):
        decision = self.workspace.identity.check_permission_resource_access(
            actor.actor_id, "self_build.workspace.materialize", "task", row.task_id, session=session
        )
        if not decision.allowed:
            fail(
                "SELF_BUILD_CREATION_PERMISSION_DENIED", "Workspace materialization is denied.", 403
            )

    def plan_in_session(self, actor, workspace_id, inspection_id, session):
        row, inspection_plan = self.git.plan_in_session(actor, workspace_id, session)
        inspection = self.git.read(actor, workspace_id, inspection_id)
        if inspection.plan != inspection_plan:
            fail(
                "SELF_BUILD_REINSPECTION_REQUIRED",
                "Inspect the repository under current policy before creation.",
            )
        reservation = self.repository.contract(row)
        payload = dict(
            workspace_id=row.id,
            workspace_version=row.version,
            workspace=reservation.plan,
            inspection_id=inspection_id,
            base_tree_sha=inspection.observation.base_tree_sha,
            inventory_digest=inspection.observation.inventory_digest,
            file_count=inspection.observation.file_count,
            tool_identity_digest=inspection.plan.tool_identity_digest,
            tool_sha256=inspection.plan.tool_sha256,
            creation_policy_digest=CREATION_POLICY_DIGEST,
        )
        provisional = WorkspaceCreationPlan(**payload, plan_hash="0" * 64)
        return row, provisional.model_copy(
            update={"plan_hash": digest(provisional.model_dump(mode="json", exclude={"plan_hash"}))}
        )

    def preview(self, actor, workspace_id, inspection_id):
        with self.repository.sessions() as session:
            return self.plan_in_session(actor, workspace_id, inspection_id, session)[1]

    def operator(self, actor, row, session):
        self.git.operator(actor, row, session)
        self.permission(actor, row, session)
        if self.app.state.remote_control_service.access.actor_id != actor.actor_id:
            fail("SELF_BUILD_APPROVAL_REQUIRED", "Use the configured authenticated operator.", 403)

    def approve(self, actor, request):
        with self.repository.leases._write() as session:
            row, plan = self.plan_in_session(
                actor, request.workspace_id, request.inspection_id, session
            )
            self.operator(actor, row, session)
            if plan.plan_hash != request.expected_plan_hash:
                fail("SELF_BUILD_PLAN_CHANGED", "Approve the exact creation plan.")
            expires = datetime.now(UTC) + timedelta(seconds=request.valid_for_seconds)
            event = self.git.event(
                session,
                row,
                actor,
                "self_build.workspace_creation.approved",
                {"plan": plan.model_dump(mode="json"), "expiresAt": expires.isoformat()},
            )
            result = WorkspaceCreationApproval(
                approval_id=event.id, plan=plan, approved_by=actor.actor_id, expires_at=expires
            )
        self.repository.leases.repository.refresh_event_cursor()
        return result

    def fence(self, actor, workspace_id, request, session):
        row, plan = self.plan_in_session(actor, workspace_id, request.inspection_id, session)
        self.workspace.authorize(actor, row.task_id, session, write=True)
        self.permission(actor, row, session)
        if request.expected_plan_hash != plan.plan_hash:
            fail("SELF_BUILD_PLAN_CHANGED", "Creation authority changed.")
        self.repository.leases._require_lease(
            session, row.task_id, request.worker_id, request.lease_token, datetime.now(UTC)
        )
        if (
            request.worker_id != row.worker_id
            or digest(request.lease_token) != row.lease_fingerprint
        ):
            fail("SELF_BUILD_RECOVERY_REQUIRED", "Creation requires the original owner lease.")
        run = self.repository.runtime(session, row.runtime_run_id)
        if run.state != "running" or not run.active_attempt_id:
            fail(
                "SELF_BUILD_CREATION_ATTEMPT_REQUIRED",
                "Creation requires a running native attempt.",
            )
        if self.repository.executor(session, run) != request.worker_id:
            fail("SELF_BUILD_EXECUTOR_MISMATCH", "Creation requires the original runtime executor.")
        event = session.get(AuditEventRow, request.approval_id)
        try:
            payload = event.payload["payload"]
            expires = datetime.fromisoformat(payload["expiresAt"])
            valid = (
                event.event_type == "self_build.workspace_creation.approved"
                and event.task_id == row.task_id
                and event.actor != actor.actor_id
                and WorkspaceCreationPlan.model_validate(payload["plan"]) == plan
                and expires.tzinfo is not None
                and expires > datetime.now(UTC)
            )
        except (AttributeError, KeyError, TypeError, ValueError):
            valid = False
        if not valid:
            fail(
                "SELF_BUILD_APPROVAL_REQUIRED", "Current exact operator approval is required.", 403
            )
        self.operator(
            RuntimeActorContext(actor_id=event.actor, stable_key="persisted-operator"), row, session
        )
        return row, plan, run.active_attempt_id

    def intent(self, row):
        try:
            private = row.creation_json
            if set(private) != {"record", "nonce", "digest"}:
                raise ValueError("unexpected intent fields")
            record = WorkspaceCreationRecord.model_validate(private["record"])
            nonce = private["nonce"]
            if (
                len(nonce) != 64
                or any(c not in "0123456789abcdef" for c in nonce)
                or record.workspace_id != row.id
                or record.worker_id != row.worker_id
                or record.plan.workspace != self.repository.contract(row).plan
                or digest(
                    {
                        key: value
                        for key, value in private["record"]["plan"].items()
                        if key != "plan_hash"
                    }
                )
                != record.plan.plan_hash
                or record.ownership_digest != digest(ownership(record, nonce))
                or private["digest"] != persisted_intent_digest(private["record"], nonce)
            ):
                raise ValueError("intent integrity failed")
            return record, nonce, private["digest"]
        except (AttributeError, KeyError, TypeError, ValueError):
            fail("SELF_BUILD_CREATION_RECORD_INVALID", "Private creation intent integrity failed.")

    def checkpoint(self, actor, record, expected_digest):
        phase = "ready" if record.state == "finalizing" else record.state
        cursor = (
            f"materializing-{record.completed_file_count}" if phase == "materializing" else phase
        )
        identifier = f"checkpoint-{record.operation_id}-{cursor}"
        metadata = dict(
            workspaceId=record.workspace_id,
            operationId=record.operation_id,
            planHash=record.plan.plan_hash,
            ownershipDigest=record.ownership_digest,
            phase=phase,
        )
        if phase != "prepared":
            metadata.update(
                completedFileCount=record.completed_file_count,
                registrationDigest=record.registration_digest,
                sourceDigest=record.source_digest,
            )
        matches = [
            item
            for item in self.runtime.checkpoints_authorized(
                record.plan.workspace.runtime_run_id, actor
            )
            if item.checkpoint_id == identifier
        ]
        if not matches:
            return None, identifier, metadata
        checkpoint = matches[0]
        if (
            len(matches) != 1
            or checkpoint.run_id != record.plan.workspace.runtime_run_id
            or checkpoint.attempt_id != record.attempt_id
            or checkpoint.integrity_digest != "sha256:" + expected_digest
            or checkpoint.state_reference != "workspace-creation:" + record.operation_id
            or checkpoint.resume_cursor != cursor
            or checkpoint.metadata != metadata
        ):
            fail(
                "SELF_BUILD_CREATION_CHECKPOINT_INVALID",
                "Native checkpoint does not acknowledge the durable intent.",
            )
        return checkpoint, identifier, metadata

    def prepare(self, actor, workspace_id, request):
        with self.repository.leases._write() as session:
            row, plan, attempt = self.fence(actor, workspace_id, request, session)
            if row.creation_json is None:
                nonce, operation_id = token_hex(32), "workspace-create-" + uuid4().hex
                now = datetime.now(UTC)
                record = WorkspaceCreationRecord(
                    operation_id=operation_id,
                    workspace_id=row.id,
                    plan=plan,
                    approval_id=request.approval_id,
                    worker_id=request.worker_id,
                    attempt_id=attempt,
                    state="prepared",
                    checkpoint_id=None,
                    ownership_digest="0" * 64,
                    completed_file_count=0,
                    created_at=now,
                    updated_at=now,
                )
                record = record.model_copy(
                    update={"ownership_digest": digest(ownership(record, nonce))}
                )
                private_digest = intent_digest(record, nonce)
                row.creation_json = {
                    "record": record.model_dump(mode="json"),
                    "nonce": nonce,
                    "digest": private_digest,
                }
                self.git.event(
                    session,
                    row,
                    actor,
                    "self_build.workspace_creation.prepared",
                    {"record": record.model_dump(mode="json"), "intentDigest": private_digest},
                )
            else:
                record, nonce, private_digest = self.intent(row)
                if (
                    record.plan != plan
                    or record.attempt_id != attempt
                    or record.worker_id != request.worker_id
                    or record.state != "prepared"
                ):
                    fail(
                        "SELF_BUILD_CREATION_CONFLICT",
                        "Recover the existing creation operation explicitly.",
                    )
        self.repository.leases.repository.refresh_event_cursor()
        return self.acknowledge(actor, workspace_id, request, record, private_digest)

    def acknowledge(self, actor, workspace_id, request, record, private_digest):
        plan = record.plan
        existing, identifier, metadata = self.checkpoint(actor, record, private_digest)
        if record.checkpoint_id is not None and (
            existing is None or record.checkpoint_id != identifier
        ):
            fail(
                "SELF_BUILD_CREATION_CHECKPOINT_INVALID",
                "Acknowledged native checkpoint is missing.",
            )
        if existing is None:
            snapshot = self.runtime.read_run_authorized(plan.workspace.runtime_run_id, actor)

            def guard(session):
                current, _, current_attempt = self.fence(actor, workspace_id, request, session)
                current_record, _, current_digest = self.intent(current)
                if (
                    current_digest != private_digest
                    or current_attempt != record.attempt_id
                    or current_record.operation_id != record.operation_id
                ):
                    fail(
                        "SELF_BUILD_CREATION_CONFLICT",
                        "Creation intent changed before checkpoint acknowledgement.",
                    )

            self.runtime.handle_authorized(
                RecordCheckpointCommand(
                    run_id=plan.workspace.runtime_run_id,
                    command_id="ack-" + identifier,
                    expected_run_version=snapshot.version,
                    timestamp=datetime.now(UTC),
                    checkpoint_id=identifier,
                    attempt_id=record.attempt_id,
                    state_reference="workspace-creation:" + record.operation_id,
                    integrity_digest="sha256:" + private_digest,
                    resume_cursor=identifier.removeprefix(f"checkpoint-{record.operation_id}-"),
                    checkpoint_metadata=metadata,
                ),
                actor,
                execution_fence=RuntimeExecutionFence(
                    task_id=plan.workspace.task_id,
                    worker_id=request.worker_id,
                    lease_token=request.lease_token,
                ),
                commit_guard=guard,
            )
            existing, _, _ = self.checkpoint(actor, record, private_digest)
            if existing is None:
                fail(
                    "SELF_BUILD_CREATION_CHECKPOINT_INVALID",
                    "Native checkpoint was not acknowledged.",
                )
        with self.repository.leases._write() as session:
            row, _, attempt = self.fence(actor, workspace_id, request, session)
            current, nonce, current_digest = self.intent(row)
            if current_digest != private_digest or current.attempt_id != attempt:
                fail(
                    "SELF_BUILD_CREATION_CONFLICT",
                    "Creation lineage changed before acknowledgement.",
                )
            if current.checkpoint_id is None:
                current = current.model_copy(
                    update={
                        "checkpoint_id": identifier,
                        "updated_at": datetime.now(UTC),
                        "state": "ready" if current.state == "finalizing" else current.state,
                    }
                )
                row.creation_json = {
                    "record": current.model_dump(mode="json"),
                    "nonce": nonce,
                    "digest": private_digest,
                }
                self.git.event(
                    session,
                    row,
                    actor,
                    "self_build.workspace_creation.checkpoint_acknowledged",
                    {
                        "operationId": current.operation_id,
                        "checkpointId": identifier,
                        "intentDigest": private_digest,
                        "authorizationApprovalId": request.approval_id,
                    },
                )
        self.repository.leases.repository.refresh_event_cursor()
        return current

    def current(self, actor, workspace_id, request):
        with self.repository.leases._write() as session:
            row, plan, attempt = self.fence(actor, workspace_id, request, session)
            record, nonce, private_digest = self.intent(row)
            if (
                record.plan != plan
                or record.attempt_id != attempt
                or record.worker_id != request.worker_id
            ):
                fail("SELF_BUILD_CREATION_CONFLICT", "Recover the original creation lineage.")
        return record, nonce, private_digest

    def transition(self, actor, workspace_id, request, previous, **changes):
        with self.repository.leases._write() as session:
            row, _, attempt = self.fence(actor, workspace_id, request, session)
            record, nonce, private_digest = self.intent(row)
            if record != previous or record.attempt_id != attempt:
                fail("SELF_BUILD_CREATION_CONFLICT", "Creation recovery position changed.")
            payload = record.model_dump()
            payload.update(changes, checkpoint_id=None, updated_at=datetime.now(UTC))
            updated = WorkspaceCreationRecord.model_validate(payload)
            updated_digest = intent_digest(updated, nonce)
            row.creation_json = dict(
                record=updated.model_dump(mode="json"), nonce=nonce, digest=updated_digest
            )
            self.git.event(
                session,
                row,
                actor,
                "self_build.workspace_creation.phase_recorded",
                dict(
                    record=updated.model_dump(mode="json"),
                    intentDigest=updated_digest,
                    authorizationApprovalId=request.approval_id,
                ),
            )
        self.repository.leases.repository.refresh_event_cursor()
        return self.acknowledge(actor, workspace_id, request, updated, updated_digest)

    def create(self, actor, workspace_id, request):
        if not checkout_driver.supports_mutation():
            fail("SELF_BUILD_CHECKOUT_PLATFORM_UNAVAILABLE", "Native mutation requires Windows.")
        with self.repository.sessions() as session:
            row = self.repository.lookup(session, workspace_id)
            self.workspace.authorize(actor, row.task_id, session)
            absent = row.creation_json is None
        if absent:
            self.prepare(actor, workspace_id, request)
        record, nonce, private_digest = self.current(actor, workspace_id, request)
        record = self.acknowledge(actor, workspace_id, request, record, private_digest)

        def authority_check():
            # Short transactions revalidate operator/lease/runtime/stop; none spans Git I/O.
            self.current(actor, workspace_id, request)

        driver = checkout_driver.CheckoutDriver(self.git.tool(), authority_check)
        policy = self.workspace.policies.get(record.plan.workspace.repository_id)
        owner = ownership(record, nonce)
        registration = driver.create(policy, record.plan, owner)
        if record.state == "prepared":
            record = self.transition(
                actor,
                workspace_id,
                request,
                record,
                state="git_created",
                registration_digest=registration["registration_digest"],
            )
        elif record.registration_digest != registration["registration_digest"]:
            fail("SELF_BUILD_CREATION_CONFLICT", "Measured Git registration changed.")
        if record.state == "ready":
            # A ready replay verifies source/index again without advancing recovery.
            expected = record.source_digest
        else:
            expected = None
        recovery_count = record.completed_file_count
        recovery_digest = record.source_digest

        def acknowledge_file(count, source_digest):
            nonlocal record
            if count < recovery_count:
                return
            if count == recovery_count:
                if source_digest != recovery_digest:
                    fail("SELF_BUILD_CREATION_CONFLICT", "Acknowledged source prefix changed.")
                return
            if expected is not None or count != record.completed_file_count + 1:
                fail("SELF_BUILD_CREATION_CONFLICT", "Materialization position is inconsistent.")
            record = self.transition(
                actor,
                workspace_id,
                request,
                record,
                state="materializing",
                completed_file_count=count,
                source_digest=source_digest,
            )

        measured = materialization.materialize(
            driver, policy, record.plan, owner, acknowledge=acknowledge_file
        )
        if expected is not None:
            if measured["source_digest"] != expected:
                fail("SELF_BUILD_CREATION_CONFLICT", "Ready source evidence changed.")
            return record
        if record.state == "finalizing":
            if record.source_digest != measured["source_digest"]:
                fail("SELF_BUILD_CREATION_CONFLICT", "Finalization evidence changed.")
            return self.acknowledge(
                actor, workspace_id, request, record, intent_digest(record, nonce)
            )
        return self.transition(
            actor,
            workspace_id,
            request,
            record,
            state="finalizing",
            completed_file_count=measured["completed_file_count"],
            source_digest=measured["source_digest"],
        )

    def read(self, actor, workspace_id):
        with self.repository.sessions() as session:
            row = self.repository.lookup(session, workspace_id)
            self.workspace.authorize(actor, row.task_id, session)
            record, _, private_digest = self.intent(row)
        if record.checkpoint_id is not None:
            checkpoint, identifier, _ = self.checkpoint(actor, record, private_digest)
            if checkpoint is None or record.checkpoint_id != identifier:
                fail(
                    "SELF_BUILD_CREATION_CHECKPOINT_INVALID",
                    "Native creation checkpoint is missing.",
                )
        return record
