import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select
from test_autonomous_worker import FakeRouter
from test_catalog import approve_activate, snapshot
from test_context_integration import context_body

from app.db.models import (
    AgentCapabilityAssignmentRow,
    AgentPermissionAssignmentRow,
    AgentRoleAssignmentRow,
    CatalogActivationRow,
    CatalogEntryRow,
    IdentityAgentRow,
    IdentityCapabilityRow,
    OutboxEventRow,
    TaskDecompositionRow,
    TaskRow,
)
from app.decomposition.repository import DecompositionRepository
from app.decomposition.service import DecompositionService, assign
from app.main import create_app
from app.models.decomposition import DecompositionProposal, ready_keys, topological_keys


def node(key="research", caps=None, deps=None, owner=None):
    return dict(
        key=key,
        title=f"Produce {key} evidence",
        description="Compare concrete alternatives with source provenance.",
        requiredCapabilities=caps or ["research.market"],
        dependsOn=deps or [],
        preferredAgentId=owner,
        deliverable="Structured comparison table with source references",
        outputType="analysis",
        completionCriteria=["Record five alternatives with price and source URL."],
    )


def proposal(nodes=None):
    return dict(
        objectiveSummary="Assess an AI clipping business and build a bounded prototype.",
        subtasks=nodes or [node()],
    )


class Router(FakeRouter):
    def __init__(self, output=None, required=None):
        super().__init__([])
        self.output = output or proposal()
        self.required = required or ["research.market"]
        self.barrier = None

    async def execute(self, *, request, requirements, budget, pricing=None):
        from app.model_providers.contracts import ModelExecutionResponse

        if request.output_schema.name == "required_capabilities":
            self.team_requests.append(request)
            content = json.dumps(
                dict(required=self.required, optional=[], reasoning_summary="Fixture capabilities")
            )
        elif request.output_schema.name == "task_decomposition":
            self.decomposition_requests.append(request)
            if self.barrier:
                self.barrier.wait(timeout=10)
            content = self.output if isinstance(self.output, str) else json.dumps(self.output)
        else:
            raise AssertionError("Unexpected inference purpose")
        self.all_requests.append(request)
        return ModelExecutionResponse(
            content=content,
            provider="local-fake",
            model="fixture-model",
            latency_ms=1,
            finish_reason="stop",
        )


