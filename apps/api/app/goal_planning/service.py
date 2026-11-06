"""Explicit consent, atomic native capture and deterministic runtime admission."""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import select, update

from app.agent_runtime.authorization import RUNTIME_PERMISSION_KEYS
from app.agent_runtime.errors import AgentRuntimeError
from app.autonomous_worker.provisioning import _find_actor, _find_permission
from app.core.errors import DomainError
from app.db.models import ContextAssemblyRow, IdentityPermissionRow, SystemStateRow
from app.goal_planning.guards import require_goal_consent, require_live_policy, task_input_hash, utc
from app.goal_planning.repository import GoalPlanningAdmissionRow, GoalPlanningPolicyRow
from app.models.agent_runtime import (
    AgentRunSpecification,
    AutonomousExecutionSpecification,
    CreateAgentRunCommand,
    QueueAgentRunCommand,
    canonical_json,
)
from app.models.context import (
    ContextAssembly,
    ContextAssemblyEventPayload,
    CreateContextAssemblyRequest,
)
from app.models.goal_planning import (
    GoalPlanningAdmissionView,
    GoalPlanningLink,
    GoalPlanningPolicyView,
    direct_goal_budget,
)
from app.models.identity import AssignPermissionRequest, CreateAgentRequest, CreatePermissionRequest
from app.repositories.sqlalchemy import IdempotencyResult
from app.services.execution_budget import direct_providers, validate_direct_assembly


