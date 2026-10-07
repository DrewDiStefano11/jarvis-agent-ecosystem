"""Explicitly approved workspace intent. No filesystem mutation or Git invocation."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.errors import DomainError
from app.db.models import AuditEventRow, DevelopmentWorkspaceRow, SystemStateRow, TaskRow
from app.models.self_build import (
    WorkspaceAbandonApproval,
    WorkspaceAbandonPlan,
    WorkspaceApproval,
    WorkspacePlan,
)
from app.self_build.policy import RepositoryPolicies, digest
from app.self_build.repository import ACTIVE_RUNTIME_STATES, WorkspaceRepository


class WorkspaceService:
    def __init__(self, app):
        self.app = app
        self.settings = app.state.settings
        self.identity = app.state.identity_service
        self.runtime_authorizer = app.state.agent_runtime_service.authorizer
        self.policies = RepositoryPolicies(self.settings)
        self.repository = WorkspaceRepository(app.state.task_leases)

    def authorize(self, actor, task_id, session, *, write=False):
        self.runtime_authorizer.authorize_task(actor, "read", task_id=task_id, session=session)
        if write:
            if session.bind is None or session.bind.dialect.name != "sqlite":
                raise DomainError(
                    "SELF_BUILD_DATABASE_UNSUPPORTED",
                    "Workspace mutation requires SQLite transaction fencing.",
                    409,
                )
            self.runtime_authorizer.authorize_task(actor, "claim", task_id=task_id, session=session)
            if not self.settings.self_build_enabled:
                raise DomainError("SELF_BUILD_DISABLED", "Self-Build is disabled.", 409)
            decision = self.identity.check_permission_resource_access(
                actor.actor_id,
                "self_build.workspace",
                "task",
                task_id,
                session=session,
            )
            if not decision.allowed:
                raise DomainError(
                    "SELF_BUILD_PERMISSION_DENIED", "Workspace mutation is denied.", 403
                )
            state = session.get(SystemStateRow, 1)
            if state is None or state.emergency_stop:
                raise DomainError(
                    "EMERGENCY_STOP_ACTIVE", "Emergency stop blocks workspace mutation.", 423
                )

    def plan_in_session(self, actor, intent, session):
        run = self.repository.runtime(session, intent.runtime_run_id)
        self.authorize(actor, run.task_id, session)
        policy = self.policies.get(intent.repository_id)
        key = digest(["development-workspace-v1", policy.repository_identity, run.run_id])[:32]
        payload = dict(
            schema_version="1.0",
            repository_id=intent.repository_id,
            repository_identity=policy.repository_identity,
            policy_digest=digest(policy.model_dump()),
            task_id=run.task_id,
            runtime_run_id=run.run_id,
            base_branch=policy.base_branch,
            base_sha=intent.base_sha,
            branch="codex/jarvis-" + key,
            worktree_key="jarvis-" + key,
        )
        return WorkspacePlan(**payload, plan_hash=digest(payload))

    def preview(self, actor, intent):
        with self.repository.sessions() as session:
            return self.plan_in_session(actor, intent, session)

    def approve(self, actor, request):
        # Only the authenticated operator router exposes this service action.
        access = getattr(self.app.state, "remote_control_service", None)
        if access is None:
            raise DomainError(
                "SELF_BUILD_APPROVAL_UNAVAILABLE", "Configure authenticated operator access.", 409
            )
        with self.repository.leases._write() as session:
            access.access.authorize_in_session(actor, "control", session)
            plan = self.plan_in_session(actor, request, session)
            self.authorize(actor, plan.task_id, session, write=True)
            run = self.repository.runtime(session, plan.runtime_run_id)
            if actor.actor_id == run.agent_id:
                raise DomainError(
                    "SELF_BUILD_SELF_APPROVAL_DENIED",
                    "The runtime agent cannot approve its workspace.",
                    403,
                )
            if request.expected_plan_hash != plan.plan_hash:
                raise DomainError(
                    "SELF_BUILD_PLAN_CHANGED", "Approve the exact current workspace plan.", 409
                )
            expires = datetime.now(UTC) + timedelta(seconds=request.valid_for_seconds)
            self.repository.leases._add_event(
                session,
                "self_build.workspace.approved",
                "Operator approved workspace reservation",
                task_id=plan.task_id,
                actor_identity_id=actor.actor_id,
                payload={
                    "planHash": plan.plan_hash,
                    "runtimeRunId": plan.runtime_run_id,
                    "expiresAt": expires.isoformat(),
                },
            )
            session.flush()
            state = session.get(SystemStateRow, 1)
            event = session.scalar(
                select(AuditEventRow).where(
                    AuditEventRow.event_session_id == state.event_session_id,
                    AuditEventRow.sequence_number == state.current_sequence_number,
                )
            )
            result = WorkspaceApproval(
                approval_id=event.id, plan=plan, approved_by=actor.actor_id, expires_at=expires
            )
        self.repository.leases.repository.refresh_event_cursor()
        return result

    def require_approval(self, actor, request, plan, run, session):
        event = session.get(AuditEventRow, request.approval_id)
        access = getattr(self.app.state, "remote_control_service", None)
        try:
            payload = event.payload["payload"] if event is not None else {}
            expires = datetime.fromisoformat(payload["expiresAt"])
            valid = (
                event.event_type == "self_build.workspace.approved"
                and event.task_id == plan.task_id
                and payload["planHash"] == plan.plan_hash
                and payload["runtimeRunId"] == run.run_id
                and event.actor != actor.actor_id
                and event.actor != run.agent_id
                and expires.tzinfo is not None
                and expires > datetime.now(UTC)
                and access is not None
                and access.access.actor_id == event.actor
            )
        except (KeyError, ValueError, TypeError):
            valid = False
        if not valid:
            raise DomainError(
                "SELF_BUILD_APPROVAL_REQUIRED",
                "A current operator approval of this exact plan is required.",
                403,
            )
        operator = self.runtime_authorizer.authenticate(event.actor)
        access.access.authorize_in_session(operator, "control", session)
        self.authorize(operator, plan.task_id, session, write=True)

    def reserve(self, actor, request):
        with self.repository.leases._write() as session:
            plan = self.plan_in_session(actor, request, session)
            self.authorize(actor, plan.task_id, session, write=True)
            if plan.plan_hash != request.expected_plan_hash:
                raise DomainError(
                    "SELF_BUILD_PLAN_CHANGED", "Approve the exact current workspace plan.", 409
                )
            run = self.repository.runtime(session, plan.runtime_run_id)
            self.require_approval(actor, request, plan, run, session)
            task = session.get(TaskRow, plan.task_id)
            if (
                run.state not in ACTIVE_RUNTIME_STATES
                or task is None
                or task.status != "in_progress"
            ):
                raise DomainError(
                    "SELF_BUILD_OWNER_INACTIVE",
                    "Workspace requires an active runtime and leased task.",
                    409,
                )
            self.repository.leases._require_lease(
                session, plan.task_id, request.worker_id, request.lease_token, datetime.now(UTC)
            )
            if self.repository.executor(session, run) != request.worker_id:
                raise DomainError(
                    "SELF_BUILD_EXECUTOR_MISMATCH",
                    "Task lease holder must match the runtime executor.",
                    409,
                )
            row = self.repository.find(session, plan.runtime_run_id, plan.repository_id)
            key_owner = session.get(DevelopmentWorkspaceRow, plan.worktree_key)
            if key_owner is not None and key_owner is not row:
                raise DomainError(
                    "SELF_BUILD_WORKSPACE_CONFLICT",
                    "The generated namespace already belongs to another reservation.",
                    409,
                )
            if row is not None:
                existing = self.repository.contract(row)
                if existing.plan != plan or row.state != "reserved":
                    raise DomainError(
                        "SELF_BUILD_WORKSPACE_CONFLICT",
                        "This mission already owns a different reservation.",
                        409,
                    )
                reason = self.repository.reason(session, row, plan.policy_digest)
                if reason:
                    raise DomainError(
                        "SELF_BUILD_RECOVERY_REQUIRED",
                        "Recover existing workspace ownership before proceeding.",
                        409,
                    )
                return existing
            now = datetime.now(UTC)
            row = DevelopmentWorkspaceRow(
                id=plan.worktree_key,
                repository_id=plan.repository_id,
                repository_identity=plan.repository_identity,
                policy_digest=plan.policy_digest,
                policy_json=self.policies.get(plan.repository_id).model_dump(),
                runtime_run_id=plan.runtime_run_id,
                task_id=plan.task_id,
                actor_id=actor.actor_id,
                worker_id=request.worker_id,
                approval_id=request.approval_id,
                lease_fingerprint=digest(request.lease_token),
                plan_json=plan.model_dump(mode="json"),
                state="reserved",
                version=1,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            self.repository.emit(session, row, actor, "self_build.workspace.reserved")
            result = self.repository.contract(row)
        self.repository.leases.repository.refresh_event_cursor()
        return result

    def read(self, actor, workspace_id):
        with self.repository.sessions() as session:
            row = self.repository.lookup(session, workspace_id)
            self.authorize(actor, row.task_id, session)
            try:
                policy_digest = digest(self.policies.get(row.repository_id).model_dump())
            except DomainError:
                policy_digest = None
            return self.repository.contract(
                row, self.repository.reason(session, row, policy_digest)
            )

    def abandon_plan(self, row):
        reservation = self.repository.contract(row)
        payload = dict(
            workspace_id=row.id,
            workspace_version=row.version if row.state == "reserved" else row.version - 1,
            workspace=reservation.plan,
            worker_id=row.worker_id,
        )
        provisional = WorkspaceAbandonPlan(**payload, plan_hash="0" * 64)
        return provisional.model_copy(
            update={"plan_hash": digest(provisional.model_dump(mode="json", exclude={"plan_hash"}))}
        )

    def preview_abandon(self, actor, workspace_id):
        with self.repository.sessions() as session:
            row = self.repository.lookup(session, workspace_id)
            self.authorize(actor, row.task_id, session)
            return self.abandon_plan(row)

    def require_operator(self, actor, plan, session):
        access = getattr(self.app.state, "remote_control_service", None)
        if access is None:
            raise DomainError(
                "SELF_BUILD_APPROVAL_UNAVAILABLE", "Configure authenticated operator access.", 409
            )
        access.access.authorize_in_session(actor, "control", session)
        self.authorize(actor, plan.task_id, session, write=True)
        run = self.repository.runtime(session, plan.runtime_run_id)
        if actor.actor_id == run.agent_id:
            raise DomainError(
                "SELF_BUILD_SELF_APPROVAL_DENIED",
                "The runtime agent cannot approve its workspace.",
                403,
            )

    def approve_abandon(self, actor, request):
        with self.repository.leases._write() as session:
            row = self.repository.lookup(session, request.workspace_id)
            plan = self.abandon_plan(row)
            self.require_operator(actor, plan.workspace, session)
            if row.state != "reserved" or request.expected_plan_hash != plan.plan_hash:
                raise DomainError(
                    "SELF_BUILD_PLAN_CHANGED", "Approve the exact current abandonment plan.", 409
                )
            expires = datetime.now(UTC) + timedelta(seconds=request.valid_for_seconds)
            self.repository.leases._add_event(
                session,
                "self_build.workspace_abandon.approved",
                "Operator approved workspace abandonment",
                task_id=row.task_id,
                actor_identity_id=actor.actor_id,
                payload={"plan": plan.model_dump(mode="json"), "expiresAt": expires.isoformat()},
            )
            session.flush()
            state = session.get(SystemStateRow, 1)
            event = session.scalar(
                select(AuditEventRow).where(
                    AuditEventRow.event_session_id == state.event_session_id,
                    AuditEventRow.sequence_number == state.current_sequence_number,
                )
            )
            result = WorkspaceAbandonApproval(
                approval_id=event.id, plan=plan, approved_by=actor.actor_id, expires_at=expires
            )
        self.repository.leases.repository.refresh_event_cursor()
        return result

    def abandon(self, actor, workspace_id, request):
        with self.repository.leases._write() as session:
            row = self.repository.lookup(session, workspace_id)
            self.authorize(actor, row.task_id, session, write=True)
            if row.version != request.expected_version:
                raise DomainError("SELF_BUILD_VERSION_CONFLICT", "Workspace version changed.", 409)
            plan = self.abandon_plan(row)
            if plan.plan_hash != request.expected_plan_hash:
                raise DomainError("SELF_BUILD_PLAN_CHANGED", "Abandonment plan changed.", 409)
            run = self.repository.runtime(session, row.runtime_run_id)
            self.repository.leases._require_lease(
                session, row.task_id, request.worker_id, request.lease_token, datetime.now(UTC)
            )
            if (
                request.worker_id != row.worker_id
                or digest(request.lease_token) != row.lease_fingerprint
                or run.state not in ACTIVE_RUNTIME_STATES
                or self.repository.executor(session, run) != row.worker_id
            ):
                raise DomainError(
                    "SELF_BUILD_RECOVERY_REQUIRED",
                    "Abandonment requires the original live runtime owner.",
                    409,
                )
            task = session.get(TaskRow, row.task_id)
            if task is None or task.status != "in_progress":
                raise DomainError(
                    "SELF_BUILD_OWNER_INACTIVE", "Abandonment requires an active leased task.", 409
                )
            event = session.get(AuditEventRow, request.approval_id)
            try:
                payload = event.payload["payload"]
                expires = datetime.fromisoformat(payload["expiresAt"])
                valid = (
                    event.event_type == "self_build.workspace_abandon.approved"
                    and event.task_id == row.task_id
                    and event.actor not in {actor.actor_id, run.agent_id}
                    and WorkspaceAbandonPlan.model_validate(payload["plan"]) == plan
                    and expires.tzinfo is not None
                    and expires > datetime.now(UTC)
                )
            except (AttributeError, KeyError, TypeError, ValueError):
                valid = False
            if not valid:
                raise DomainError(
                    "SELF_BUILD_APPROVAL_REQUIRED",
                    "Current exact operator abandonment approval is required.",
                    403,
                )
            operator = self.runtime_authorizer.authenticate(event.actor)
            self.require_operator(operator, plan.workspace, session)
            if row.state == "abandoned":
                return self.repository.contract(row)
            # Abandonment never removes files or releases task/runtime ownership.
            row.state, row.version, row.updated_at = "abandoned", row.version + 1, datetime.now(UTC)
            self.repository.emit(session, row, actor, "self_build.workspace.abandoned")
            result = self.repository.contract(row)
        self.repository.leases.repository.refresh_event_cursor()
        return result
