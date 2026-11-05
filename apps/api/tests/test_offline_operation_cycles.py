"""Real native task cycles with scripted inference, durable restart and idle checks."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.db.models import ModelExecutionRow
from app.main import create_app
from app.models.context import ContextSourceType, TrustLevel
from app.models.identity import AssignPermissionRequest
from tests.test_agent_runtime_sql_control_plane import grant_runtime_permissions
from tests.test_autonomous_worker import (
    VALID_RESULT,
    FakeRouter,
    configure_worker,
    create_assembly_and_runtime,
    queue_only_demo_task,
)
from tests.test_context_integration import context_body
from tests.test_persistence import database_url


def test_repeated_native_cycles_restart_and_reuse_task_memory(tmp_path):
    url = database_url(tmp_path / "cycles.db")
    completed = []
    # Fresh application/service graph on each cycle; same durable database.
    for cycle in range(3):
        app = create_app(database_url=url)
        router = FakeRouter([json.dumps({**VALID_RESULT, "summary": f"Cycle {cycle} deliverable"})])
        with TestClient(app) as client:
            if cycle == 0:
                queue_only_demo_task(app)
                task_id = "task-demo"
            else:
                response = client.post(
                    "/api/tasks",
                    json={
                        "title": f"Offline cycle {cycle}",
                        "description": "Produce a bounded planning deliverable",
                    },
                )
                assert response.status_code == 201, response.text
                task_id = response.json()["data"]["id"]
            if cycle == 0:
                actor_id = grant_runtime_permissions(app, "cycle-operator", task_id=task_id)
            else:
                for permission in app.state.identity_service.list_definitions("permission", 0, 100):
                    app.state.identity_service.assign_permission(
                        actor_id,
                        AssignPermissionRequest(
                            permission_id=permission.id,
                            effect="allow",
                            resource_type="task",
                            resource_id=task_id,
                        ),
                    )
            configure_worker(app, actor_id, router)
            run_id = f"cycle-run-{cycle}"
            create_assembly_and_runtime(client, app, actor_id, run_id=run_id, task_id=task_id)
            worker = app.state.task_leases.register_worker(
                "Offline acceptance", f"cycle-worker-{cycle}", 60, {}
            )
            assert client.portal is not None
            execution = client.portal.call(app.state.autonomous_worker_service.run_once, worker.id)
            assert execution.stage == "completed"
            assert execution.result.summary == f"Cycle {cycle} deliverable"
            assert app.state.agent_runtime_service.repository.load_run(run_id).state == "succeeded"
            assert app.state.agent_runtime_service.repository.list_checkpoints(run_id)
            # Repeated polls of the actual service must create neither calls nor results.
            call_count = len(router.requests)
            for _ in range(3):
                assert (
                    client.portal.call(app.state.autonomous_worker_service.run_once, worker.id)
                    is None
                )
            assert len(router.requests) == call_count == 1
            app.state.task_leases.stop_worker(worker.id)
            completed.append((task_id, execution.executionId, execution.result.summary))
        app.state.engine.dispose()
    restarted = create_app(database_url=url)
    with TestClient(restarted) as client:
        for task_id, execution_id, summary in completed:
            # Reopened SQL-backed state, no inference enabled on this app.
            task = client.get(f"/api/tasks/{task_id}").json()["data"]
            assert task["status"] == "completed"
            response = client.post(
                "/api/context/assemblies",
                json=context_body("Inspect the recorded result after restart", task_id=task_id),
                headers={"Idempotency-Key": f"recall-{task_id}"},
            )
            assert response.status_code == 201, response.text
            assembly = response.json()["data"]
            prior = [
                item
                for item in assembly["manifest"]["includedSources"]
                if item["sourceType"] == ContextSourceType.PRIOR_MODEL_OUTPUT.value
            ]
            assert len(prior) == 1
            assert prior[0]["trustLevel"] == TrustLevel.PRIOR_MODEL_OUTPUT.value
            serialized = json.dumps(assembly["modelRequest"])
            assert execution_id in serialized and summary in serialized
            assert all(
                other_id not in serialized
                for other_task, other_id, _ in completed
                if other_task != task_id
            )
        # Verify SQL filtering/order/limit against retained completed, review and
        # failed history, without another inference request or production task.
        task_id, execution_id, _ = completed[0]
        with restarted.state.repository.session_factory.begin() as session:
            original = session.get(ModelExecutionRow, execution_id)
            fields = {
                column.key: getattr(original, column.key)
                for column in ModelExecutionRow.__table__.columns
            }
            now = datetime.now(UTC)
            for index, stage in enumerate(("completed", "human_review_required", "failed")):
                session.add(
                    ModelExecutionRow(
                        **{
                            **fields,
                            "execution_id": f"history-{index}",
                            "runtime_attempt_id": f"history-attempt-{index}",
                            "stage": stage,
                            "created_at": now + timedelta(seconds=index),
                            "completed_at": now + timedelta(seconds=index),
                        }
                    )
                )
        repo = restarted.state.model_execution_repository
        assert [item.executionId for item in repo.list_context_results(task_id)] == [
            "history-1",
            "history-0",
        ]
        assert [item.executionId for item in repo.list_context_results(task_id, limit=1)] == [
            "history-1"
        ]
        for invalid in (0, 101):
            with pytest.raises(ValueError, match="prior-result limit"):
                repo.list_context_results(task_id, limit=invalid)
    restarted.state.engine.dispose()
