"""Remote operator commands reuse native domain persistence and outbox commits."""

from hashlib import sha256

from app.agent_runtime.authorization import IdentityRuntimeAuthorizer, RuntimeActorContext
from app.agent_runtime.errors import RuntimeActorMismatchError, RuntimePermissionDeniedError
from app.agent_runtime.service import AgentRuntimeService
from app.agent_runtime.sqlalchemy_repository import SqlAlchemyAgentRuntimeRepository
from app.autonomous_worker.repository import ModelExecutionRepository
from app.core.errors import DomainError
from app.decomposition.repository import DecompositionRepository
from app.models.agent_runtime import (
    AgentRunSnapshot,
    RequestCancellationCommand,
    RequestPauseCommand,
    ResumeAgentRunCommand,
    RuntimeCommandResult,
)
from app.models.domain import CreateTaskRequest, Task
from app.models.identity import AgentIdentity
from app.models.remote_control import (
    RemoteAgentPage,
    RemoteAuditPage,
    RemoteGoalPage,
    RemoteRuntimeCommand,
)
from app.remote_control.access import RemoteControlAccess
from app.repositories.sqlalchemy import IdempotencyResult, SqlAlchemyRepository
from app.repositories.task_leases import TaskLeaseRepository
from app.services.events import EventBroker
from app.services.task_creation import prepare_task_creation


