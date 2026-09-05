from __future__ import annotations

import asyncio

import pytest

from app.autonomous_worker.__main__ import _run_once_resilient
from app.autonomous_worker.provisioning import configure_task_actor
from app.models.agent_runtime import RequestCancellationCommand
from app.models.identity import AssignPermissionRequest
from tests.test_tool_execution import authorize, prepared


def poll(app, worker):
    return asyncio.run(_run_once_resilient(app.state.autonomous_worker_service, worker.id))


def stored(app, execution):
    return app.state.tool_execution_service.repository.get(execution["executionId"])


def runtime_state(app, execution):
    return app.state.agent_runtime_service.repository.load_run(execution["runtimeRunId"]).state


def request_cancellation(app, actor_id, execution):
    runtime = app.state.agent_runtime_service
    actor = runtime.authenticate_actor(actor_id)
    snapshot = runtime.read_run_authorized(execution["runtimeRunId"], actor)
    app.state.autonomous_worker_service._handle(
        RequestCancellationCommand,
        snapshot,
        actor,
        "test-reviewed-tool-cancel",
        reason_code="operator_cancelled",
        detail="Operator cancelled reviewed tool execution",
        requester_reference=actor_id,
    )


def test_queued_emergency_stop_preserves_polling_and_resumes_once(tmp_path):
    with prepared(tmp_path) as (app, client, actor_id, worker, workspace, _source, body):
        queued = authorize(client, actor_id, body)
        response = client.post("/api/system/emergency-stop")
        assert response.status_code == 200, response.text
        assert poll(app, worker) is None
        assert stored(app, queued).stage == "queued"
        assert all(step.status == "pending" for step in stored(app, queued).steps)
        assert runtime_state(app, queued) == "queued"
        assert not (workspace / "reports/plan.md").exists()
        assert client.post("/api/system/resume").status_code == 200
        result = poll(app, worker)
        assert result.executionId == queued["executionId"] and result.stage == "completed"
        assert (workspace / "reports/plan.md").exists()
        assert poll(app, worker) is None


def test_inactive_target_before_start_pauses_without_killing_polling_or_writing(tmp_path):
    with prepared(tmp_path) as (app, client, target_id, worker, workspace, source, body):
        # A distinct authorized executor remains active while its assigned target
        # is suspended. All identities and grants use the real identity service.
        actor_id = configure_task_actor(app, source.taskId, "recovery-executor")
        app.state.settings.autonomous_worker_actor_id = actor_id
        queued = authorize(client, actor_id, body)
        assert queued["targetAgentId"] == target_id
        app.state.identity_service.transition(target_id, "suspended")
        result = poll(app, worker)
        assert result is not None and result.stage == "paused"
        assert result.failureCode == "TOOL_TARGET_INACTIVE"
        assert all(step.status == "pending" for step in result.steps)
        assert runtime_state(app, queued) == "paused"
        assert app.state.repository.get_task_durable(result.taskId).status == "under_review"
        assert not (workspace / "reports/plan.md").exists()
        assert poll(app, worker) is None


@pytest.mark.parametrize("boundary_change", ["runtime_cancel", "execution_permission_revoked"])
def test_current_policy_wins_at_task_completion_boundary(tmp_path, monkeypatch, boundary_change):
    with prepared(tmp_path) as (app, client, actor_id, worker, workspace, _source, body):
        queued = authorize(client, actor_id, body)
        complete_task = app.state.task_leases.complete_task
        interrupted = False

        def change_before_terminal_transaction(task_id, worker_id, token, result, **kwargs):
            nonlocal interrupted
            if not interrupted:
                interrupted = True
                assert task_id == queued["taskId"]
                assert all(step.status == "completed" for step in stored(app, queued).steps)
                if boundary_change == "runtime_cancel":
                    request_cancellation(app, actor_id, queued)
                else:
                    permission = next(
                        item
                        for item in app.state.identity_service.list_definitions(
                            "permission", 0, 100
                        )
                        if item.stable_key == "runtime.execute"
                    )
                    app.state.identity_service.assign_permission(
                        actor_id,
                        AssignPermissionRequest(
                            permission_id=permission.id,
                            effect="deny",
                            resource_type="task",
                            resource_id=task_id,
                            reason="Operator revoked execution before task completion",
                        ),
                    )
            return complete_task(task_id, worker_id, token, result, **kwargs)

        monkeypatch.setattr(
            app.state.task_leases, "complete_task", change_before_terminal_transaction
        )
        poll(app, worker)
        assert interrupted, "The test must reach the actual final task transaction boundary"
        assert (workspace / "reports/plan.md").exists(), "Earlier authorized effects remain durable"
        assert all(step.status == "completed" for step in stored(app, queued).steps)
        assert app.state.repository.get_task_durable(queued["taskId"]).status != "completed"
        assert runtime_state(app, queued) != "succeeded"
        assert stored(app, queued).stage != "completed"
        # A fresh poll must remain safe and cannot turn the interrupted run into success.
        poll(app, worker)
        assert app.state.repository.get_task_durable(queued["taskId"]).status != "completed"


def test_full_page_of_cancelled_candidates_cannot_hide_later_healthy_work(tmp_path):
    with prepared(tmp_path) as (app, client, actor_id, worker, workspace, _source, body):
        cancelled = []
        for index in range(32):
            queued = authorize(
                client, actor_id, {**body, "commandId": f"cancelled-before-start-{index}"}
            )
            request_cancellation(app, actor_id, queued)
            cancelled.append(queued)
        healthy = authorize(client, actor_id, {**body, "commandId": "healthy-after-cancelled-page"})
        completed = None
        # A worker may reconcile one cancelled item per poll; bounded progress
        # must still reach the next page without another operator mutation.
        for _ in range(len(cancelled) + 2):
            result = poll(app, worker)
            if result is not None and result.executionId == healthy["executionId"]:
                completed = result
        assert completed is not None and completed.stage == "completed"
        assert (workspace / "reports/plan.md").exists()
        for queued in cancelled:
            record = stored(app, queued)
            assert record.stage == "failed" and record.failureCode == "EXECUTION_CANCELLED"
            assert all(step.status == "pending" for step in record.steps)
            assert runtime_state(app, queued) == "cancelled"
            assert app.state.repository.get_task_durable(record.taskId).status == "cancelled"
