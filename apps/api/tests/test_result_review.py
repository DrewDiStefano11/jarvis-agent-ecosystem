"""Operator decisions over a real native paused result; no live inference."""

import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest
from sqlalchemy import func, select, update

from app.db.models import (
    AgentPermissionAssignmentRow,
    AgentRuntimeEventRow,
    AgentRuntimeProcessedCommandRow,
    ArtifactRow,
    AuditEventRow,
    IdentityAgentRow,
    ModelExecutionRow,
    OutboxEventRow,
    ResultReviewRow,
    TaskLeaseRow,
    TaskRow,
)
from app.models.identity import AssignPermissionRequest
from tests.test_autonomous_worker import VALID_RESULT, FakeRouter, worker_fixture


@pytest.fixture
async def paused(tmp_path):
    output = VALID_RESULT | {
        "summary": "Level 1 trapeze prefab QC checklist",
        "recommendations": [
            {
                "title": title,
                "description": "Confirm against project and field evidence before release.",
                "priority": "high",
            }
            for title in [
                "Quantities and loads",
                "Conductor sizing",
                "Mount coordination",
                "Tagging",
                "Code addenda",
            ]
        ],
        "assumptions": ["No project schedules or field documents were supplied."],
        "requiresHumanReview": True,
    }
    router = FakeRouter([json.dumps(output)])
    app, client, worker_actor, worker = worker_fixture(
        tmp_path, router=router, run_id="run-operator-review"
    )
    execution = await app.state.autonomous_worker_service.run_once(worker.id)
    assert execution.stage == "human_review_required"
    origin = {"Origin": app.state.settings.web_origin}
    try:
        yield app, client, worker_actor, router, execution, origin
    finally:
        client.__exit__(None, None, None)


def prepare_reviewer(client, origin):
    response = client.post(
        "/api/results/operator/setup",
        headers=origin,
        json={"confirmation": "enable_local_result_review"},
    )
    assert response.status_code == 200, response.text
    return {**origin, "X-Jarvis-Actor-Id": response.json()["data"]["actorId"]}


def decision_body(item, actor, decision="accept"):
    return {
        "command_type": "review_result",
        "run_id": item["execution"]["runtimeRunId"],
        "command_id": "review-once",
        "expected_run_version": item["runVersion"],
        "timestamp": datetime.now(UTC).isoformat(),
        "actor_reference": actor,
        "execution_id": item["execution"]["executionId"],
        "expected_result_hash": item["execution"]["resultHash"],
        "expected_checkpoint_id": item["checkpointId"],
        "expected_task_updated_at": item["taskUpdatedAt"],
        "decision": decision,
        "reason": "Reviewed against the supplied scope.",
        **(
            {"revised_request": "Revise the checklist using confirmed project schedule references."}
            if decision == "request_changes"
            else {}
        ),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", ["accept", "request_changes", "reject"])
async def test_native_review_decision_is_atomic_replayable_and_preserves_model_evidence(
    paused, decision
):
    app, client, _, router, execution, origin = paused
    headers = prepare_reviewer(client, origin)
    pending = client.get("/api/results/reviews", headers=headers)
    assert pending.status_code == 200
    item = pending.json()["data"][0]
    assert len(item["execution"]["result"]["recommendations"]) == 5
    body = decision_body(item, headers["X-Jarvis-Actor-Id"], decision)
    response = client.post(
        f"/api/results/executions/{execution.executionId}/review", headers=headers, json=body
    )
    assert response.status_code == 200, response.text
    receipt = response.json()["data"]
    assert receipt["decision"] == decision
    assert (
        client.post(
            f"/api/results/executions/{execution.executionId}/review", headers=headers, json=body
        ).json()["data"]
        == receipt
    )
    task = client.get(f"/api/tasks/{execution.taskId}").json()["data"]
    assert (
        task["status"]
        == {"accept": "completed", "request_changes": "revision_requested", "reject": "cancelled"}[
            decision
        ]
    )
    stored = app.state.model_execution_repository.get(execution.executionId)
    assert stored.resultHash == execution.resultHash and stored.result == execution.result
    assert stored.requiresHumanReview is True
    assert len(router.plan_requests) == 1
    assert client.get("/api/results/reviews", headers=headers).json()["data"] == []
    if decision == "accept":
        artifact = client.get(
            f"/api/results/artifacts/{receipt['artifactId']}/content", headers=headers
        )
        assert artifact.status_code == 200
        assert sha256(artifact.content).hexdigest() == execution.resultHash
        assert artifact.json()["requiresHumanReview"] is True
        assert receipt["artifactId"] in task["artifactIds"]
    elif decision == "request_changes":
        correction = client.get(f"/api/tasks/{receipt['correctionTaskId']}").json()["data"]
        assert correction["correctionOfTaskId"] == execution.taskId
        assert correction["request"] == body["revised_request"]
        assert correction["status"] == "queued"
    changed = {
        **body,
        "decision": "reject" if decision == "accept" else "accept",
        "revised_request": None,
    }
    assert (
        client.post(
            f"/api/results/executions/{execution.executionId}/review", headers=headers, json=changed
        ).status_code
        == 409
    )


@pytest.mark.asyncio
async def test_worker_execution_permissions_do_not_authorize_output_review(paused):
    _, client, worker_actor, _, execution, origin = paused
    reviewer = prepare_reviewer(client, origin)
    item = client.get("/api/results/reviews", headers=reviewer).json()["data"][0]
    headers = {**origin, "X-Jarvis-Actor-Id": worker_actor}
    response = client.post(
        f"/api/results/executions/{execution.executionId}/review",
        headers=headers,
        json=decision_body(item, worker_actor),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("expected_result_hash", "0" * 64),
        ("expected_checkpoint_id", "checkpoint-wrong"),
        ("expected_run_version", 0),
        ("expected_task_updated_at", "2000-01-01T00:00:00Z"),
        ("run_id", "wrong-run"),
    ],
)
async def test_stale_review_does_not_change_native_state(paused, field, value):
    app, client, _, _, execution, origin = paused
    headers = prepare_reviewer(client, origin)
    item = client.get("/api/results/reviews", headers=headers).json()["data"][0]
    body = decision_body(item, headers["X-Jarvis-Actor-Id"])
    before = app.state.agent_runtime_repository.load_run(execution.runtimeRunId)
    response = client.post(
        f"/api/results/executions/{execution.executionId}/review",
        headers=headers,
        json={**body, field: value},
    )
    assert response.status_code == 409
    assert app.state.agent_runtime_repository.load_run(execution.runtimeRunId) == before
    assert (
        app.state.model_execution_repository.get(execution.executionId).stage
        == "human_review_required"
    )