@pytest.fixture
def app(tmp_path, monkeypatch):
    for key in ("JARVIS_AUTONOMOUS_WORKER_ACTOR_ID", "JARVIS_MODEL_PROVIDER_PRIORITY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("JARVIS_AUTONOMOUS_WORKER_ENABLED", "false")
    monkeypatch.setenv("JARVIS_MODEL_EXECUTION_MODE", "disabled")
    monkeypatch.setenv("JARVIS_MODEL_OLLAMA_ENABLED", "false")
    monkeypatch.setenv("JARVIS_MODEL_OPENAI_COMPATIBLE_ENABLED", "false")
    application = create_app(database_url=f"sqlite:///{(tmp_path / 'decomposition.db').as_posix()}")
    application.state.model_router = Router()
    with application.state.repository.session_factory() as session, session.begin():
        for agent_id in ("scout", "atlas", "jarvis"):
            if session.get(IdentityAgentRow, agent_id) is None:
                session.add(
                    IdentityAgentRow(
                        id=agent_id,
                        stable_key=agent_id,
                        display_name=agent_id,
                        agent_type="coordinator" if agent_id == "jarvis" else "specialist",
                        lifecycle_state="active",
                        is_enabled=True,
                    )
                )
        session.flush()
        for key in ("research.market", "software.backend", "business.financial-analysis"):
            cap = session.scalar(
                select(IdentityCapabilityRow).where(IdentityCapabilityRow.stable_key == key)
            )
            if cap is None:
                cap = IdentityCapabilityRow(
                    id="cap-" + key,
                    stable_key=key,
                    display_name=key,
                    description="Fixture",
                    category="fixture",
                    is_enabled=True,
                )
                session.add(cap)
                session.flush()
            # Seeded compatibility identities ensure the task-assignment FK is real.
            for agent_id in ("scout", "atlas"):
                agent = session.get(IdentityAgentRow, agent_id)
                agent.lifecycle_state = "active"
                agent.is_enabled = True
                session.add(
                    AgentCapabilityAssignmentRow(
                        id=agent_id + key,
                        agent_id=agent_id,
                        capability_id=cap.id,
                        source="fixture",
                        starts_at=datetime.now(UTC),
                    )
                )
        session.get(IdentityAgentRow, "jarvis").lifecycle_state = "active"
    yield application
    application.state.engine.dispose()


def setup(app, output=None, required=None, auto=False):
    router = Router(output, required)
    app.state.model_router = router
    if auto:
        app.state.settings.model_execution_mode = "local_only"
    with closing(TestClient(app)) as client:
        task = client.post(
            "/api/tasks",
            json={
                "title": "Research AI clipping business",
                "description": "Assess markets, economics and prototype.",
            },
        ).json()["data"]
        assembly = client.post("/api/context/assemblies", json=context_body(task_id=task["id"]))
        assert assembly.status_code == 201, assembly.text
        return task["id"], assembly.json()["data"]["id"], router


def service(app):
    return DecompositionService(
        app.state.repository, app.state.identity_service, app.state.model_router
    )


@pytest.mark.parametrize(
    "bad",
    [
        [node("same"), node("same")],
        [node(deps=["research"])],
        [node(deps=["missing"])],
        [node("first", deps=["second"]), node("second", deps=["first"])],
        [node(caps=["invented.capability"])],
        [node(chr(97 + i)) for i in range(13)],
        [node(chr(97 + i), deps=[chr(96 + i)] if i else []) for i in range(7)],
    ],
)
def test_reject_invalid_graph(bad):
    with pytest.raises(ValidationError):
        DecompositionProposal.model_validate(proposal(bad))


def test_deterministic_dag_and_ready_nodes():
    nodes = [node("prototype", deps=["research"]), node("finance", deps=["research"]), node()]
    graph = DecompositionProposal.model_validate(proposal(nodes))
    assert topological_keys(graph.subtasks) == ["research", "finance", "prototype"]
    assert topological_keys(list(reversed(graph.subtasks))) == ["research", "finance", "prototype"]
    assert ready_keys(graph.subtasks) == ["research"]
    assert ready_keys(graph.subtasks, frozenset({"research"})) == ["finance", "prototype"]


def test_simple_automatic_replay_restart_and_zero_authority(app):
    with app.state.repository.session_factory() as session:
        before = [
            session.scalar(select(func.count()).select_from(model))
            for model in (
                AgentPermissionAssignmentRow,
                AgentRoleAssignmentRow,
                CatalogActivationRow,
            )
        ]
        ranks = [(a.id, a.rank_id) for a in session.scalars(select(IdentityAgentRow))]
    task_id, assembly_id, router = setup(app, auto=True)
    graph = service(app).current(task_id)
    assert graph.status == "ready" and len(graph.subtasks) == 1
    assert graph.subtasks[0].assignedAgentId in {"scout", "atlas"}
    assert (
        graph.teamSelectionId
        == app.state.repository.get_task_durable(task_id).teamSelection.selectionId
    )
    assert len(router.team_requests) == len(router.decomposition_requests) == 1
    with TestClient(app) as client:
        first = client.post(f"/api/tasks/{task_id}/decomposition", json={}).json()["data"]
        assert first["id"] == graph.id
        assert (
            client.post(f"/api/tasks/{task_id}/decomposition/rebuild", json={}).json()["data"]
            == first
        )
        assert client.get(f"/api/tasks/{task_id}/decomposition").json()["data"] == first
    assert len(router.decomposition_requests) == 1
    assert DecompositionRepository(app.state.repository.session_factory).current(task_id) == graph
    with app.state.repository.session_factory() as session:
        assert before == [
            session.scalar(select(func.count()).select_from(model))
            for model in (
                AgentPermissionAssignmentRow,
                AgentRoleAssignmentRow,
                CatalogActivationRow,
            )
        ]
        assert ranks == [(a.id, a.rank_id) for a in session.scalars(select(IdentityAgentRow))]
        assert session.scalar(select(func.count()).select_from(TaskDecompositionRow)) == 1
    restarted = create_app(database_url=app.state.settings.database_url)
    restarted.state.model_router = router
    assert service(restarted).current(task_id) == graph
    with restarted.state.repository.session_factory() as session:
        assert (
            session.scalar(
                select(func.count()).select_from(TaskRow).where(TaskRow.parent_task_id == task_id)
            )
            == 0
        )
    restarted.state.engine.dispose()


def test_real_concurrent_proposals_converge(app):
    task_id, assembly_id, router = setup(app)
    router.barrier = threading.Barrier(2)

    def run():
        return asyncio.run(service(app).prepare(task_id, assembly_id))

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))
    assert results[0] == results[1]
    with app.state.repository.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(TaskDecompositionRow)) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(OutboxEventRow)
                .where(OutboxEventRow.event_type == "task_decomposition.completed")
            )
            == 1
        )


