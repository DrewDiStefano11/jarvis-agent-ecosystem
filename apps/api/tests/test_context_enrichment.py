from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.context.enrichment import ContextEnricher
from app.models.context import ContextSourceType


@dataclass
class MockTask:
    id: str
    title: str = "Test Task"
    description: str = "Test description"
    priority: str = "normal"
    projectId: str | None = None
    status: str = "queued"
    retryCount: int = 0
    correctionOfTaskId: str | None = None
    parentTaskId: str | None = None
    childTaskIds: list[str] = None
    assignedManagerId: str | None = None
    assignedAgentIds: list[str] = None

    def __post_init__(self):
        if self.childTaskIds is None:
            self.childTaskIds = []
        if self.assignedAgentIds is None:
            self.assignedAgentIds = []


@dataclass
class MockAgent:
    id: str
    display_name: str = "Agent"
    stable_key: str = "agent"
    agent_type: str = "worker"
    lifecycle_state: str = "active"
    operational_status: str = "idle"
    is_enabled: bool = True


@dataclass
class MockPermissionDecision:
    allowed: bool
    matched_denials: bool = False


class MockIdentityService:
    def __init__(self, agents: list[MockAgent] = None, permissions: dict = None):
        self.agents = agents or []
        self.permissions = permissions or {}

    def list_agents(self, offset: int, limit: int) -> list[MockAgent]:
        return self.agents

    def check_permission_resource_access(
        self, actor_id: str, permission: str, resource_type: str, resource_id: str
    ) -> MockPermissionDecision:
        if permission == "throw":
            raise RuntimeError("Database error")
        allowed = self.permissions.get(f"{actor_id}:{permission}:{resource_id}", False)
        denials = self.permissions.get(f"{actor_id}:{permission}:{resource_id}:denials", False)
        return MockPermissionDecision(allowed=allowed, matched_denials=denials)


class MockSettings:
    model_execution_mode: str = "local_only"
    model_ollama_enabled: bool = True
    model_ollama_model: str = "qwen2"
    model_allow_remote: bool = False
    model_prefer_local: bool = True
    model_provider_priority: str = "local"
    autonomous_worker_enabled: bool = True
    autonomous_worker_max_execution_seconds: int = 300
    autonomous_worker_max_repair_calls: int = 1
    tool_execution_enabled: bool = True
    tool_workspaces_json: str = "{}"


class MockModelExecutionRepo:
    def __init__(self, executions: list = None):
        self.executions = executions or []

    def list_for_task(self, task_id: str) -> list:
        return [e for e in self.executions if getattr(e, "taskId", "") == task_id]


class MockRepository:
    def __init__(self, task: MockTask = None, executions: list = None):
        self.task = task
        self.model_execution_repository = MockModelExecutionRepo(executions)

    def get_task_durable(self, task_id: str) -> MockTask | None:
        if task_id == "throw":
            raise RuntimeError("Database error")
        return self.task


class MockWorkspace:
    def __init__(self, wid, ready=True, tools=None, read=None, write=None):
        self.workspaceId = wid
        self.ready = ready
        self.reasonCode = None if ready else "error"
        self.allowedTools = tools or []
        self.readPrefixes = read or []
        self.writePrefixes = write or []


class MockToolRegistry:
    def __init__(self, workspaces=None):
        self._workspaces = workspaces or []

    def workspaces(self):
        return self._workspaces


@pytest.fixture
def base_enricher():
    return ContextEnricher(
        identity_service=MockIdentityService(),
        settings=MockSettings(),
        repository=MockRepository(),
        tool_registry=MockToolRegistry(),
    )


# ---------------------------------------------------------------------------
# 1. TASK STATE TESTS (4 tests)
# ---------------------------------------------------------------------------


def test_task_state_included_when_task_exists():
    task = MockTask(id="task-123", projectId="proj-1", status="queued")
    enricher = ContextEnricher(
        identity_service=MockIdentityService(),
        settings=MockSettings(),
        repository=MockRepository(task),
        tool_registry=MockToolRegistry(),
    )
    sources = enricher.enrich("task-123")
    task_source = next(s for s in sources if s.sourceId == "system-task-state")
    assert task_source.sourceType == ContextSourceType.TASK_REQUEST
    assert "Task ID: task-123" in task_source.content
    assert task_source.metadata.projectId == "proj-1"


def test_task_state_skipped_when_task_missing(base_enricher):
    sources = base_enricher.enrich("missing")
    assert not any(s.sourceId == "system-task-state" for s in sources)


def test_task_state_includes_complex_hierarchy():
    task = MockTask(
        id="task-1",
        correctionOfTaskId="task-0",
        parentTaskId="task-p",
        childTaskIds=["task-c1", "task-c2"],
    )
    enricher = ContextEnricher(
        identity_service=MockIdentityService(),
        settings=MockSettings(),
        repository=MockRepository(task),
        tool_registry=MockToolRegistry(),
    )
    sources = enricher.enrich("task-1")
    task_source = next(s for s in sources if s.sourceId == "system-task-state")
    assert "Correction of task: task-0" in task_source.content
    assert "Parent task: task-p" in task_source.content
    assert "Child tasks: task-c1, task-c2" in task_source.content