@pytest.mark.asyncio
async def test_stop_and_inactive_or_denied_reviewer_block_finalization(paused):
    app, client, _, _, execution, origin = paused
    headers = prepare_reviewer(client, origin)
    item = client.get("/api/results/reviews", headers=headers).json()["data"][0]
    body = decision_body(item, headers["X-Jarvis-Actor-Id"])
    app.state.repository.emergency_stop = True
    app.state.repository.persist()
    url = f"/api/results/executions/{execution.executionId}/review"
    assert client.post(url, headers=headers, json=body).status_code == 423
    app.state.repository.emergency_stop = False
    app.state.repository.persist()
    from app.autonomous_worker.provisioning import _find_permission

    permission = _find_permission(app.state.identity_service, "results.review")
    app.state.identity_service.assign_permission(
        headers["X-Jarvis-Actor-Id"],
        AssignPermissionRequest(
            permission_id=permission.id,
            effect="deny",
            resource_type="task",
            resource_id=execution.taskId,
        ),
    )
    assert client.post(url, headers=headers, json=body).status_code == 403
    # Re-running setup must not remove the task-specific denial.
    prepare_reviewer(client, origin)
    assert client.post(url, headers=headers, json=body).status_code == 403
    with app.state.result_review_service.sessions.begin() as session:
        session.execute(
            update(IdentityAgentRow)
            .where(IdentityAgentRow.id == headers["X-Jarvis-Actor-Id"])
            .values(is_enabled=False)
        )
    assert client.post(url, headers=headers, json=body).status_code == 403
    assert (
        client.post(
            "/api/results/operator/setup",
            headers=origin,
            json={"confirmation": "enable_local_result_review"},
        ).status_code
        == 409
    )


@pytest.mark.asyncio
async def test_domain_failure_rolls_back_events_task_artifact_decision_and_outbox(
    paused, monkeypatch
):
    app, client, _, _, execution, origin = paused
    headers = prepare_reviewer(client, origin)
    item = client.get("/api/results/reviews", headers=headers).json()["data"][0]
    body = decision_body(item, headers["X-Jarvis-Actor-Id"])
    sessions = app.state.result_review_service.sessions
    before = app.state.agent_runtime_repository.load_run(execution.runtimeRunId)
    with sessions() as session:
        counts = {
            table: session.scalar(select(func.count()).select_from(table))
            for table in (
                ResultReviewRow,
                ArtifactRow,
                AgentRuntimeEventRow,
                AgentRuntimeProcessedCommandRow,
                OutboxEventRow,
                AuditEventRow,
            )
        }

    def fail(*args, **kwargs):
        raise RuntimeError("fixture interruption before commit")

    monkeypatch.setattr(app.state.task_leases, "_add_event", fail)
    with pytest.raises(RuntimeError, match="fixture interruption"):
        client.post(
            f"/api/results/executions/{execution.executionId}/review", headers=headers, json=body
        )
    assert app.state.agent_runtime_repository.load_run(execution.runtimeRunId) == before
    with sessions() as session:
        assert {
            table: session.scalar(select(func.count()).select_from(table)) for table in counts
        } == counts
        assert session.get(TaskRow, execution.taskId).status == "under_review"
        assert (
            session.get(ModelExecutionRow, execution.executionId).stage == "human_review_required"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Origin": "https://example.invalid"},
        {"Origin": "http://localhost:5173", "Forwarded": "for=127.0.0.1"},
    ],
)
async def test_setup_requires_local_operator_origin(paused, headers):
    _, client, _, _, _, _ = paused
    assert (
        client.post(
            "/api/results/operator/setup",
            headers=headers,
            json={"confirmation": "enable_local_result_review"},
        ).status_code
        == 403
    )


