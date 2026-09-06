from __future__ import annotations

import json
import logging
from hashlib import sha256
from typing import TYPE_CHECKING

from app.models.context import (
    ContextSource,
    ContextSourceMetadata,
    ContextSourceType,
    TrustLevel,
)

if TYPE_CHECKING:
    from app.core.config import Settings
    from app.identity.service import IdentityService

logger = logging.getLogger(__name__)

# Maximum agents to include in workforce snapshot.
_MAX_AGENTS = 20

# Maximum prior results to include.
_MAX_PRIOR_RESULTS = 2

# Maximum summary characters from a prior result.
_MAX_RESULT_SUMMARY_CHARS = 500


def _hash(content: str) -> str:
    return sha256(content.encode("utf-8")).hexdigest()


def _source(
    *,
    source_id: str,
    source_type: ContextSourceType,
    trust_level: TrustLevel,
    title: str,
    content: str,
    inclusion_priority: int = 500,
    project_id: str | None = None,
    truncation_allowed: bool = True,
) -> ContextSource:
    """Build a well-formed context source with correct hash and metadata."""
    return ContextSource(
        sourceId=source_id,
        sourceType=source_type,
        trustLevel=trust_level,
        title=title,
        content=content,
        contentHash=_hash(content),
        metadata=ContextSourceMetadata(
            projectId=project_id,
            approved=True,
            inclusionPriority=inclusion_priority,
            truncationAllowed=truncation_allowed,
        ),
    )