def test_rollback_after_graph_insert(app, monkeypatch):
    task_id, assembly_id, _ = setup(app)

    def fail(*args):
        raise RuntimeError("Injected outbox failure after graph flush")

    monkeypatch.setattr(DecompositionRepository, "_event", fail)
    with pytest.raises(RuntimeError, match="Injected"):
        asyncio.run(service(app).prepare(task_id, assembly_id))
    with app.state.repository.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(TaskDecompositionRow)) == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(OutboxEventRow)
                .where(OutboxEventRow.event_type.like("task_decomposition.%"))
            )
            == 0
        )


def test_versioning_stale_and_execution_protection(app):
    task_id, assembly_id, _ = setup(app)
    first = asyncio.run(service(app).prepare(task_id, assembly_id))
    with app.state.repository.session_factory() as session, session.begin():
        row = session.get(TaskRow, task_id)
        row.payload = row.payload | {"request": "Revised market question"}
        row.original_request = "Revised market question"
    assert service(app).current(task_id).status == "needs_redecomposition"
    second = asyncio.run(service(app).prepare(task_id, assembly_id))
    assert second.version == 2
    history = service(app).repository.history(task_id)
    assert history[0].status == "superseded" and history[0].supersededBy == second.id
    assert history[0].subtasks == first.subtasks
    with app.state.repository.session_factory() as session, session.begin():
        row = session.get(TaskRow, task_id)
        row.payload = row.payload | {
            "request": "Third question",
            "startedAt": datetime.now(UTC).isoformat(),
        }
    from app.core.errors import DomainError

    with pytest.raises(DomainError, match="execution"):
        asyncio.run(service(app).prepare(task_id, assembly_id))


@pytest.mark.parametrize("mode", ["disabled", "suspended", "incapable", "nonexistent", "manager"])
def test_invalid_preferred_owner_repaired_within_team(app, mode):
    task_id, assembly_id, router = setup(app)
    task = app.state.repository.get_task_durable(task_id)
    selected = task.teamSelection.selectedAgentIds[0]
    preferred = (
        "nonexistent" if mode == "nonexistent" else "jarvis" if mode == "manager" else selected
    )
    # Include another capable specialist explicitly in the authoritative selected pool.
    with app.state.repository.session_factory() as session, session.begin():
        row = session.get(TaskRow, task_id)
        task.teamSelection.selectedAgentIds = ["scout", "atlas"]
        row.payload = task.model_dump(mode="json")
        identity = session.get(IdentityAgentRow, selected)
        if mode == "disabled":
            identity.is_enabled = False
        if mode == "suspended":
            identity.lifecycle_state = "suspended"
        if mode == "incapable":
            for assignment in session.scalars(
                select(AgentCapabilityAssignmentRow).where(
                    AgentCapabilityAssignmentRow.agent_id == selected
                )
            ):
                assignment.revoked_at = datetime.now(UTC)
    router.output = proposal([node(owner=preferred)])
    result = asyncio.run(service(app).prepare(task_id, assembly_id))
    assert result.status == "ready"
    assert result.subtasks[0].assignedAgentId in {"scout", "atlas"}
    if mode in {"disabled", "suspended", "incapable"}:
        assert result.subtasks[0].assignedAgentId != selected


def test_missing_capability_and_unknown_output_bounded(app):
    task_id, assembly_id, router = setup(app)
    router.output = proposal([node(), node("security", ["software.security"])])
    result = asyncio.run(service(app).prepare(task_id, assembly_id))
    assert result.status == "needs_team_reselection" and result.subtasks == []
    assert result.issues[0].requiredCapabilities == ["software.security"]
    assert result.issues[0].affectedSubtasks == ["security"]


@pytest.mark.parametrize(
    "output",
    [
        "not json",
        proposal([node(caps=["software.backend"])]),
        proposal([node(caps=["made.up"])]),
        proposal([dict(node(), permissions=["admin"])]),
    ],
)
def test_invalid_output_fails_without_fabrication(app, output):
    task_id, assembly_id, router = setup(app, output=output)
    result = asyncio.run(service(app).prepare(task_id, assembly_id))
    assert result.status == "failed" and not result.subtasks
    assert result.requestCount == len(router.decomposition_requests) == 2