@pytest.mark.asyncio
async def test_expired_grant_is_not_renewed_by_repeated_setup(paused):
    app, client, _, _, execution, origin = paused
    headers = prepare_reviewer(client, origin)
    item = client.get("/api/results/reviews", headers=headers).json()["data"][0]
    body = decision_body(item, headers["X-Jarvis-Actor-Id"])
    with app.state.result_review_service.sessions.begin() as session:
        session.execute(
            update(AgentPermissionAssignmentRow)
            .where(AgentPermissionAssignmentRow.agent_id == headers["X-Jarvis-Actor-Id"])
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    prepare_reviewer(client, origin)
    assert (
        client.post(
            f"/api/results/executions/{execution.executionId}/review", headers=headers, json=body
        ).status_code
        == 403
    )


@pytest.mark.asyncio
async def test_saved_review_and_exact_command_replay_survive_reopening_api(paused):
    from fastapi.testclient import TestClient

    from app.main import create_app

    app, client, _, router, execution, origin = paused
    headers = prepare_reviewer(client, origin)
    item = client.get("/api/results/reviews", headers=headers).json()["data"][0]
    body = decision_body(item, headers["X-Jarvis-Actor-Id"])
    response = client.post(
        f"/api/results/executions/{execution.executionId}/review", headers=headers, json=body
    )
    assert response.status_code == 200
    original = response.json()["data"]
    reopened = create_app(database_url=app.state.settings.database_url)
    with TestClient(reopened) as restarted:
        replay = restarted.post(
            f"/api/results/executions/{execution.executionId}/review", headers=headers, json=body
        )
        assert replay.status_code == 200 and replay.json()["data"] == original
        artifacts = restarted.get("/api/results/artifacts", headers=headers).json()["data"]
        assert len(artifacts) == 1 and artifacts[0]["id"] == original["artifactId"]
        content = restarted.get(
            f"/api/results/artifacts/{original['artifactId']}/content", headers=headers
        )
        assert sha256(content.content).hexdigest() == execution.resultHash
        assert content.headers["Cache-Control"] == "no-store"
    assert len(router.plan_requests) == 1


@pytest.mark.asyncio
async def test_explicit_read_denial_hides_one_result_without_disabling_discovery(paused):
    from app.autonomous_worker.provisioning import _find_permission

    app, client, _, _, execution, origin = paused
    headers = prepare_reviewer(client, origin)
    permission = _find_permission(app.state.identity_service, "results.read")
    app.state.identity_service.assign_permission(
        headers["X-Jarvis-Actor-Id"],
        AssignPermissionRequest(
            permission_id=permission.id,
            effect="deny",
            resource_type="task",
            resource_id=execution.taskId,
        ),
    )
    response = client.get("/api/results/reviews", headers=headers)
    assert response.status_code == 200 and response.json()["data"] == []
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.asyncio
async def test_target_suspension_is_rechecked_inside_native_commit(paused, monkeypatch):
    app, client, _, _, execution, origin = paused
    headers = prepare_reviewer(client, origin)
    item = client.get("/api/results/reviews", headers=headers).json()["data"][0]
    body = decision_body(item, headers["X-Jarvis-Actor-Id"])
    service = app.state.result_review_service
    original = service.authorize

    def suspend_before_transactional_position(actor, task, **kwargs):
        original(actor, task, **kwargs)
        session = kwargs.get("session")
        if session is not None and session.in_transaction():
            session.execute(
                update(IdentityAgentRow)
                .where(IdentityAgentRow.id == execution.targetAgentId)
                .values(lifecycle_state="suspended")
            )

    monkeypatch.setattr(service, "authorize", suspend_before_transactional_position)
    response = client.post(
        f"/api/results/executions/{execution.executionId}/review", headers=headers, json=body
    )
    assert response.status_code == 409
    assert (
        app.state.model_execution_repository.get(execution.executionId).stage
        == "human_review_required"
    )


@pytest.mark.asyncio
async def test_stale_newer_results_do_not_hide_an_older_reviewable_output(paused):
    app, client, _, _, execution, origin = paused
    headers = prepare_reviewer(client, origin)
    with app.state.result_review_service.sessions.begin() as session:
        row = session.get(ModelExecutionRow, execution.executionId)
        values = {
            column.key: getattr(row, column.key) for column in ModelExecutionRow.__table__.columns
        }
        for index in range(100):
            session.add(
                ModelExecutionRow(
                    **{
                        **values,
                        "execution_id": f"stale-execution-{index}",
                        "runtime_attempt_id": f"stale-attempt-{index}",
                        "updated_at": datetime.now(UTC) + timedelta(seconds=1),
                    }
                )
            )
    response = client.get("/api/results/reviews", headers=headers)
    assert response.status_code == 200
    assert [item["execution"]["executionId"] for item in response.json()["data"]] == [
        execution.executionId
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("related", ["lease", "run", "child_task"])
async def test_live_related_work_blocks_review_without_cancelling_it(paused, related):
    from app.models.agent_runtime import CreateAgentRunCommand
    from app.models.domain import Task
    from app.repositories.sqlalchemy import SqlAlchemyRepository
    from tests.agent_runtime_testkit import make_spec

    app, client, _, _, execution, origin = paused
    headers = prepare_reviewer(client, origin)
    item = client.get("/api/results/reviews", headers=headers).json()["data"][0]
    body = decision_body(item, headers["X-Jarvis-Actor-Id"])
    sessions = app.state.result_review_service.sessions
    if related == "lease":
        now = datetime.now(UTC)
        with sessions.begin() as session:
            session.add(
                TaskLeaseRow(
                    task_id=execution.taskId,
                    worker_id=execution.workerId,
                    lease_token="fixture-live-lease",
                    acquired_at=now,
                    renewed_at=now,
                    expires_at=now + timedelta(minutes=5),
                    attempt_number=2,
                    version=1,
                )
            )
    elif related == "run":
        spec = make_spec(
            run_id="related-live-run",
            task_id="separate-related-task",
            agent_id=execution.targetAgentId,
            parent_run_id=execution.runtimeRunId,
        )
        app.state.agent_runtime_service.create_run(
            CreateAgentRunCommand(
                specification=spec,
                command_id="related-create",
                timestamp=spec.created_at,
                actor_reference=execution.targetAgentId,
            )
        )
    else:
        with sessions.begin() as session:
            source = Task.model_validate(session.get(TaskRow, execution.taskId).payload)
            child = source.model_copy(
                update={
                    "id": "child-awaiting-work",
                    "parentTaskId": source.id,
                    "status": "queued",
                    "result": None,
                }
            )
            session.add(SqlAlchemyRepository._task_row(child))
    response = client.post(
        f"/api/results/executions/{execution.executionId}/review", headers=headers, json=body
    )
    assert response.status_code == 409
    assert (
        app.state.model_execution_repository.get(execution.executionId).stage
        == "human_review_required"
    )
    if related == "run":
        assert (
            app.state.agent_runtime_repository.load_run("related-live-run").state.value == "created"
        )


@pytest.mark.asyncio
async def test_corrupt_original_model_bytes_cannot_be_accepted(paused):
    app, client, _, _, execution, origin = paused
    headers = prepare_reviewer(client, origin)
    item = client.get("/api/results/reviews", headers=headers).json()["data"][0]
    body = decision_body(item, headers["X-Jarvis-Actor-Id"])
    with app.state.result_review_service.sessions.begin() as session:
        row = session.get(ModelExecutionRow, execution.executionId)
        row.result_json = {**row.result_json, "summary": "Fixture tampering"}
    response = client.post(
        f"/api/results/executions/{execution.executionId}/review", headers=headers, json=body
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "MODEL_RESULT_CORRUPT"
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.asyncio
async def test_populated_review_history_refuses_migration_downgrade(paused, monkeypatch):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    app, client, _, _, execution, origin = paused
    headers = prepare_reviewer(client, origin)
    item = client.get("/api/results/reviews", headers=headers).json()["data"][0]
    body = decision_body(item, headers["X-Jarvis-Actor-Id"])
    receipt = client.post(
        f"/api/results/executions/{execution.executionId}/review", headers=headers, json=body
    ).json()["data"]
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parents[1] / "migrations"))
    config.set_main_option("sqlalchemy.url", app.state.settings.database_url.replace("%", "%%"))
    monkeypatch.setenv("JARVIS_DATABASE_URL", app.state.settings.database_url)
    with pytest.raises(RuntimeError, match="Preserve reviewed results"):
        command.downgrade(config, "20261106_goals")
    response = client.get(
        f"/api/results/artifacts/{receipt['artifactId']}/content", headers=headers
    )
    assert response.status_code == 200
    assert sha256(response.content).hexdigest() == execution.resultHash
