"""Explicitly approved workspace intent. No filesystem mutation or Git invocation."""

from datetime import UTC, datetime

from app.core.errors import DomainError
from app.db.models import DevelopmentWorkspaceRow, SystemStateRow, TaskRow
from app.models.self_build import WorkspacePlan
from app.self_build.policy import RepositoryPolicies, digest
from app.self_build.repository import ACTIVE_RUNTIME_STATES, WorkspaceRepository


class WorkspaceService:
    def __init__(self, app):
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

    def reserve(self, actor, request):
        with self.repository.leases._write() as session:
            plan = self.plan_in_session(actor, request, session)
            self.authorize(actor, plan.task_id, session, write=True)
            if plan.plan_hash != request.expected_plan_hash:
                raise DomainError(
                    "SELF_BUILD_PLAN_CHANGED", "Approve the exact current workspace plan.", 409
                )
            run = self.repository.runtime(session, plan.runtime_run_id)
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
            row = session.get(DevelopmentWorkspaceRow, plan.worktree_key)
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

    def abandon(self, actor, workspace_id, request):
        with self.repository.leases._write() as session:
            row = self.repository.lookup(session, workspace_id)
            self.authorize(actor, row.task_id, session, write=True)
            if row.version != request.expected_version:
                raise DomainError("SELF_BUILD_VERSION_CONFLICT", "Workspace version changed.", 409)
            if row.state == "abandoned":
                return self.repository.contract(row)
            # Abandonment never removes files or releases task/runtime ownership.
            row.state, row.version, row.updated_at = "abandoned", row.version + 1, datetime.now(UTC)
            self.repository.emit(session, row, actor, "self_build.workspace.abandoned")
            result = self.repository.contract(row)
        self.repository.leases.repository.refresh_event_cursor()
        return result
