from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from hashlib import sha256

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import AuditEventRow, OutboxEventRow, ToolExecutionRow
from app.main import create_app
from app.models.agent_runtime import (
    AutonomousExecutionSpecification,
    CreateAgentRunCommand,
    QueueAgentRunCommand,
)
from tests.agent_runtime_testkit import make_spec, ts
from tests.test_agent_runtime_sql_control_plane import grant_runtime_permissions
from tests.test_autonomous_worker import (
    VALID_RESULT,
    FakeRouter,
    configure_worker,
    queue_only_demo_task,
)
from tests.test_context_integration import context_body
from tests.test_persistence import database_url


@contextmanager
def prepared(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "inputs").mkdir()
    (workspace / "reports").mkdir()
    (workspace / "inputs" / "brief.txt").write_text("Known operator facts", encoding="utf-8")
    (workspace / ".jarvis-workspace.json").write_text(
        json.dumps({"schemaVersion": "1.0", "workspaceId": "lab"})
    )
    plan = {
        **VALID_RESULT,
        "steps": [
            {"tool": "workspace.list", "path": "inputs"},
            {"tool": "workspace.read", "path": "inputs/brief.txt"},
            {
                "tool": "workspace.report",
                "path": "reports/plan.md",
                "content": "# Reviewed plan\nBased on facts supplied by the operator.\n",
            },
        ],
    }
    app = create_app(delay_ms=1, database_url=database_url(tmp_path / "tools.db"))
    with TestClient(app) as client:
        queue_only_demo_task(app)
        actor_id = grant_runtime_permissions(app, "tools-operator", task_id="task-demo")
        configure_worker(app, actor_id, FakeRouter([json.dumps(plan)]))
        app.state.settings.tool_execution_enabled = True
        app.state.settings.tool_workspaces_json = json.dumps({"lab": str(workspace)})
        assembly = client.post(
            "/api/context/assemblies",
            json=context_body("Approved planning facts.", task_id="task-demo"),
            headers={"Idempotency-Key": "tool-context"},
        )
        assert assembly.status_code == 201, assembly.text
        spec = make_spec(
            run_id="workspace-plan", task_id="task-demo", agent_id=actor_id
        ).model_copy(
            update={
                "autonomous_execution": AutonomousExecutionSpecification(
                    execution_type="workspace_plan",
                    context_assembly_id=assembly.json()["data"]["id"],
                    response_format="workspace_plan_json_v1",
                    provider_preference="local-fake",
                    model_name="fixture-model",
                )
            }
        )
        runtime = app.state.agent_runtime_service
        actor = runtime.authenticate_actor(actor_id)
        created = runtime.handle_authorized(
            CreateAgentRunCommand(
                specification=spec, command_id="create-workspace-plan", timestamp=ts(0)
            ),
            actor,
        )
        runtime.handle_authorized(
            QueueAgentRunCommand(
                run_id=spec.run_id,
                command_id="queue-workspace-plan",
                expected_run_version=created.snapshot.version,
                timestamp=ts(1),
            ),
            actor,
        )
        worker = app.state.task_leases.register_worker("tool-test", "tool-test", 60)
        result = asyncio.run(app.state.autonomous_worker_service.run_once(worker.id))
        assert result is not None and result.stage == "completed", result
        body = {
            "commandId": "approve-tools-1",
            "sourceExecutionId": result.executionId,
            "expectedPlanHash": result.resultHash,
            "scope": {
                "workspaceId": "lab",
                "allowedTools": ["workspace.list", "workspace.read", "workspace.report"],
                "readPrefixes": ["inputs"],
                "writePrefixes": ["reports"],
                "maximumBytes": 65536,
                "maximumSteps": 8,
            },
        }
        yield app, client, actor_id, worker, workspace, result, body


