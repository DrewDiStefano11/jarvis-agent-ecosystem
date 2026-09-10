"""Preparation boundary for coordinating an explicitly queued planning runtime."""

from app.agent_runtime.authorization import IdentityRuntimeAuthorizer
from app.catalog.taxonomy import satisfies
from app.coordination.repository import CoordinationRepository
from app.core.errors import DomainError
from app.db.models import ContextAssemblyRow, IdentityAgentRow
from app.decomposition.service import DecompositionService, fingerprint
from app.models.context import ContextAssembly
from app.models.decomposition import topological_keys


class CoordinatorService:
    def __init__(self, tasks, identities, runtime, router):
        self.repository = CoordinationRepository(tasks.session_factory)
        self.identities = identities
        self.runtime = runtime
        self.decomposition = DecompositionService(tasks, identities, router)
        self.authorizer = IdentityRuntimeAuthorizer(identities)

    def live_validator(self, actor):
        def validate(session, task, graph, run):
            # Use the existing authorizer inside the write transaction, including
            # its established administrative override and denial precedence.
            for operation in ("read", "claim"):
                self.authorizer.authorize(actor, operation, snapshot=run, session=session)
            request = run.specification.autonomous_execution
            team = task.teamSelection
            if (
                request is None
                or request.execution_type != "planning_review"
                or request.context_assembly_id != graph.contextAssemblyId
                or team is None
                or team.status != "completed"
                or team.selectionId != graph.teamSelectionId
                or team.managerId != run.specification.agent_id
            ):
                raise DomainError(
                    "COORDINATION_INPUT_CHANGED",
                    "Runtime, context and selected team must match the graph.",
                    409,
                )
            manager = session.get(IdentityAgentRow, team.managerId)
            if manager is None or manager.agent_type not in {"planner", "coordinator"}:
                raise DomainError(
                    "COORDINATION_MANAGER_INELIGIBLE",
                    "The selected manager must be a planning identity.",
                    409,
                )
            context_row = session.get(ContextAssemblyRow, graph.contextAssemblyId)
            if context_row is None or context_row.task_id != task.id:
                raise DomainError(
                    "COORDINATION_CONTEXT_REQUIRED", "Grounded task context is required.", 409
                )
            context = ContextAssembly.model_validate(context_row.payload)
            if context.status != "completed" or context.modelRequest is None:
                raise DomainError(
                    "COORDINATION_CONTEXT_REQUIRED", "Completed grounded context is required.", 409
                )
            workforce = self.decomposition._team(task, session)
            if graph.inputFingerprint != fingerprint(
                self.decomposition._input(task, context, workforce)
            ):
                raise DomainError(
                    "COORDINATION_STALE_PLAN", "Planning inputs changed; reconcile the graph.", 409
                )
            by_id = {agent["id"]: agent for agent in workforce}
            topological_keys(graph.subtasks)
            if team.managerId not in by_id or by_id[team.managerId]["catalog_revision_id"]:
                raise DomainError(
                    "COORDINATION_MANAGER_INELIGIBLE", "The manager is no longer eligible.", 409
                )
            for node in graph.subtasks:
                agent = by_id.get(node.assignedAgentId)
                if (
                    agent is None
                    or node.assignedAgentId not in team.selectedAgentIds
                    or node.assignedAgentId == team.managerId
                    or agent["agent_type"] not in {"specialist", "worker", "reviewer"}
                    or node.parentTaskId != task.id
                    or not all(
                        any(satisfies(offered, required) for offered in agent["capabilities"])
                        for required in node.requiredCapabilities
                    )
                ):
                    raise DomainError(
                        "COORDINATION_ASSIGNMENT_INELIGIBLE",
                        "The assigned specialist is no longer eligible.",
                        409,
                    )
            return frozenset(by_id)

        return validate

    def prepare(self, task_id, decomposition_id, run_id, actor):
        return self.repository.initialize(
            task_id, decomposition_id, run_id, self.live_validator(actor)
        )

    def claim_ready(self, record_id, fence, actor):
        return self.repository.claim_ready(record_id, fence, self.live_validator(actor))