class GoalPlanningService:
    def __init__(self, app):
        self.app = app
        self.repository = app.state.repository
        self.sessions = self.repository.session_factory
        self.identity = app.state.identity_service
        self.runtime = app.state.agent_runtime_service

    def setup_operator(self):
        actor = _find_actor(self.identity, "local-goal-planning-operator")
        if actor and (not actor.is_enabled or actor.lifecycle_state != "active"):
            raise DomainError("AGENT_INACTIVE", "Setup preserves inactive identities.", 409)
        permission = _find_permission(self.identity, "goals.configure")
        if permission and (
            not permission.is_enabled
            or permission.resource_type != "administrative_function"
            or permission.action != "configure_goal_planning"
        ):
            raise DomainError(
                "PERMISSION_SCOPE_MISMATCH", "Goal configuration permission is incompatible.", 409
            )
        if permission is None:
            permission = self.identity.create_definition(
                "permission",
                CreatePermissionRequest(
                    stable_key="goals.configure",
                    display_name="Configure bounded goal planning",
                    resource_type="administrative_function",
                    action="configure_goal_planning",
                ),
            )
        if actor is None:
            actor = self.identity.create_agent(
                CreateAgentRequest(
                    stable_key="local-goal-planning-operator",
                    display_name="Local goal planning operator",
                    agent_type="coordinator",
                )
            )
            actor = self.identity.transition(actor.id, "active")
            self.identity.assign_permission(
                actor.id,
                AssignPermissionRequest(
                    permission_id=permission.id,
                    effect="allow",
                    resource_type="administrative_function",
                    resource_id="goal_planning",
                ),
            )
        return actor.id

    def authorize(self, actor_id, session=None):
        if (
            not actor_id
            or not self.identity.check_permission_resource_access(
                actor_id,
                "goals.configure",
                "administrative_function",
                "goal_planning",
                session=session,
            ).allowed
        ):
            raise DomainError(
                "GOAL_PLANNING_PERMISSION_DENIED",
                "Goal planning configuration is not authorized.",
                403,
            )

    def unavailable_reason(self):
        settings = self.app.state.settings
        if (
            not settings.autonomous_worker_enabled
            or not settings.autonomous_worker_actor_id.strip()
        ):
            return "worker_not_configured"
        if (
            settings.model_execution_mode != "local_only"
            and not settings.verified_cloud_execution_allowed
        ):
            return "model_execution_disabled"
        eligible = [
            provider
            for provider in direct_providers(self.app.state.model_router)
            if provider.execution_mode != "disabled"
            and (
                provider.is_local
                or (
                    settings.verified_cloud_execution_allowed
                    and getattr(provider, "free_eligible", lambda model: False)(
                        provider.default_model
                    )
                )
            )
        ]
        if not eligible:
            return "bounded_provider_required"
        return None

    @staticmethod
    def policy_view(row, unavailable=None):
        if row is None:
            return GoalPlanningPolicyView(unavailableReason=unavailable)
        return GoalPlanningPolicyView(
            id=row.id,
            revision=row.revision,
            enabled=row.enabled and utc(row.expires_at) > datetime.now(UTC),
            policyHash=row.policy_hash,
            maximumGoals=row.maximum_goals,
            admittedGoals=row.admitted_goals,
            expiresAt=utc(row.expires_at),
            unavailableReason=unavailable,
        )

    def policy(self):
        with self.sessions() as session:
            row = session.scalar(
                select(GoalPlanningPolicyRow)
                .order_by(GoalPlanningPolicyRow.revision.desc())
                .limit(1)
            )
            reason = self.unavailable_reason()
            if (
                row is not None
                and row.worker_actor_id
                != self.app.state.settings.autonomous_worker_actor_id.strip()
            ):
                reason = "worker_configuration_changed"
            return self.policy_view(row, reason)

    def configure(self, actor_id, body, key):
        self.authorize(actor_id)
        payload = {"actorId": actor_id, **body.model_dump(mode="json")}
        command = "goal-planning.configure"
        claim = self.repository.idempotency_claim(key, command, payload)
        if claim.response:
            return GoalPlanningPolicyView.model_validate(claim.response[1]["data"])
        try:
            with self.sessions.begin() as session:
                # Shared native write fence also arbitrates concurrent last-slot
                # intake and explicit revocation; no second model quota ledger.
                session.execute(
                    update(SystemStateRow)
                    .where(SystemStateRow.id == 1)
                    .values(updated_at=datetime.now(UTC))
                )
                self.authorize(actor_id, session)
                if self.unavailable_reason():
                    raise DomainError(
                        "GOAL_PLANNING_UNAVAILABLE",
                        "Configure an eligible bounded provider and explicit planning worker first.",
                        409,
                    )
                latest = session.scalar(
                    select(GoalPlanningPolicyRow)
                    .order_by(GoalPlanningPolicyRow.revision.desc())
                    .limit(1)
                    .with_for_update()
                )
                if body.expectedRevision != (latest.revision if latest else 0):
                    raise DomainError(
                        "GOAL_PLANNING_REVISION_CONFLICT",
                        "Refresh the planning consent before changing it.",
                        409,
                    )
                now = datetime.now(UTC)
                if not now < body.expiresAt <= now + timedelta(hours=24):
                    raise DomainError(
                        "GOAL_PLANNING_EXPIRY_INVALID",
                        "Planning consent must expire within 24 hours.",
                        422,
                    )
                worker = self.app.state.settings.autonomous_worker_actor_id.strip()
                self.runtime.authenticate_actor(worker)
                self._permissions(session)
                if latest:
                    latest.enabled = False
                policy_id = "goal-policy-" + uuid4().hex
                scope = {
                    "schemaVersion": "1.0",
                    "profile": "direct-v1",
                    "id": policy_id,
                    "revision": body.expectedRevision + 1,
                    "operatorId": actor_id,
                    "workerActorId": worker,
                    "maximumGoals": body.maximumGoals,
                    "expiresAt": body.expiresAt.isoformat(),
                    "budget": direct_goal_budget().model_dump(mode="json"),
                    "operatorReviewRequired": True,
                }
                row = GoalPlanningPolicyRow(
                    id=policy_id,
                    revision=body.expectedRevision + 1,
                    enabled=True,
                    operator_id=actor_id,
                    worker_actor_id=worker,
                    maximum_goals=body.maximumGoals,
                    admitted_goals=0,
                    expires_at=body.expiresAt,
                    policy_hash=sha256(canonical_json(scope).encode()).hexdigest(),
                    scope_json=scope,
                    created_at=now,
                )
                session.add(row)
                result = self.policy_view(row)
                self.app.state.task_leases._add_event(
                    session,
                    "goal.planning.consent.created",
                    "Configured bounded planning consent for new goals",
                    actor_identity_id=actor_id,
                    source="goal-planning",
                    payload={
                        "policyId": row.id,
                        "policyHash": row.policy_hash,
                        "maximumGoals": row.maximum_goals,
                        "expiresAt": body.expiresAt.isoformat(),
                    },
                )
                self.repository._store_idempotency(
                    session,
                    IdempotencyResult(
                        key=key,
                        command=command,
                        payload=payload,
                        status=200,
                        body={"data": result.model_dump(mode="json")},
                        lease_expires_at=claim.lease_expires_at,
                        resource_id=row.id,
                    ),
                )
                return result
        except BaseException:
            self.repository.idempotency_abandon(key, command, claim.lease_expires_at)
            raise

    def revoke(self, actor_id, policy_id, expected_hash):
        with self.sessions.begin() as session:
            session.execute(
                update(SystemStateRow)
                .where(SystemStateRow.id == 1)
                .values(updated_at=datetime.now(UTC))
            )
            self.authorize(actor_id, session)
            row = session.get(GoalPlanningPolicyRow, policy_id, with_for_update=True)
            if row is None or row.policy_hash != expected_hash:
                raise DomainError(
                    "GOAL_PLANNING_REVISION_CONFLICT", "The consent does not match.", 409
                )
            if row.enabled:
                row.enabled = False
                self.app.state.task_leases._add_event(
                    session,
                    "goal.planning.consent.revoked",
                    "Revoked bounded goal planning consent",
                    actor_identity_id=actor_id,
                    source="goal-planning",
                    payload={"policyId": row.id, "policyHash": row.policy_hash},
                )
            return self.policy_view(row)

    @staticmethod
    def _permissions(session):
        permissions = []
        for key in sorted(set(RUNTIME_PERMISSION_KEYS.values())):
            permission = session.scalar(
                select(IdentityPermissionRow).where(IdentityPermissionRow.stable_key == key)
            )
            if (
                permission is None
                or not permission.is_enabled
                or permission.resource_type != "task"
                or permission.action != key.removeprefix("runtime.")
            ):
                raise DomainError(
                    "GOAL_PLANNING_UNAVAILABLE",
                    "Native planning permissions must be explicitly configured first.",
                    409,
                )
            permissions.append(permission)
        return permissions

    def prepare_capture(self, actor_id, goal, task, consent):
        self.authorize(actor_id)
        with self.sessions() as session:
            policy = require_live_policy(
                session, consent.policyId, consent.policyHash, settings=self.app.state.settings
            )
            if policy.operator_id != actor_id:
                raise DomainError(
                    "GOAL_PLANNING_PERMISSION_DENIED",
                    "Use the operator that configured this consent.",
                    403,
                )
        if self.unavailable_reason():
            raise DomainError(
                "GOAL_PLANNING_UNAVAILABLE", "Explicit planning configuration is unavailable.", 409
            )
        task.statusMessage = "Saved under bounded planning consent"
        body = CreateContextAssemblyRequest.model_validate(
            {
                "taskId": task.id,
                "projectId": task.projectId or "jarvis-agent-ecosystem",
                "executionBudget": direct_goal_budget().model_dump(mode="json"),
                "allowedResultType": "structured_output",
                "completionCriteria": "Produce a bounded planning review with recommendations, risks and explicit assumptions.",
                "toolAvailabilitySummary": {
                    "prohibited_tools": ["shell", "browser", "filesystem", "external_actions"]
                },
                "policy": {
                    "maximumContextTokens": 4096,
                    "estimatedTokenBudget": 4096,
                    "reservedOutputTokens": 1536,
                },
                "sources": [
                    {
                        "sourceId": "goal-request-" + goal.id,
                        "sourceType": "manual_note",
                        "trustLevel": "operator_instruction",
                        "title": "Operator supplied goal",
                        "content": task.request,
                        "contentHash": sha256(task.request.encode()).hexdigest(),
                        "metadata": {
                            "projectId": task.projectId or "jarvis-agent-ecosystem",
                            "approved": True,
                            "truncationAllowed": False,
                        },
                    }
                ],
            }
        )
        assembly = self.app.state.context_assembler.assemble(task, body)
        providers = direct_providers(self.app.state.model_router)
        try:
            validate_direct_assembly(
                assembly,
                tuple(
                    self.app.state.model_router.model_assignments.get(provider.name, {}).get("chat")
                    or provider.default_model
                    for provider in providers
                    if provider.default_model
                ),
            )
        except ValueError as error:
            raise DomainError("EXECUTION_BUDGET_EXCEEDED", str(error), 422) from None
        goal.planning = GoalPlanningLink(
            policyId=consent.policyId, policyHash=consent.policyHash, runId="run-" + goal.id
        )

        def effects(session):
            self.authorize(actor_id, session)
            policy = require_live_policy(
                session, consent.policyId, consent.policyHash, settings=self.app.state.settings
            )
            if policy.operator_id != actor_id or self.unavailable_reason():
                raise DomainError(
                    "GOAL_PLANNING_CONSENT_UNAVAILABLE", "Captured planning authority changed.", 409
                )
            state = session.get(SystemStateRow, 1)
            if state is None or state.emergency_stop:
                raise DomainError(
                    "EMERGENCY_STOP_ACTIVE", "Emergency stop blocks planning admission.", 423
                )
            if policy.admitted_goals >= policy.maximum_goals:
                raise DomainError(
                    "GOAL_PLANNING_CONSENT_EXHAUSTED",
                    "This consent has reached its maximum number of goals.",
                    409,
                )
            self.repository._persist_created_context(session, assembly, task)
            policy.admitted_goals += 1
            session.add(
                GoalPlanningAdmissionRow(
                    goal_id=goal.id,
                    task_id=task.id,
                    policy_id=policy.id,
                    policy_hash=policy.policy_hash,
                    worker_actor_id=policy.worker_actor_id,
                    run_id=goal.planning.runId,
                    task_input_hash=task_input_hash(task),
                    context_assembly_id=assembly.id,
                    context_input_hash=assembly.inputHash,
                    last_error_code=None,
                    created_at=goal.createdAt,
                )
            )
            for permission in self._permissions(session):
                self.identity.assign_permission_in_session(
                    session,
                    policy.worker_actor_id,
                    AssignPermissionRequest(
                        permission_id=permission.id,
                        effect="allow",
                        resource_type="task",
                        resource_id=task.id,
                        starts_at=goal.createdAt,
                        expires_at=utc(policy.expires_at),
                        assigned_by=actor_id,
                        reason="Captured bounded goal consent " + policy.id,
                    ),
                )
            for key in sorted(set(RUNTIME_PERMISSION_KEYS.values())):
                if not self.identity.check_permission_resource_access(
                    policy.worker_actor_id, key, "task", task.id, session=session
                ).allowed:
                    raise DomainError(
                        "GOAL_PLANNING_PERMISSION_DENIED",
                        "Native planning policy denies the captured task.",
                        403,
                    )
            event = ContextAssemblyEventPayload(
                assemblyId=assembly.id,
                status=assembly.status,
                requestHash=assembly.requestHash,
                includedSourceCount=assembly.report.includedSourceCount,
                excludedSourceCount=assembly.report.excludedSourceCount,
                redactionCount=assembly.report.redactionCount,
                injectionFindingCount=assembly.report.injectionFindingCount,
                conflictCount=assembly.report.conflictCount,
            ).model_dump(mode="json")
            self.app.state.task_leases._add_event(
                session,
                "context.assembly.created",
                "Captured bounded goal context",
                task_id=task.id,
                actor_identity_id=actor_id,
                source="goal-planning",
                payload=event,
            )

        return effects

    def admit(self, actor_id, goal_id):
        """Explicit submission/retry only; no background polling or new scheduler."""
        self.authorize(actor_id)
        with self.sessions() as session:
            row = session.get(GoalPlanningAdmissionRow, goal_id)
            if row is None:
                return
            policy = session.get(GoalPlanningPolicyRow, row.policy_id)
            if policy.operator_id != actor_id:
                raise DomainError(
                    "GOAL_PLANNING_PERMISSION_DENIED",
                    "The captured consent belongs to another operator.",
                    403,
                )
            run_id, worker = row.run_id, row.worker_actor_id
            task_id, context_id = row.task_id, row.context_assembly_id
            created_at = utc(row.created_at)
            context = ContextAssembly.model_validate(
                session.get(ContextAssemblyRow, context_id).payload
            )
        try:
            current = self.runtime.repository.load_run(run_id)
            if current is not None and current.state.value != "created":
                return
            with self.sessions() as session:
                require_goal_consent(
                    session,
                    task_id,
                    run_id=run_id,
                    actor_id=worker,
                    settings=self.app.state.settings,
                )
            if context.status != "completed":
                raise DomainError(
                    "CONTEXT_ASSEMBLY_REVIEW_REQUIRED",
                    "The captured goal context requires review.",
                    409,
                )
            actor = self.runtime.authenticate_actor(worker)
            specification = AgentRunSpecification(
                run_id=run_id,
                task_id=task_id,
                agent_id=worker,
                requested_operation="Bounded goal planning",
                created_at=created_at,
                idempotency_key="goal-plan-" + goal_id,
                maximum_permitted_attempts=1,
                autonomous_execution=AutonomousExecutionSpecification(
                    execution_type="planning_review",
                    context_assembly_id=context_id,
                    response_format="planning_review_json_v1",
                    maximum_provider_requests=1,
                    maximum_repair_calls=0,
                    maximum_output_tokens=1536,
                    maximum_execution_seconds=300,
                ),
            )
            if current is None:
                self.runtime.handle_authorized(
                    CreateAgentRunCommand(
                        specification=specification,
                        command_id="goal-create-" + goal_id,
                        timestamp=created_at,
                        actor_reference=worker,
                    ),
                    actor,
                    require_execution_enabled=True,
                )
            current = self.runtime.repository.load_run(run_id)
            self.runtime.handle_authorized(
                QueueAgentRunCommand(
                    run_id=run_id,
                    command_id="goal-queue-" + goal_id,
                    expected_run_version=current.version,
                    timestamp=created_at,
                    actor_reference=worker,
                    detail="Operator submitted a goal under exact bounded consent",
                ),
                actor,
                require_execution_enabled=True,
            )
            with self.sessions.begin() as session:
                session.get(GoalPlanningAdmissionRow, goal_id).last_error_code = None
        except (DomainError, AgentRuntimeError) as error:
            with self.sessions.begin() as session:
                session.get(GoalPlanningAdmissionRow, goal_id).last_error_code = error.code

    def admit_saved(self, actor_id, goal_id):
        """A later admission refusal cannot erase a committed creation receipt."""
        try:
            self.admit(actor_id, goal_id)
        except (DomainError, AgentRuntimeError) as error:
            with self.sessions.begin() as session:
                row = session.get(GoalPlanningAdmissionRow, goal_id)
                if row is not None:
                    policy = session.get(GoalPlanningPolicyRow, row.policy_id)
                    if policy.operator_id == actor_id:
                        row.last_error_code = error.code

    def admission_view(self, actor_id, goal_id):
        self.authorize(actor_id)
        with self.sessions() as session:
            row = session.get(GoalPlanningAdmissionRow, goal_id)
            if row is None:
                raise DomainError(
                    "GOAL_PLANNING_NOT_FOUND", "This goal was saved without planning consent.", 404
                )
            policy = session.get(GoalPlanningPolicyRow, row.policy_id)
            if policy.operator_id != actor_id:
                raise DomainError(
                    "GOAL_PLANNING_PERMISSION_DENIED",
                    "The captured consent belongs to another operator.",
                    403,
                )
            snapshot = self.runtime.repository.load_run(row.run_id)
            error_code = (
                row.last_error_code
                if snapshot is None or snapshot.state.value == "created"
                else None
            )
            if snapshot is None or snapshot.state.value in {
                "created",
                "queued",
                "claimed",
                "starting",
                "running",
                "retry_waiting",
            }:
                try:
                    require_goal_consent(
                        session,
                        row.task_id,
                        run_id=row.run_id,
                        actor_id=row.worker_actor_id,
                        settings=self.app.state.settings,
                    )
                except DomainError as error:
                    error_code = error.code
            return GoalPlanningAdmissionView(
                goalId=goal_id,
                taskId=row.task_id,
                runId=row.run_id,
                state=snapshot.state.value
                if snapshot
                else ("blocked" if row.last_error_code else "not_admitted"),
                errorCode=error_code,
            )