def authorize(client, actor_id, body):
    response = client.post(
        "/api/tool-executions/authorize", headers={"X-Jarvis-Actor-Id": actor_id}, json=body
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_fixed_plan_authorization_execution_and_restart(tmp_path):
    with prepared(tmp_path) as (app, client, actor, worker, workspace, source, body):
        original = app.state.repository.get_task_durable(source.taskId).model_dump(mode="json")
        assert not (workspace / "reports" / "plan.md").exists()
        queued = authorize(client, actor, body)
        assert queued["stage"] == "queued"
        assert queued["taskId"] != source.taskId
        assert (
            app.state.repository.get_task_durable(source.taskId).model_dump(mode="json") == original
        )
        assert authorize(client, actor, body) == queued
        result = asyncio.run(app.state.autonomous_worker_service.run_once(worker.id))
        assert result.stage == "completed", result
        assert all(item.status == "completed" for item in result.steps)
        assert result.steps[1].observation.content == "Known operator facts"
        artifact = result.artifacts[0]
        text = (workspace / "reports" / "plan.md").read_text(encoding="utf-8")
        assert sha256(text.encode()).hexdigest() == artifact.contentHash
        assert app.state.repository.get_task_durable(result.taskId).status == "completed"
        assert (
            app.state.agent_runtime_service.read_run_authorized(
                result.runtimeRunId, app.state.agent_runtime_service.authenticate_actor(actor)
            ).state
            == "succeeded"
        )
        headers = {"X-Jarvis-Actor-Id": actor}
        for task_id in (source.taskId, result.taskId):
            rows = client.get(f"/api/tool-executions?taskId={task_id}", headers=headers).json()[
                "data"
            ]
            assert rows[0]["executionId"] == result.executionId
        durable = client.get(f"/api/tool-executions/{result.executionId}", headers=headers).json()[
            "data"
        ]
        assert authorize(client, actor, body) == durable
        content = client.get(f"/api/tool-artifacts/{artifact.artifactId}", headers=headers)
        assert content.status_code == 200, content.text
        assert content.json()["data"]["content"] == text
        with app.state.task_leases.session_factory() as session:
            assert len(list(session.scalars(select(ToolExecutionRow)))) == 1
            assert (
                len(
                    list(
                        session.scalars(
                            select(OutboxEventRow).where(
                                OutboxEventRow.event_type == "tool.step.completed"
                            )
                        )
                    )
                )
                == 3
            )
            assert (
                len(
                    list(
                        session.scalars(
                            select(AuditEventRow).where(
                                AuditEventRow.event_type == "tool.step.completed"
                            )
                        )
                    )
                )
                == 3
            )
    restarted = create_app(delay_ms=1, database_url=database_url(tmp_path / "tools.db"))
    with TestClient(restarted) as client:
        assert (
            client.get(f"/api/tool-executions/{result.executionId}", headers=headers).json()["data"]
            == durable
        )
        assert (
            client.get(f"/api/tool-artifacts/{artifact.artifactId}", headers=headers).json()[
                "data"
            ]["content"]
            == text
        )


def test_authorization_rejects_changed_plan_scope_and_reused_command(tmp_path):
    with prepared(tmp_path) as (app, client, actor, worker, workspace, source, body):
        headers = {"X-Jarvis-Actor-Id": actor}
        changed = {**body, "expectedPlanHash": "f" * 64}
        response = client.post("/api/tool-executions/authorize", headers=headers, json=changed)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "TOOL_PLAN_CHANGED"
        outside = {**body, "scope": {**body["scope"], "writePrefixes": ["inputs"]}}
        assert (
            client.post("/api/tool-executions/authorize", headers=headers, json=outside).status_code
            == 403
        )
        assert client.post("/api/tool-executions/authorize", json=body).status_code == 401
        queued = authorize(client, actor, body)
        assert (
            client.post(
                "/api/tool-executions/authorize",
                headers=headers,
                json={**body, "scope": {**body["scope"], "maximumSteps": 7}},
            ).status_code
            == 409
        )
        assert (
            app.state.tool_execution_service.repository.get(queued["executionId"]).stage == "queued"
        )
        assert not (workspace / "reports" / "plan.md").exists()


def test_preparing_authorization_retry_preserves_identity_and_intent(tmp_path, monkeypatch):
    import app.tool_execution.service as module

    with prepared(tmp_path) as (app, client, actor, worker, workspace, source, body):
        original = module.configure_task_actor

        def interrupted(*args, **kwargs):
            raise RuntimeError("simulated interruption before runtime setup")

        monkeypatch.setattr(module, "configure_task_actor", interrupted)
        with pytest.raises(RuntimeError, match="simulated interruption"):
            authorize(client, actor, body)
        with app.state.task_leases.session_factory() as session:
            rows = list(session.scalars(select(ToolExecutionRow)))
            assert len(rows) == 1 and rows[0].stage == "preparing"
            expected_id, expected_task = rows[0].execution_id, rows[0].task_id
        monkeypatch.setattr(module, "configure_task_actor", original)
        queued = authorize(client, actor, body)
        assert (queued["executionId"], queued["taskId"]) == (expected_id, expected_task)
        assert (
            asyncio.run(app.state.autonomous_worker_service.run_once(worker.id)).stage
            == "completed"
        )


def test_atomic_write_interruption_recovers_without_duplicate_effect(tmp_path, monkeypatch):
    from app.tool_execution.filesystem import WorkspaceToolRegistry

    original_execute = WorkspaceToolRegistry.execute
    with prepared(tmp_path) as (app, client, actor, worker, workspace, source, body):
        queued = authorize(client, actor, body)
        writes = []

        def interrupted(registry, step, scope):
            observation = original_execute(registry, step, scope)
            if step.tool == "workspace.report":
                writes.append(observation.written)
                raise RuntimeError("simulated crash after atomic replace before database commit")
            return observation

        monkeypatch.setattr(WorkspaceToolRegistry, "execute", interrupted)
        with pytest.raises(RuntimeError, match="simulated crash"):
            asyncio.run(app.state.autonomous_worker_service.run_once(worker.id))
        record = app.state.tool_execution_service.repository.get(queued["executionId"])
        assert record.steps[-1].status == "started"
        assert record.artifacts == []
        assert (workspace / "reports" / "plan.md").exists()
    monkeypatch.setattr(WorkspaceToolRegistry, "execute", original_execute)
    app = create_app(delay_ms=1, database_url=database_url(tmp_path / "tools.db"))
    with TestClient(app):
        configure_worker(app, actor, FakeRouter([]))
        app.state.settings.tool_execution_enabled = True
        app.state.settings.tool_workspaces_json = json.dumps({"lab": str(workspace)})

        def observe_replay(registry, step, scope):
            result = original_execute(registry, step, scope)
            if step.tool == "workspace.report":
                writes.append(result.written)
            return result

        monkeypatch.setattr(WorkspaceToolRegistry, "execute", observe_replay)
        result = asyncio.run(app.state.autonomous_worker_service.run_once(worker.id))
        assert result.stage == "completed"
        assert writes == [True, False]
        assert len(result.artifacts) == 1
        assert (
            len(
                app.state.agent_runtime_service.checkpoints_authorized(
                    result.runtimeRunId, app.state.agent_runtime_service.authenticate_actor(actor)
                )
            )
            == 3
        )


def test_task_commit_interruption_finalizes_without_reexecuting_steps(tmp_path, monkeypatch):
    with prepared(tmp_path) as (app, client, actor, worker, workspace, source, body):
        queued = authorize(client, actor, body)
        original = app.state.tool_execution_service._finalize

        def interrupted(*args):
            raise RuntimeError("simulated crash after task commit")

        monkeypatch.setattr(app.state.tool_execution_service, "_finalize", interrupted)
        with pytest.raises(RuntimeError, match="after task commit"):
            asyncio.run(app.state.autonomous_worker_service.run_once(worker.id))
        assert app.state.repository.get_task_durable(queued["taskId"]).status == "completed"
        monkeypatch.setattr(app.state.tool_execution_service, "_finalize", original)
        result = asyncio.run(app.state.autonomous_worker_service.run_once(worker.id))
        assert result.stage == "completed"
        assert len(result.artifacts) == 1


def test_corrupt_authorization_fails_before_side_effect(tmp_path):
    from app.core.errors import DomainError

    with prepared(tmp_path) as (app, client, actor, worker, workspace, source, body):
        queued = authorize(client, actor, body)
        with app.state.task_leases.session_factory() as session, session.begin():
            row = session.get(ToolExecutionRow, queued["executionId"])
            row.scope_json = {**row.scope_json, "writePrefixes": ["inputs"]}
        with pytest.raises(DomainError) as caught:
            asyncio.run(app.state.autonomous_worker_service.run_once(worker.id))
        assert caught.value.code == "TOOL_RECORD_INVALID"
        assert not (workspace / "reports" / "plan.md").exists()


def test_nonempty_tool_downgrade_refuses_data_loss(tmp_path):
    from alembic import command
    from sqlalchemy import text

    from tests.test_autonomous_worker_migration import migration_config

    with prepared(tmp_path) as (app, client, actor, worker, workspace, source, body):
        authorize(client, actor, body)
        with pytest.raises(RuntimeError, match="not representable"):
            command.downgrade(migration_config(tmp_path / "tools.db"), "20260905_07")
        with app.state.engine.connect() as connection:
            assert (
                connection.scalar(text("select version_num from alembic_version")) == "20260905_08"
            )
            assert connection.scalar(text("select count(*) from tool_executions")) == 1


def test_source_read_permission_does_not_grant_workspace_observations(tmp_path):
    from app.autonomous_worker.provisioning import configure_task_actor

    with prepared(tmp_path) as (app, client, actor, worker, workspace, source, body):
        queued = authorize(client, actor, body)
        result = asyncio.run(app.state.autonomous_worker_service.run_once(worker.id))
        observer = configure_task_actor(app, source.taskId, "source-observer")
        headers = {"X-Jarvis-Actor-Id": observer}
        assert (
            client.get(f"/api/model-executions/{source.executionId}", headers=headers).status_code
            == 200
        )
        assert (
            client.get(f"/api/tool-executions/{queued['executionId']}", headers=headers).status_code
            == 403
        )
        assert (
            client.get(
                f"/api/tool-artifacts/{result.artifacts[0].artifactId}", headers=headers
            ).status_code
            == 403
        )
