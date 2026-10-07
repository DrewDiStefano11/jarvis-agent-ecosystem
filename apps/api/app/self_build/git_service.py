"""Durable Git reads, fenced by native authority with no transaction across subprocesses."""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

from sqlalchemy import select

from app.agent_runtime.authorization import RuntimeActorContext
from app.core.errors import DomainError
from app.db.models import AuditEventRow, SystemStateRow
from app.models import git_inspection as inspection_contracts
from app.models.git_inspection import (
    RepositoryInspection,
    RepositoryInspectionApproval,
    RepositoryInspectionPlan,
    RepositoryObservation,
)
from app.self_build import git_image as image_module
from app.self_build import git_observer as observer_module
from app.self_build import policy as repository_policy_module
from app.self_build.git_observer import GitObserver
from app.self_build.policy import digest
from app.tool_execution import filesystem as filesystem_module

INSPECTION_POLICY_DIGEST = sha256(
    Path(__file__).read_text(encoding="utf-8").encode()
    + Path(observer_module.__file__).read_text(encoding="utf-8").encode()
    + Path(image_module.__file__).read_text(encoding="utf-8").encode()
    + Path(inspection_contracts.__file__).read_text(encoding="utf-8").encode()
    + Path(filesystem_module.__file__).read_text(encoding="utf-8").encode()
    + Path(repository_policy_module.__file__).read_text(encoding="utf-8").encode()
).hexdigest()


