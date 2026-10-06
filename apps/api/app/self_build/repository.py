"""Durable workspace reservations; transaction, task fencing and outbox are native."""

from datetime import UTC, datetime

from sqlalchemy import select

from app.core.errors import DomainError
from app.db.models import AgentRuntimeRunRow, DevelopmentWorkspaceRow, TaskLeaseRow, TaskRow
from app.models.agent_runtime import AgentRunSnapshot
from app.models.self_build import WorkspacePlan, WorkspaceReservation
from app.repositories.task_leases import _utc
from app.self_build.policy import RepositoryPolicy, digest

ACTIVE_RUNTIME_STATES = {"claimed", "starting", "running"}


class WorkspaceRepository:
    def __init__(self, leases):
        self.leases = leases
        self.sessions = leases.session_factory

    @staticmethod
    def runtime(session, run_id):
        row = session.get(AgentRuntimeRunRow, run_id)
        if row is None:
            raise DomainError("SELF_BUILD_RUN_NOT_FOUND", "Runtime run was not found.", 404)
        try:
            snapshot = AgentRunSnapshot.model_validate_json(row.snapshot_json)
        except ValueError:
            raise DomainError(
                "SELF_BUILD_LINEAGE_INVALID", "Runtime lineage is inconsistent.", 409
            ) from None
        if (
            snapshot.specification.run_id != row.run_id
            or snapshot.specification.task_id != row.task_id
            or snapshot.state.value != row.state
            or snapshot.version != row.version
        ):
            raise DomainError("SELF_BUILD_LINEAGE_INVALID", "Runtime lineage is inconsistent.", 409)
        return row

    @staticmethod
    def contract(row, reason=None):
        try:
            plan = WorkspacePlan.model_validate(row.plan_json)
            policy = RepositoryPolicy.model_validate(row.policy_json)
        except ValueError:
            raise DomainError(
                "SELF_BUILD_RECORD_INVALID", "Workspace plan integrity failed.", 409
            ) from None
        key = digest(["development-workspace-v1", policy.repository_identity, row.runtime_run_id])[
            :32
        ]
        payload = plan.model_dump(mode="json", exclude={"plan_hash"})
        if (
            digest(payload) != plan.plan_hash
            or plan.repository_id != row.repository_id
            or plan.repository_identity != row.repository_identity
            or plan.repository_identity != policy.repository_identity
            or plan.base_branch != policy.base_branch
            or plan.policy_digest != row.policy_digest
            or plan.policy_digest != digest(policy.model_dump())
            or plan.worktree_key != "jarvis-" + key
            or plan.branch != "codex/jarvis-" + key
            or row.id != plan.worktree_key
            or plan.runtime_run_id != row.runtime_run_id
            or plan.task_id != row.task_id
        ):
            raise DomainError("SELF_BUILD_RECORD_INVALID", "Workspace plan integrity failed.", 409)
        return WorkspaceReservation(
            workspace_id=row.id,
            plan=plan,
            state=row.state,
            recovery_required=reason is not None,
            recovery_reason=reason,
            created_by=row.actor_id,
            worker_id=row.worker_id,
            created_at=_utc(row.created_at),
            updated_at=_utc(row.updated_at),
            version=row.version,
        )

    def reason(self, session, row, policy_digest):
        if row.state == "abandoned":
            return None
        if row.policy_digest != policy_digest:
            return "policy_changed"
        run = self.runtime(session, row.runtime_run_id)
        task = session.get(TaskRow, row.task_id)
        if run.state not in ACTIVE_RUNTIME_STATES or task is None or task.status != "in_progress":
            return "runtime_inactive"
        lease = session.get(TaskLeaseRow, row.task_id)
        if (
            lease is None
            or lease.worker_id != row.worker_id
            or _utc(lease.expires_at) <= datetime.now(UTC)
            or digest(lease.lease_token) != row.lease_fingerprint
        ):
            return "lease_lost"
        return None

    def emit(self, session, row, actor, event):
        self.leases._add_event(
            session,
            event,
            "Development workspace reservation updated",
            task_id=row.task_id,
            actor_identity_id=actor.actor_id,
            payload={
                "workspaceId": row.id,
                "runtimeRunId": row.runtime_run_id,
                "repositoryId": row.repository_id,
                "planHash": row.plan_json["plan_hash"],
                "state": row.state,
                "version": row.version,
            },
        )

    @staticmethod
    def lookup(session, workspace_id):
        row = session.get(DevelopmentWorkspaceRow, workspace_id)
        if row is None:
            raise DomainError("SELF_BUILD_WORKSPACE_NOT_FOUND", "Workspace was not found.", 404)
        return row

    @staticmethod
    def find(session, run_id, repository_id):
        return session.scalar(
            select(DevelopmentWorkspaceRow).where(
                DevelopmentWorkspaceRow.runtime_run_id == run_id,
                DevelopmentWorkspaceRow.repository_id == repository_id,
            )
        )