class ContextEnricher:
    """Builds authoritative system-state context sources for planning assemblies.

    Sources are generated server-side from real system state. The browser
    cannot forge these; they are injected by the API when a context assembly
    is created.
    """

    def __init__(
        self,
        *,
        identity_service: IdentityService,
        settings: Settings,
        repository: object,
        tool_registry: object | None = None,
    ) -> None:
        self.identity_service = identity_service
        self.settings = settings
        self.repository = repository
        self.tool_registry = tool_registry

    def enrich(self, task_id: str, actor_id: str | None = None) -> list[ContextSource]:
        """Build bounded authoritative context sources for a task.

        Each subsystem failure is isolated: a failed snapshot logs a warning
        and is skipped rather than failing the entire enrichment.
        """
        sources: list[ContextSource] = []
        project_id: str | None = None

        # 1. Task state
        try:
            task_source, project_id = self._task_state(task_id)
            if task_source is not None:
                sources.append(task_source)
        except Exception:
            logger.warning("Context enrichment: task state failed for %s", task_id, exc_info=True)

        # 2. Workforce snapshot
        try:
            workforce = self._workforce_snapshot(project_id)
            if workforce is not None:
                sources.append(workforce)
        except Exception:
            logger.warning("Context enrichment: workforce snapshot failed", exc_info=True)

        # 3. Runtime state
        try:
            sources.append(self._runtime_state())
        except Exception:
            logger.warning("Context enrichment: runtime state failed", exc_info=True)

        # 4. Tool policy
        try:
            sources.append(self._tool_policy())
        except Exception:
            logger.warning("Context enrichment: tool policy failed", exc_info=True)

        # 5. Permission summary
        try:
            if actor_id:
                perm = self._permission_summary(actor_id, task_id)
                if perm is not None:
                    sources.append(perm)
        except Exception:
            logger.warning(
                "Context enrichment: permission summary failed for %s on %s",
                actor_id,
                task_id,
                exc_info=True,
            )

        # 6. Persistence/recovery facts
        try:
            sources.append(self._persistence_facts())
        except Exception:
            logger.warning("Context enrichment: persistence facts failed", exc_info=True)

        # 7. Prior results
        try:
            sources.extend(self._prior_results(task_id))
        except Exception:
            logger.warning(
                "Context enrichment: prior results failed for %s", task_id, exc_info=True
            )

        return sources

    # ------------------------------------------------------------------
    # Individual snapshot builders
    # ------------------------------------------------------------------

    def _task_state(self, task_id: str) -> tuple[ContextSource | None, str | None]:
        """Build task state snapshot. Returns (source, project_id)."""
        repo = self.repository
        task = None
        if hasattr(repo, "get_task_durable"):
            task = repo.get_task_durable(task_id)
        elif hasattr(repo, "tasks"):
            task = repo.tasks.get(task_id)
        if task is None:
            return None, None

        lines = [
            f"Task ID: {task.id}",
            f"Title: {task.title}",
            f"Description: {task.description}",
            f"Priority: {task.priority}",
            f"Project: {task.projectId or 'none'}",
            f"Status: {task.status}",
            f"Retry count: {task.retryCount}",
        ]
        if task.correctionOfTaskId:
            lines.append(f"Correction of task: {task.correctionOfTaskId}")
        if task.parentTaskId:
            lines.append(f"Parent task: {task.parentTaskId}")
        if task.childTaskIds:
            lines.append(f"Child tasks: {', '.join(task.childTaskIds)}")
        if task.assignedManagerId:
            lines.append(f"Assigned manager: {task.assignedManagerId}")
        if task.assignedAgentIds:
            lines.append(f"Assigned agents: {', '.join(task.assignedAgentIds)}")

        content = "\n".join(lines) + "\n"
        return (
            _source(
                source_id="system-task-state",
                source_type=ContextSourceType.TASK_REQUEST,
                trust_level=TrustLevel.TASK_REQUEST,
                title="Current task state",
                content=content,
                project_id=task.projectId,
            ),
            task.projectId,
        )

    def _workforce_snapshot(self, project_id: str | None) -> ContextSource | None:
        """Build workforce snapshot of active, enabled agents."""
        agents: list[object] = []
        if hasattr(self.identity_service, "list_agents"):
            agents = self.identity_service.list_agents(offset=0, limit=_MAX_AGENTS + 10)
        elif hasattr(self.repository, "agents"):
            agents = list(self.repository.agents.values())

        active = [
            a
            for a in agents
            if getattr(a, "lifecycle_state", "") == "active" and getattr(a, "is_enabled", False)
        ][:_MAX_AGENTS]

        if not active:
            return _source(
                source_id="system-workforce-snapshot",
                source_type=ContextSourceType.SYSTEM_POLICY,
                trust_level=TrustLevel.TRUSTED_CONFIGURATION,
                title="Workforce snapshot",
                content="No active agents are currently registered.\n",
                project_id=project_id,
            )

        lines = [f"Active agents ({len(active)}):"]
        for agent in active:
            lines.append(
                f"- {getattr(agent, 'display_name', '?')} "
                f"(key={getattr(agent, 'stable_key', '?')}, "
                f"type={getattr(agent, 'agent_type', '?')}, "
                f"status={getattr(agent, 'operational_status', '?')})"
            )
        content = "\n".join(lines) + "\n"

        return _source(
            source_id="system-workforce-snapshot",
            source_type=ContextSourceType.SYSTEM_POLICY,
            trust_level=TrustLevel.TRUSTED_CONFIGURATION,
            title="Workforce snapshot",
            content=content,
            project_id=project_id,
        )

    def _runtime_state(self) -> ContextSource:
        """Build runtime configuration state from actual settings."""
        s = self.settings
        lines = [
            f"Model execution mode: {s.model_execution_mode}",
            f"Ollama enabled: {s.model_ollama_enabled}",
            f"Ollama model: {s.model_ollama_model}",
            f"Remote fallback allowed: {s.model_allow_remote}",
            f"Prefer local: {s.model_prefer_local}",
            f"Provider priority: {s.model_provider_priority}",
            f"Autonomous worker enabled: {s.autonomous_worker_enabled}",
            f"Maximum execution seconds: {s.autonomous_worker_max_execution_seconds}",
            f"Maximum repair calls: {s.autonomous_worker_max_repair_calls}",
        ]
        # Derive local planning availability
        local_available = (
            s.model_execution_mode == "local_only"
            and s.model_ollama_enabled
            and s.autonomous_worker_enabled
        )
        lines.insert(0, f"Local planning available: {local_available}")
        content = "\n".join(lines) + "\n"

        return _source(
            source_id="system-runtime-state",
            source_type=ContextSourceType.SYSTEM_POLICY,
            trust_level=TrustLevel.TRUSTED_CONFIGURATION,
            title="Runtime and model configuration",
            content=content,
        )

    def _tool_policy(self) -> ContextSource:
        """Build tool execution policy from settings and registry."""
        s = self.settings
        lines = [f"Tool execution enabled: {s.tool_execution_enabled}"]

        if not s.tool_execution_enabled:
            lines.append("Tool execution is completely disabled. No workspace tools are available.")
        else:
            # Parse workspace configuration
            try:
                workspaces = json.loads(s.tool_workspaces_json)
                if workspaces:
                    lines.append(f"Configured workspaces: {len(workspaces)}")
                    for alias in sorted(workspaces):
                        lines.append(f"  - {alias}")
                else:
                    lines.append("No workspaces are configured.")
            except (json.JSONDecodeError, TypeError):
                lines.append("Workspace configuration could not be parsed.")

            # Check tool registry for workspace details
            if self.tool_registry and hasattr(self.tool_registry, "workspaces"):
                try:
                    ws_info = self.tool_registry.workspaces()
                    for ws in ws_info:
                        ready = "ready" if ws.ready else f"not ready ({ws.reasonCode})"
                        lines.append(
                            f"  Workspace '{ws.workspaceId}': {ready}, "
                            f"tools={ws.allowedTools}, "
                            f"read={ws.readPrefixes}, "
                            f"write={ws.writePrefixes}"
                        )
                except Exception:
                    pass

        lines.extend(
            [
                "Prohibited execution classes: shell, browser, external_actions",
                "All workspace tool execution requires explicit operator authorization",
                "Maximum file size: 64 KB per operation",
            ]
        )
        content = "\n".join(lines) + "\n"

        return _source(
            source_id="system-tool-policy",
            source_type=ContextSourceType.SYSTEM_POLICY,
            trust_level=TrustLevel.TRUSTED_CONFIGURATION,
            title="Tool execution policy",
            content=content,
        )

    def _permission_summary(self, actor_id: str, task_id: str) -> ContextSource | None:
        """Build safe permission summary for actor on task."""
        permission_keys = [
            "runtime.read",
            "runtime.create",
            "runtime.queue",
            "runtime.execute",
            "runtime.complete",
        ]
        granted: list[str] = []
        denied: list[str] = []
        explicit_denials = False

        for key in permission_keys:
            try:
                decision = self.identity_service.check_permission_resource_access(
                    actor_id, key, "task", task_id
                )
                if decision.allowed:
                    granted.append(key)
                else:
                    denied.append(key)
                    if decision.matched_denials:
                        explicit_denials = True
            except Exception:
                denied.append(f"{key} (evaluation failed)")

        lines = [
            f"Actor: {actor_id}",
            f"Task: {task_id}",
            f"Granted permissions: {', '.join(granted) if granted else 'none'}",
            f"Denied permissions: {', '.join(denied) if denied else 'none'}",
        ]
        if explicit_denials:
            lines.append("Note: explicit deny rules are in effect for this actor")
        lines.append("Workspace execution requires explicit operator authorization")
        lines.append("External actions are prohibited")
        content = "\n".join(lines) + "\n"

        return _source(
            source_id="system-permission-summary",
            source_type=ContextSourceType.SYSTEM_POLICY,
            trust_level=TrustLevel.TRUSTED_CONFIGURATION,
            title="Permission summary for this task",
            content=content,
        )

    def _persistence_facts(self) -> ContextSource:
        """Build persistence/recovery fact summary from actual implementation."""
        lines = [
            "Task state is durable (SQLite with Alembic migrations)",
            "Runtime state and checkpoints are durable",
            "Model execution results are persisted with SHA-256 integrity hashes",
            "Lease-fenced retries prevent duplicate execution",
            "Emergency stop capability exists and is respected",
            "Completed results survive API/worker restart",
            "Recovery from interrupted executions is automatic",
        ]
        content = "\n".join(lines) + "\n"

        return _source(
            source_id="system-persistence-facts",
            source_type=ContextSourceType.SYSTEM_POLICY,
            trust_level=TrustLevel.TRUSTED_CONFIGURATION,
            title="Persistence and recovery capabilities",
            content=content,
        )

    def _prior_results(self, task_id: str) -> list[ContextSource]:
        """Build prior model execution summaries for this task."""
        sources: list[ContextSource] = []
        results: list[object] = []

        # Try the model execution repository
        if hasattr(self.repository, "model_execution_repository"):
            repo = self.repository.model_execution_repository
            if hasattr(repo, "list_for_task"):
                results = repo.list_for_task(task_id)
        elif hasattr(self.repository, "model_executions"):
            all_executions = self.repository.model_executions
            if isinstance(all_executions, dict):
                results = [
                    v for v in all_executions.values() if getattr(v, "taskId", "") == task_id
                ]

        # Filter to completed results and take most recent
        completed = [
            r for r in results if getattr(r, "stage", "") in ("completed", "human_review_required")
        ]
        # Sort by completedAt descending
        completed.sort(
            key=lambda r: getattr(r, "completedAt", "") or getattr(r, "createdAt", ""),
            reverse=True,
        )
        completed = completed[:_MAX_PRIOR_RESULTS]

        for i, result in enumerate(completed):
            result_summary = ""
            result_obj = getattr(result, "result", None)
            if result_obj is not None:
                if hasattr(result_obj, "summary"):
                    result_summary = str(result_obj.summary)[:_MAX_RESULT_SUMMARY_CHARS]
                elif isinstance(result_obj, dict):
                    result_summary = str(result_obj.get("summary", ""))[:_MAX_RESULT_SUMMARY_CHARS]

            lines = [
                f"Execution: {getattr(result, 'executionId', '?')}",
                f"Stage: {getattr(result, 'stage', '?')}",
                f"Provider: {getattr(result, 'provider', '?')}",
                f"Model: {getattr(result, 'model', '?')}",
            ]
            failure = getattr(result, "failureCode", None)
            if failure:
                lines.append(f"Failure code: {failure}")
            if result_summary:
                lines.append(f"Summary: {result_summary}")
            review = getattr(result, "requiresHumanReview", None)
            if review is not None:
                lines.append(f"Requires human review: {review}")

            content = "\n".join(lines) + "\n"
            sources.append(
                _source(
                    source_id=f"prior-results-{i}",
                    source_type=ContextSourceType.PRIOR_MODEL_OUTPUT,
                    trust_level=TrustLevel.PRIOR_MODEL_OUTPUT,
                    title=f"Prior model result #{i + 1}",
                    content=content,
                    inclusion_priority=100,
                )
            )

        return sources