class RemoteControlService:
    def __init__(
        self,
        access: RemoteControlAccess,
        repository: SqlAlchemyRepository,
        broker: EventBroker,
        task_leases: TaskLeaseRepository,
        runtime: AgentRuntimeService,
        runtime_repository: SqlAlchemyAgentRuntimeRepository,
    ):
        self.access, self.repository, self.broker = access, repository, broker
        self.task_leases = task_leases
        self.runtime, self.runtime_repository = runtime, runtime_repository

    def runtime_command(
        self, actor: RuntimeActorContext, command: RemoteRuntimeCommand
    ) -> RuntimeCommandResult:
        if not isinstance(
            command, RequestPauseCommand | ResumeAgentRunCommand | RequestCancellationCommand
        ):
            raise ValueError("Remote controls cannot submit worker or completion commands")
        self.access.authorize(actor, "control")
        if (
            isinstance(command, RequestCancellationCommand)
            and command.requester_reference != actor.actor_id
        ):
            raise RuntimeActorMismatchError(run_id=command.run_id, command_id=command.command_id)
        native_authorizer = IdentityRuntimeAuthorizer(self.access.identity)

        def authorize_commit(session, snapshot: AgentRunSnapshot):
            self.access.authorize_in_session(actor, "control", session)
            native_authorizer.authorize(
                actor, command.command_type, snapshot=snapshot, session=session
            )

        with self.runtime_repository.authorize_commits(authorize_commit):
            return self.runtime.handle_authorized(command, actor)

    async def cancel_goal(self, actor: RuntimeActorContext, task_id: str) -> Task:
        self.access.authorize(actor, "control")
        authorizer = IdentityRuntimeAuthorizer(self.access.identity)
        authorizer.authorize_task(actor, "request_cancellation", task_id=task_id)

        def authorize_commit(session):
            self.access.authorize_in_session(actor, "control", session)
            authorizer.authorize_task(
                actor, "request_cancellation", task_id=task_id, session=session
            )

        task = self.task_leases.cancel_task(
            task_id,
            actor_identity_id=actor.actor_id,
            authorize=authorize_commit,
        )
        await self.broker.dispatch_pending()
        return task

    def inspect_goal(self, actor: RuntimeActorContext, task_id: str) -> Task:
        self.access.authorize(actor, "read")
        IdentityRuntimeAuthorizer(self.access.identity).authorize_task(
            actor, "read", task_id=task_id
        )
        return self.repository.get_task_durable(task_id)

    def inspect_graph(self, actor: RuntimeActorContext, task_id: str):
        self.inspect_goal(actor, task_id)
        return DecompositionRepository(self.repository.session_factory).current(task_id)

    def inspect_result(self, actor: RuntimeActorContext, run_id: str):
        self.access.authorize(actor, "read")
        self.runtime.read_run_authorized(run_id, actor)
        return ModelExecutionRepository(
            self.repository.session_factory, outbox_max_attempts=self.repository.outbox_max_attempts
        ).get_by_run(run_id)

    def goal_audit(self, actor: RuntimeActorContext, task_id: str, *, offset: int, limit: int):
        self.inspect_goal(actor, task_id)
        items = self.repository.task_audit_page(task_id, offset=offset, limit=limit)
        return RemoteAuditPage(
            items=items, nextOffset=offset + len(items) if len(items) == limit else None
        )

    def status(self, actor: RuntimeActorContext):
        self.access.authorize(actor, "runtime")
        return self.repository.system_control_snapshot()

    async def system_control(self, actor: RuntimeActorContext, simulator, *, stop: bool):
        self.access.authorize(actor, "control")

        def authorize(session=None):
            if session is not None:
                self.access.authorize_in_session(actor, "control", session)
            decision = self.access.identity.check_permission_resource_access(
                actor.actor_id,
                "system.control",
                "administrative_function",
                "system_control",
                **({"session": session} if session is not None else {}),
            )
            if not decision.allowed:
                raise DomainError(
                    "REMOTE_SYSTEM_CONTROL_DENIED", "System control permission is denied.", 403
                )

        authorize()
        current = self.repository.system_control_snapshot()
        if current.emergencyStop == stop:
            return current
        action = simulator.emergency_stop if stop else simulator.system_resume
        await action(authorize=authorize, actor_identity_id=actor.actor_id)
        return self.repository.system_control_snapshot()

    def active_agents(self, actor: RuntimeActorContext, *, offset: int, limit: int):
        self.access.authorize(actor, "runtime")
        rows = self.access.identity.list_agents(offset, limit)
        return RemoteAgentPage(
            items=[
                AgentIdentity.model_validate(row)
                for row in rows
                if row.lifecycle_state == "active" and row.is_enabled
            ],
            nextOffset=offset + len(rows) if len(rows) == limit else None,
        )

    def list_goals(self, actor: RuntimeActorContext, *, offset: int, limit: int) -> RemoteGoalPage:
        self.access.authorize(actor, "read")
        authorizer = IdentityRuntimeAuthorizer(self.access.identity)
        items, cursor = [], offset
        for _ in range(5):
            candidates = self.repository.list_tasks_page(offset=cursor, limit=limit)
            for task in candidates:
                cursor += 1
                try:
                    authorizer.authorize_task(actor, "read", task_id=task.id)
                except RuntimePermissionDeniedError:
                    continue
                items.append(task)
                if len(items) == limit:
                    return RemoteGoalPage(items=items, nextOffset=cursor)
            if len(candidates) < limit:
                return RemoteGoalPage(items=items)
        return RemoteGoalPage(items=items, nextOffset=cursor)

    async def submit_goal(
        self, actor: RuntimeActorContext, body: CreateTaskRequest, idempotency_key: str
    ) -> Task:
        self.access.authorize(actor, "submit")
        authorizer = IdentityRuntimeAuthorizer(self.access.identity)

        def authorize_submission(session=None):
            if session is not None:
                self.access.authorize_in_session(actor, "submit", session)
            if body.correctionOfTaskId is not None:
                if session is None:
                    self.access.authorize(actor, "read")
                else:
                    self.access.authorize_in_session(actor, "read", session)
                authorizer.authorize_task(
                    actor, "read", task_id=body.correctionOfTaskId, session=session
                )

        # Correction copies source project/lineage, which requires source read authority.
        authorize_submission()
        # Namespace retries by authenticated identity, never a caller actor field.
        key = "remote-goal-" + sha256((actor.actor_id + ":" + idempotency_key).encode()).hexdigest()
        command = "remote.goal.submit"
        payload = {"actorId": actor.actor_id, "goal": body.model_dump(mode="json")}
        claim = self.repository.idempotency_claim(key, command, payload)
        if claim.response is not None:
            return Task.model_validate(claim.response[1]["data"])
        assert claim.owned and claim.lease_expires_at is not None
        try:
            source = (
                self.repository.get_task_durable(body.correctionOfTaskId)
                if body.correctionOfTaskId
                else None
            )
            task = prepare_task_creation(body, source)
            task.createdBy = actor.actor_id
            await self.broker.emit(
                "task.created",
                {"task": task.model_dump(mode="json")},
                task.id,
                correlation_id=key,
                source="remote-operator",
                audit={
                    "summary": "Remote operator submitted a goal",
                    "actorIdentityId": actor.actor_id,
                    "payload": {"remoteOperation": "submit", "actorId": actor.actor_id},
                },
                created_task=task,
                authorize=authorize_submission,
                idempotency=IdempotencyResult(
                    key=key,
                    command=command,
                    payload=payload,
                    status=201,
                    body={"data": task.model_dump(mode="json")},
                    lease_expires_at=claim.lease_expires_at,
                    resource_id=task.id,
                ),
            )
            return task
        finally:
            # The existing repository only abandons an uncommitted owned claim.
            # A lost publication/HTTP acknowledgement cannot erase a committed result.
            self.repository.idempotency_abandon(key, command, claim.lease_expires_at)