def test_task_state_failure_is_isolated():
    enricher = ContextEnricher(
        identity_service=MockIdentityService(),
        settings=MockSettings(),
        repository=MockRepository(MockTask("task-1")),
        tool_registry=MockToolRegistry(),
    )
    sources = enricher.enrich("throw")
    assert not any(s.sourceId == "system-task-state" for s in sources)
    assert any(s.sourceId == "system-runtime-state" for s in sources)


# ---------------------------------------------------------------------------
# 2. WORKFORCE SNAPSHOT TESTS (3 tests)
# ---------------------------------------------------------------------------


def test_workforce_snapshot_includes_active_enabled_agents():
    agents = [
        MockAgent(id="1", display_name="A1", is_enabled=True, lifecycle_state="active"),
        MockAgent(id="2", display_name="A2", is_enabled=False, lifecycle_state="active"),
        MockAgent(id="3", display_name="A3", is_enabled=True, lifecycle_state="deleted"),
    ]
    enricher = ContextEnricher(
        identity_service=MockIdentityService(agents=agents),
        settings=MockSettings(),
        repository=MockRepository(MockTask("t1")),
        tool_registry=MockToolRegistry(),
    )
    sources = enricher.enrich("t1")
    wf = next(s for s in sources if s.sourceId == "system-workforce-snapshot")
    assert "A1" in wf.content
    assert "A2" not in wf.content
    assert "A3" not in wf.content


def test_workforce_snapshot_empty_state(base_enricher):
    sources = base_enricher.enrich("t1")
    wf = next(s for s in sources if s.sourceId == "system-workforce-snapshot")
    assert "No active agents are currently registered" in wf.content


def test_workforce_snapshot_capped_at_max_agents():
    agents = [MockAgent(id=str(i), display_name=f"Agent{i}") for i in range(30)]
    enricher = ContextEnricher(
        identity_service=MockIdentityService(agents=agents),
        settings=MockSettings(),
        repository=MockRepository(MockTask("t1")),
        tool_registry=MockToolRegistry(),
    )
    sources = enricher.enrich("t1")
    wf = next(s for s in sources if s.sourceId == "system-workforce-snapshot")
    assert "Active agents (20):" in wf.content
    assert "Agent19" in wf.content
    assert "Agent20" not in wf.content


# ---------------------------------------------------------------------------
# 3. RUNTIME STATE TESTS (3 tests)
# ---------------------------------------------------------------------------


def test_runtime_state_exposes_configuration():
    settings = MockSettings()
    settings.model_execution_mode = "remote_only"
    enricher = ContextEnricher(
        identity_service=MockIdentityService(),
        settings=settings,
        repository=MockRepository(),
        tool_registry=MockToolRegistry(),
    )
    sources = enricher.enrich("t1")
    rt = next(s for s in sources if s.sourceId == "system-runtime-state")
    assert "Model execution mode: remote_only" in rt.content
    assert "Local planning available: False" in rt.content


def test_runtime_state_derives_local_planning_availability():
    settings = MockSettings()
    settings.model_execution_mode = "local_only"
    settings.model_ollama_enabled = True
    settings.autonomous_worker_enabled = True
    enricher = ContextEnricher(
        identity_service=MockIdentityService(),
        settings=settings,
        repository=MockRepository(),
        tool_registry=MockToolRegistry(),
    )
    sources = enricher.enrich("t1")
    rt = next(s for s in sources if s.sourceId == "system-runtime-state")
    assert "Local planning available: True" in rt.content


def test_runtime_state_is_always_included(base_enricher):
    sources = base_enricher.enrich("t1")
    assert any(s.sourceId == "system-runtime-state" for s in sources)


# ---------------------------------------------------------------------------
# 4. TOOL POLICY TESTS (3 tests)
# ---------------------------------------------------------------------------


def test_tool_policy_reflects_disabled_state():
    settings = MockSettings()
    settings.tool_execution_enabled = False
    enricher = ContextEnricher(
        identity_service=MockIdentityService(),
        settings=settings,
        repository=MockRepository(),
        tool_registry=MockToolRegistry(),
    )
    sources = enricher.enrich("t1")
    tp = next(s for s in sources if s.sourceId == "system-tool-policy")
    assert "Tool execution is completely disabled" in tp.content


def test_tool_policy_includes_workspaces():
    settings = MockSettings()
    settings.tool_workspaces_json = '{"ws1": {}, "ws2": {}}'
    enricher = ContextEnricher(
        identity_service=MockIdentityService(),
        settings=settings,
        repository=MockRepository(),
        tool_registry=MockToolRegistry([MockWorkspace("ws1")]),
    )
    sources = enricher.enrich("t1")
    tp = next(s for s in sources if s.sourceId == "system-tool-policy")
    assert "Configured workspaces: 2" in tp.content
    assert "Workspace 'ws1': ready" in tp.content


