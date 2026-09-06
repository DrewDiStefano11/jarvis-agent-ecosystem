import json
import logging
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field

from app.catalog.taxonomy import map_tags, satisfies
from app.identity.service import IdentityService
from app.model_providers.budget import TaskBudget
from app.model_providers.contracts import (
    MessageRole,
    ModelCapability,
    ModelExecutionRequest,
    ModelMessage,
    ModelOutputSchema,
)
from app.model_providers.router import ModelRouter, RoutingRequirements
from app.models.domain import Task
from app.models.team_selection import TeamSelectionRationale, TeamSelectionRecord
from app.repositories.protocols import Repository
from app.services.events import EventBroker

logger = logging.getLogger(__name__)


class RequiredCapabilitiesResult(BaseModel):
    required: list[str] = Field(
        description="List of capability keys explicitly required to complete the core objective."
    )
    optional: list[str] = Field(
        description="List of capability keys that would be beneficial but are not strictly required."
    )
    reasoning_summary: str = Field(
        description="Brief explanation of why these capabilities were chosen."
    )


class TeamSelectionService:
    def __init__(
        self,
        repository: Repository,
        identity_service: IdentityService,
        model_router: ModelRouter,
        event_broker: EventBroker | None = None,
    ) -> None:
        self.repository = repository
        self.identity_service = identity_service
        self.model_router = model_router
        self.event_broker = event_broker

    async def _infer_capabilities(self, task: Task) -> tuple[list[str], list[str], str]:
        schema = RequiredCapabilitiesResult.model_json_schema()
        output_schema = ModelOutputSchema(name="required_capabilities", json_schema=schema)

        messages = [
            ModelMessage(
                role=MessageRole.SYSTEM,
                content=(
                    "You are the Jarvis AI Hub Team Selector. Your job is to read the task objective "
                    "and determine exactly which capabilities are required and which are optional. "
                    "Use ONLY canonical capability keys (e.g. software.backend, research.market). "
                    "Do not invent new capabilities. Keep requirements minimal and strictly bounded "
                    "to the task."
                ),
            ),
            ModelMessage(
                role=MessageRole.USER,
                content=f"Task: {task.title}\nDescription: {task.description}\nRequest: {task.request}",
            ),
        ]

        request = ModelExecutionRequest(
            messages=messages,
            output_schema=output_schema,
            prefer_no_reasoning=True,
            temperature=0.0,
            task_id=task.id,
        )

        requirements = RoutingRequirements(
            required_capability=ModelCapability.CHAT,
            prefer_local=True,
            allow_remote=False,
            allow_fallback=False,
        )
        budget = TaskBudget(maximum_requests=1, maximum_output_tokens=1024)

        try:
            response = await self.model_router.execute(
                request=request, requirements=requirements, budget=budget
            )
            payload = json.loads(response.content)
            result = RequiredCapabilitiesResult.model_validate(payload)
            # map_tags returns (valid, invalid)
            valid_req, _ = map_tags(result.required)
            valid_opt, _ = map_tags(result.optional)
            return sorted(set(valid_req)), sorted(set(valid_opt)), result.reasoning_summary
        except Exception as e:
            logger.error("Capability inference failed", exc_info=e)
            # If model is unavailable or malformed, return empty or a default fallback if known
            return [], [], "Failed to infer capabilities from objective."

    def _select_manager(self, task: Task, active_workforce: list[dict]) -> str | None:
        if task.assignedManagerId:
            # Check if assigned manager is eligible
            manager = next((a for a in active_workforce if a["id"] == task.assignedManagerId), None)
            if manager:
                return manager["id"]

        # Default manager is jarvis planner identity
        jarvis_agents = [
            a
            for a in active_workforce
            if a["agent_type"] == "planner" and "system" in a.get("role", "").lower()
        ]
        if not jarvis_agents:
            jarvis_agents = [a for a in active_workforce if a["agent_type"] == "planner"]

        if jarvis_agents:
            # Deterministic tie-break
            jarvis_agents.sort(key=lambda a: a["stable_key"])
            return jarvis_agents[0]["id"]
        return None

    def _select_specialists(
        self, required: list[str], active_workforce: list[dict], pinned: list[str]
    ) -> tuple[list[dict], list[str]]:
        # A deterministic greedy set-cover
        selected_specialists: list[dict] = []
        uncovered = set(required)

        # 1. Start with pinned specialists
        for p in pinned:
            agent = next((a for a in active_workforce if a["id"] == p), None)
            if agent:
                selected_specialists.append(agent)
                for cap in agent["capabilities"]:
                    # mark descendants as covered if parent is required
                    covered_reqs = [r for r in uncovered if satisfies(cap, r)]
                    for r in covered_reqs:
                        uncovered.discard(r)

        # Filter candidates: exclude already selected
        candidates = [
            a for a in active_workforce if a["id"] not in pinned and a["agent_type"] != "planner"
        ]

        while uncovered:
            best_candidate = None
            best_uncovered_count = 0
            best_total_caps = 9999

            for agent in candidates:
                # How many uncovered requirements does this agent satisfy?
                caps = agent["capabilities"]
                covers = {r for r in uncovered for c in caps if satisfies(c, r)}
                if not covers:
                    continue

                uncovered_count = len(covers)
                total_caps = len(caps)

                # We want to maximize uncovered_count, minimize total_caps (least overlap),
                # then stable_key alphabetically (deterministic).

                better = False
                if uncovered_count > best_uncovered_count:
                    better = True
                elif uncovered_count == best_uncovered_count:
                    if total_caps < best_total_caps:
                        better = True
                    elif total_caps == best_total_caps and best_candidate is not None:
                        if agent["stable_key"] < best_candidate["stable_key"]:
                            better = True

                if better:
                    best_candidate = agent
                    best_uncovered_count = uncovered_count
                    best_total_caps = total_caps

            if not best_candidate:
                # No more candidates can cover the remaining requirements
                break

            selected_specialists.append(best_candidate)
            candidates.remove(best_candidate)

            for cap in best_candidate["capabilities"]:
                covered_reqs = [r for r in uncovered if satisfies(cap, r)]
                for r in covered_reqs:
                    uncovered.discard(r)

            if len(selected_specialists) >= 6:
                break

        return selected_specialists, sorted(uncovered)

    async def assign_team(self, task: Task) -> Task:
        # Avoid duplicate work if we already have a successful team selection that matches the task
        # Re-selection is possible if teamSelection status was blocked, etc.
        if task.teamSelection and task.teamSelection.status == "completed":
            return task

        required, optional, reason = await self._infer_capabilities(task)

        workforce = self.identity_service.workforce_snapshot(limit=100)
        manager_id = self._select_manager(task, workforce)

        pinned_specialists = [s for s in task.assignedAgentIds if s != manager_id]

        specialists, missing = self._select_specialists(required, workforce, pinned_specialists)

        status = "blocked_missing_capability" if missing else "completed"

        rationales = []
        if manager_id:
            rationales.append(
                TeamSelectionRationale(
                    agentId=manager_id,
                    rationale="Assigned as task manager.",
                    coveredCapabilities=[],
                )
            )

        for sp in specialists:
            caps = sp["capabilities"]
            covered = [r for r in required for c in caps if satisfies(c, r)]
            # Also optional
            covered_opt = [r for r in optional for c in caps if satisfies(c, r)]
            all_cov = sorted(set(covered + covered_opt))
            rationales.append(
                TeamSelectionRationale(
                    agentId=sp["id"],
                    rationale=f"Covers {', '.join(all_cov)}"
                    if all_cov
                    else "Explicitly pinned operator assignment.",
                    coveredCapabilities=all_cov,
                )
            )

        selected_agent_ids = [s["id"] for s in specialists]

        # Determine unique workforce fingerprint for the active workforce
        fingerprint = ",".join(
            sorted(f"{a['id']}:{a['catalog_revision_id'] or 'jarvis'}" for a in workforce)
        )

        selection = TeamSelectionRecord(
            selectionId=f"sel-{uuid4().hex[:12]}",
            taskId=task.id,
            status=status,
            requiredCapabilities=required,
            optionalCapabilities=optional,
            managerId=manager_id,
            selectedAgentIds=selected_agent_ids,
            rationaleSummaries=rationales,
            workforceFingerprint=fingerprint,
            createdAt=datetime.now(UTC),
            updatedAt=datetime.now(UTC),
        )

        task.teamSelection = selection

        # Only assign if not blocked? Actually, even if blocked, we might just fail the task execution or save it.
        # "Missing capability blocks selection. Return a structured selection state such as blocked_missing_capability. Expose this clearly to the user."
        # If blocked, we still persist the selection record so UI can show it.
        # Do we update task assignments? "The task's actual manager/specialist assignment must stay synchronized with the durable selection."
        task.assignedManagerId = manager_id
        task.assignedAgentIds = selected_agent_ids

        if self.event_broker:
            audit_summary = f"Team selected: {len(selected_agent_ids)} specialists"
            if status == "blocked_missing_capability":
                audit_summary = (
                    f"Team selection blocked: missing capabilities ({', '.join(missing)})"
                )

            await self.event_broker.emit(
                "team_selection.completed",
                {"task": task.model_dump(mode="json")},
                task.id,
                audit={"summary": audit_summary},
                updated_task=task,
            )

        return task