def test_multidisciplinary_parallel_and_prompt_bounds(app):
    nodes = [
        node(),
        node("finance", ["business.financial-analysis"], ["research"]),
        node("prototype", ["software.backend"], ["research"]),
    ]
    task_id, assembly_id, router = setup(
        app,
        output=proposal(nodes),
        required=["research.market", "software.backend", "business.financial-analysis"],
    )
    catalog = app.state.catalog_service
    catalog.import_snapshot(snapshot(50, body="DORMANT_SECRET_PROMPT " * 100), False)
    result = asyncio.run(service(app).prepare(task_id, assembly_id))
    assert result.status == "ready" and len(result.subtasks) == 3
    assert ready_keys(result.subtasks, frozenset({"research"})) == ["finance", "prototype"]
    assert len({n.assignedAgentId for n in result.subtasks}) == 1  # No fake diversity.
    prompt = router.decomposition_requests[0].model_dump_json()
    assert "DORMANT_SECRET_PROMPT" not in prompt
    assert sum(len(m.content) for m in router.decomposition_requests[0].messages) < 30000


def test_historical_disabled_assignment_is_not_rewritten(app):
    task_id, assembly_id, _ = setup(app)
    result = asyncio.run(service(app).prepare(task_id, assembly_id))
    with app.state.repository.session_factory() as session, session.begin():
        session.get(IdentityAgentRow, result.subtasks[0].assignedAgentId).is_enabled = False
    current = service(app).current(task_id)
    assert current.status == "needs_redecomposition"
    assert current.subtasks == result.subtasks


def test_catalog_disable_and_dormant_not_assignment_pool(app):
    catalog = app.state.catalog_service
    catalog.import_snapshot(snapshot(2), False)
    entry = catalog.repository.page("agent").items[0]
    activated = approve_activate(catalog, entry)
    with app.state.repository.session_factory() as session, session.begin():
        session.get(CatalogEntryRow, entry.id).enabled = False
    assert app.state.identity_service.workforce_snapshot(agent_ids=[activated.identity_id]) == []


def test_assignment_stable_balancing_and_optional_coverage(app):
    task_id, _, _ = setup(app)
    task = app.state.repository.get_task_durable(task_id)
    task.teamSelection.selectedAgentIds = ["scout", "atlas"]
    task.teamSelection.optionalCapabilities = ["software.security"]
    workforce = app.state.identity_service.workforce_snapshot(agent_ids=["scout", "atlas"])
    graph = DecompositionProposal.model_validate(
        proposal([node("alpha"), node("beta"), node("gamma")])
    )
    first, issues = assign(graph, task, workforce)
    second, _ = assign(graph, task, list(reversed(workforce)))
    assert not issues
    assert [n.assignedAgentId for n in first] == [n.assignedAgentId for n in second]
    assert len({n.assignedAgentId for n in first}) == 2


def test_operator_protected_blocks_redecomposition(app):
    task_id, assembly_id, router = setup(app)
    first = asyncio.run(service(app).prepare(task_id, assembly_id))

    with app.state.repository.session_factory() as session, session.begin():
        row = session.get(TaskDecompositionRow, first.id)
        from sqlalchemy.orm.attributes import flag_modified

        payload = row.payload.copy()
        payload["operatorProtected"] = True
        row.payload = payload
        flag_modified(row, "payload")

    with app.state.repository.session_factory() as session, session.begin():
        row = session.get(TaskRow, task_id)
        payload = row.payload.copy()
        payload["request"] = "something new"
        row.payload = payload
        flag_modified(row, "payload")

    from app.core.errors import DomainError

    with pytest.raises(DomainError, match="Operator"):
        asyncio.run(service(app).prepare(task_id, assembly_id))


def test_decomposition_inference_is_strictly_bounded(app):
    task_id, assembly_id, router = setup(app)
    # The taxonomy contains 100+ capabilities, but the request should only include selected agent metadata
    asyncio.run(service(app).prepare(task_id, assembly_id))
    req = router.decomposition_requests[0]
    user_msg = next(m.content for m in req.messages if m.role == "user")

    # Assert it does NOT include prompt instructions of the agents
    assert "You are Scout" not in user_msg
    # Assert it includes the taxonomy
    assert "software.architecture" in user_msg

    parsed = json.loads(user_msg)
    # Assert only selected agents are in the specialists list (no dormant agents or unselected agents)
    assert len(parsed["specialists"]) == len(
        app.state.repository.get_task_durable(task_id).teamSelection.selectedAgentIds
    )
    assert "prompt" not in parsed["specialists"][0]