class GitInspectionService:
    def __init__(self, app):
        self.app = app
        self.workspace = app.state.self_build_workspace_service
        self.repository = self.workspace.repository
        self.settings = app.state.settings

    @staticmethod
    def fail(code, message):
        raise DomainError(code, message, 409)

    def tool(self):
        if (
            not self.settings.self_build_enabled
            or not self.settings.self_build_git_executable
            or not self.settings.self_build_git_sha256
        ):
            self.fail(
                "SELF_BUILD_GIT_DISABLED",
                "Configure the pinned native Git observer before inspection.",
            )
        return GitObserver(
            self.settings.self_build_git_executable, self.settings.self_build_git_sha256
        )

    def permission(self, actor, task_id, session):
        decision = self.workspace.identity.check_permission_resource_access(
            actor.actor_id, "self_build.git.inspect", "task", task_id, session=session
        )
        if not decision.allowed:
            raise DomainError(
                "SELF_BUILD_GIT_PERMISSION_DENIED", "Repository inspection is denied.", 403
            )

    def plan_in_session(self, actor, workspace_id, session):
        row = self.repository.lookup(session, workspace_id)
        self.workspace.authorize(actor, row.task_id, session)
        self.repository.contract(row)
        tool = self.tool()
        policy = self.workspace.policies.get(row.repository_id)
        if row.state != "reserved" or self.repository.reason(
            session, row, digest(policy.model_dump())
        ):
            self.fail(
                "SELF_BUILD_RECOVERY_REQUIRED",
                "Recover active workspace ownership before inspection.",
            )
        payload = dict(
            schema_version="1.0",
            operation="git.repository.inspect",
            workspace_id=row.id,
            workspace_version=row.version,
            workspace_plan_hash=row.plan_json["plan_hash"],
            repository_identity=row.repository_identity,
            base_sha=row.plan_json["base_sha"],
            tool_identity_digest=digest([str(tool.executable), tool.executable_hash]),
            tool_sha256=tool.executable_hash,
            inspection_policy_digest=INSPECTION_POLICY_DIGEST,
        )
        return row, RepositoryInspectionPlan(**payload, plan_hash=digest(payload))

    def preview(self, actor, workspace_id):
        with self.repository.sessions() as session:
            return self.plan_in_session(actor, workspace_id, session)[1]

    def operator(self, actor, row, session):
        access = getattr(self.app.state, "remote_control_service", None)
        if access is None:
            self.fail("SELF_BUILD_APPROVAL_UNAVAILABLE", "Configure authenticated operator access.")
        access.access.authorize_in_session(actor, "control", session)
        self.workspace.authorize(actor, row.task_id, session, write=True)
        self.permission(actor, row.task_id, session)
        run = self.repository.runtime(session, row.runtime_run_id)
        if actor.actor_id in {row.actor_id, run.agent_id}:
            raise DomainError(
                "SELF_BUILD_SELF_APPROVAL_DENIED",
                "The implementation actor cannot approve repository inspection.",
                403,
            )

    def event(self, session, row, actor, event_type, payload):
        self.repository.leases._add_event(
            session,
            event_type,
            "Native Git inspection authority or evidence",
            task_id=row.task_id,
            actor_identity_id=actor.actor_id,
            payload=payload,
        )
        session.flush()
        state = session.get(SystemStateRow, 1)
        return session.scalar(
            select(AuditEventRow).where(
                AuditEventRow.event_session_id == state.event_session_id,
                AuditEventRow.sequence_number == state.current_sequence_number,
            )
        )

    def approve(self, actor, request):
        with self.repository.leases._write() as session:
            row, plan = self.plan_in_session(actor, request.workspace_id, session)
            self.operator(actor, row, session)
            if request.expected_plan_hash != plan.plan_hash:
                self.fail(
                    "SELF_BUILD_PLAN_CHANGED", "Approve the exact repository inspection plan."
                )
            expires = datetime.now(UTC) + timedelta(seconds=request.valid_for_seconds)
            event = self.event(
                session,
                row,
                actor,
                "self_build.git_inspection.approved",
                {"plan": plan.model_dump(mode="json"), "expiresAt": expires.isoformat()},
            )
            result = RepositoryInspectionApproval(
                approval_id=event.id, plan=plan, approved_by=actor.actor_id, expires_at=expires
            )
        self.repository.leases.repository.refresh_event_cursor()
        return result

    def fence(self, actor, workspace_id, request, session):
        row, plan = self.plan_in_session(actor, workspace_id, session)
        self.workspace.authorize(actor, row.task_id, session, write=True)
        self.permission(actor, row.task_id, session)
        if request.expected_plan_hash != plan.plan_hash:
            self.fail("SELF_BUILD_PLAN_CHANGED", "Repository inspection plan changed.")
        self.repository.leases._require_lease(
            session, row.task_id, request.worker_id, request.lease_token, datetime.now(UTC)
        )
        if (
            request.worker_id != row.worker_id
            or digest(request.lease_token) != row.lease_fingerprint
        ):
            self.fail(
                "SELF_BUILD_RECOVERY_REQUIRED",
                "Repository inspection requires the original owner lease.",
            )
        event = session.get(AuditEventRow, request.approval_id)
        try:
            payload = event.payload["payload"]
            expires = datetime.fromisoformat(payload["expiresAt"])
            valid = (
                event.event_type == "self_build.git_inspection.approved"
                and event.task_id == row.task_id
                and RepositoryInspectionPlan.model_validate(payload["plan"]) == plan
                and event.actor != actor.actor_id
                and expires.tzinfo is not None
                and expires > datetime.now(UTC)
            )
        except (AttributeError, KeyError, ValueError, TypeError):
            valid = False
        if not valid:
            raise DomainError(
                "SELF_BUILD_APPROVAL_REQUIRED",
                "Current operator approval of the exact Git inspection plan is required.",
                403,
            )
        self.operator(
            RuntimeActorContext(actor_id=event.actor, stable_key="persisted-operator"), row, session
        )
        return row, plan, self.workspace.policies.get(row.repository_id)

    def inspect(self, actor, workspace_id, request):
        with self.repository.leases._write() as session:
            _, plan, policy = self.fence(actor, workspace_id, request, session)
        started = datetime.now(UTC)

        # Never hold SQLite's write lock across a process: cancellation and
        # emergency-stop transactions must remain able to revoke authority.
        def guard():
            with self.repository.leases._write() as session:
                self.fence(actor, workspace_id, request, session)

        observer = self.tool()
        observer.authority_check = guard
        observation = RepositoryObservation(**observer.inspect(policy, plan.base_sha))
        finished = datetime.now(UTC)
        with self.repository.leases._write() as session:
            row, current_plan, _ = self.fence(actor, workspace_id, request, session)
            if plan != current_plan:
                self.fail(
                    "SELF_BUILD_PLAN_CHANGED", "Inspection authority changed during the read."
                )
            payload = dict(
                workspace_id=row.id,
                plan=plan.model_dump(mode="json"),
                approval_id=request.approval_id,
                worker_id=request.worker_id,
                started_at=started.isoformat(),
                finished_at=finished.isoformat(),
                observation=observation.model_dump(mode="json"),
            )
            event = self.event(session, row, actor, "self_build.git_inspection.observed", payload)
            result = RepositoryInspection(inspection_id=event.id, **payload)
        self.repository.leases.repository.refresh_event_cursor()
        return result

    def read(self, actor, workspace_id, inspection_id):
        with self.repository.sessions() as session:
            row = self.repository.lookup(session, workspace_id)
            self.workspace.authorize(actor, row.task_id, session)
            self.repository.contract(row)
            event = session.get(AuditEventRow, inspection_id)
            try:
                payload = event.payload["payload"]
                if (
                    event.event_type != "self_build.git_inspection.observed"
                    or event.task_id != row.task_id
                    or payload["workspace_id"] != row.id
                ):
                    raise ValueError("wrong workspace")
                result = RepositoryInspection(inspection_id=event.id, **payload)
                if (
                    digest(result.plan.model_dump(mode="json", exclude={"plan_hash"}))
                    != result.plan.plan_hash
                    or result.plan.workspace_plan_hash != row.plan_json["plan_hash"]
                    or result.worker_id != row.worker_id
                ):
                    raise ValueError("inspection record integrity failed")
                return result
            except (AttributeError, KeyError, ValueError, TypeError):
                self.fail(
                    "SELF_BUILD_INSPECTION_NOT_FOUND",
                    "Repository inspection evidence was not found.",
                )