def test_tool_policy_handles_invalid_json():
    settings = MockSettings()
    settings.tool_workspaces_json = "invalid"
    enricher = ContextEnricher(
        identity_service=MockIdentityService(),
        settings=settings,
        repository=MockRepository(),
        tool_registry=MockToolRegistry(),
    )
    sources = enricher.enrich("t1")
    tp = next(s for s in sources if s.sourceId == "system-tool-policy")
    assert "Workspace configuration could not be parsed." in tp.content


# ---------------------------------------------------------------------------
# 5. PERMISSION SUMMARY TESTS (4 tests)
# ---------------------------------------------------------------------------


def test_permission_summary_omitted_without_actor(base_enricher):
    sources = base_enricher.enrich("t1", actor_id=None)
    assert not any(s.sourceId == "system-permission-summary" for s in sources)


def test_permission_summary_includes_granted_and_denied():
    permissions = {
        "a1:runtime.read:t1": True,
        "a1:runtime.create:t1": False,
        "a1:runtime.queue:t1": False,
        "a1:runtime.queue:t1:denials": True,
    }
    enricher = ContextEnricher(
        identity_service=MockIdentityService(permissions=permissions),
        settings=MockSettings(),
        repository=MockRepository(),
        tool_registry=MockToolRegistry(),
    )
    sources = enricher.enrich("t1", actor_id="a1")
    ps = next(s for s in sources if s.sourceId == "system-permission-summary")
    assert "Granted permissions: runtime.read" in ps.content
    assert "runtime.create" in ps.content
    assert "explicit deny rules are in effect" in ps.content


def test_permission_summary_isolates_evaluation_errors():
    class ThrowingIdentityService(MockIdentityService):
        def check_permission_resource_access(
            self, actor_id: str, permission: str, resource_type: str, resource_id: str
        ):
            raise RuntimeError("Database error")

    enricher = ContextEnricher(
        identity_service=ThrowingIdentityService(),
        settings=MockSettings(),
        repository=MockRepository(),
        tool_registry=MockToolRegistry(),
    )
    sources = enricher.enrich("t1", actor_id="a1")
    # Should contain fail-closed permission summary
    ps = next(s for s in sources if s.sourceId == "system-permission-summary")
    assert "Granted permissions: none" in ps.content
    assert "(evaluation failed)" in ps.content

    # And it should still contain other things like system-runtime-state
    assert any(s.sourceId == "system-runtime-state" for s in sources)


def test_permission_summary_has_correct_metadata():
    enricher = ContextEnricher(
        identity_service=MockIdentityService({"a1:runtime.read:t1": True}),
        settings=MockSettings(),
        repository=MockRepository(MockTask("t1", projectId="proj-1")),
        tool_registry=MockToolRegistry(),
    )
    sources = enricher.enrich("t1", actor_id="a1")
    ps = next(s for s in sources if s.sourceId == "system-permission-summary")
    # Project ID is not injected into permission summary currently
    assert ps.metadata.approved is True


# ---------------------------------------------------------------------------
# 6. PERSISTENCE FACTS TESTS (1 test)
# ---------------------------------------------------------------------------


def test_persistence_facts_included(base_enricher):
    sources = base_enricher.enrich("t1")
    pf = next(s for s in sources if s.sourceId == "system-persistence-facts")
    assert "Task state is durable" in pf.content


# ---------------------------------------------------------------------------
# 7. PRIOR RESULTS TESTS (2 tests)
# ---------------------------------------------------------------------------


@dataclass
class MockResultObj:
    summary: str


@dataclass
class MockExecution:
    taskId: str
    executionId: str
    stage: str
    provider: str
    model: str
    result: MockResultObj = None
    completedAt: str = "2026-01-01"


def test_prior_results_includes_completed_executions():
    executions = [
        MockExecution("t1", "e1", "completed", "p", "m", MockResultObj("Result 1")),
        MockExecution("t1", "e2", "failed", "p", "m"),
        MockExecution(
            "t1", "e3", "human_review_required", "p", "m", MockResultObj("Result 3"), "2026-01-02"
        ),
    ]
    enricher = ContextEnricher(
        identity_service=MockIdentityService(),
        settings=MockSettings(),
        repository=MockRepository(executions=executions),
        tool_registry=MockToolRegistry(),
    )
    sources = enricher.enrich("t1")
    pr_sources = [s for s in sources if s.sourceId.startswith("prior-results-")]
    assert len(pr_sources) == 2
    # e3 is most recent (completedAt="2026-01-02")
    assert "Execution: e3" in pr_sources[0].content
    assert "Result 3" in pr_sources[0].content
    assert "Execution: e1" in pr_sources[1].content


def test_prior_results_truncates_long_summaries():
    long_summary = "A" * 1000
    executions = [
        MockExecution("t1", "e1", "completed", "p", "m", MockResultObj(long_summary)),
    ]
    enricher = ContextEnricher(
        identity_service=MockIdentityService(),
        settings=MockSettings(),
        repository=MockRepository(executions=executions),
        tool_registry=MockToolRegistry(),
    )
    sources = enricher.enrich("t1")
    pr = next(s for s in sources if s.sourceId == "prior-results-0")
    # Summary is truncated to 500 chars + prefix
    assert "Summary: " + ("A" * 500) in pr.content
    assert "A" * 501 not in pr.content
