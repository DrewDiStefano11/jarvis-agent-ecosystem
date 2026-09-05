from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db.models import (
    AuditEventRow,
    IdempotencyRecordRow,
    OutboxEventRow,
    TaskRow,
)
from app.main import create_app
from app.models.domain import CreateTaskRequest
from app.services.unit_of_work import UnitOfWork
from tests.test_autonomous_worker import VALID_RESULT, FakeRouter, worker_fixture
from tests.test_context_integration import context_body
from tests.test_persistence import database_url


def source_state(app, source_id: str, **changes):
    """Simulate a separate worker updating durable state while the API cache is stale."""
    with app.state.repository.session_factory() as session, session.begin():
        row = session.get(TaskRow, source_id)
        assert row is not None
        row.payload = row.payload | changes
        if "status" in changes:
            row.status = changes["status"]
        if "projectId" in changes:
            row.project_id = changes["projectId"]
        return row.payload


@pytest.mark.parametrize("status", ["under_review", "failed", "cancelled", "completed"])
def test_correction_preserves_source_project_provenance_and_restart(tmp_path: Path, status):
    url = database_url(tmp_path / f"correction-{status}.db")
    app = create_app(database_url=url)
    headers = {"Idempotency-Key": "correction-command"}
    with TestClient(app) as client:
        source = client.post(
            "/api/tasks", json={"title": "Original task", "description": "Original facts"}
        ).json()["data"]
        before = source_state(app, source["id"], status=status, projectId="project-existing")
        body = {
            "title": "Corrected task",
            "description": "Use these corrected facts",
            "correctionOfTaskId": source["id"],
        }
        response = client.post("/api/tasks", json=body, headers=headers)
        assert response.status_code == 201
        corrected = response.json()["data"]
        assert corrected["id"] != source["id"]
        assert corrected["correctionOfTaskId"] == source["id"]
        assert corrected["projectId"] == "project-existing"
        assert corrected["description"] == corrected["request"] == body["description"]
        assert corrected["status"] == "queued"
        assert corrected["parentTaskId"] is None
        assert corrected["assignedAgentIds"] == corrected["artifactIds"] == []
        assert corrected["result"] is None
        assert corrected["error"] is None
        assert client.get(f"/api/tasks/{source['id']}").json()["data"] == before
        with client.websocket_connect("/ws/events") as socket:
            snapshot = socket.receive_json()["payload"]["snapshot"]
            assert next(task for task in snapshot["tasks"] if task["id"] == source["id"]) == before
            assert (
                next(task for task in snapshot["tasks"] if task["id"] == corrected["id"])
                == corrected
            )
        assert client.post("/api/tasks", json=body, headers=headers).json() == response.json()
        changed_source = body | {"correctionOfTaskId": "task-other"}
        conflict = client.post("/api/tasks", json=changed_source, headers=headers)
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"
        with app.state.repository.session_factory() as session:
            audit = session.scalars(
                select(AuditEventRow).where(AuditEventRow.task_id == corrected["id"])
            ).one()
            assert audit.payload["payload"] == {
                "correctionOfTaskId": source["id"],
                "projectId": "project-existing",
            }
            events = [
                row
                for row in session.scalars(select(OutboxEventRow))
                if row.envelope.get("taskId") == corrected["id"]
            ]
            assert len(events) == 1
            assert events[0].event_type == "task.created"
            assert events[0].envelope["payload"]["task"] == corrected
            assert audit.sequence_number == events[0].sequence_number
        # Preparing the new task must preserve its source even while the API's
        # task cache still predates the separate worker's update.
        assembly = context_body("Corrected planning facts", task_id=corrected["id"])
        assembly["projectId"] = corrected["projectId"]
        assert client.post("/api/context/assemblies", json=assembly).status_code == 201
        assert client.get(f"/api/tasks/{source['id']}").json()["data"] == before
    with TestClient(create_app(database_url=url)) as restarted:
        assert restarted.get(f"/api/tasks/{corrected['id']}").json()["data"] == corrected
        assert restarted.get(f"/api/tasks/{source['id']}").json()["data"] == before
        assert restarted.post("/api/tasks", json=body, headers=headers).json() == response.json()


@pytest.mark.parametrize(
    "status",
    [
        "queued",
        "planning",
        "assigned",
        "in_progress",
        "waiting",
        "waiting_for_approval",
        "revision_requested",
        "paused",
        "retrying",
    ],
)
def test_live_source_rejected_without_creating_or_mutating_tasks(tmp_path: Path, status):
    app = create_app(database_url=database_url(tmp_path / "live.db"))
    with TestClient(app) as client:
        source_state(app, "task-demo", status=status)
        before = client.get("/api/tasks").json()
        response = client.post(
            "/api/tasks",
            json={
                "title": "Correction",
                "description": "Correct facts",
                "correctionOfTaskId": "task-demo",
            },
            headers={"Idempotency-Key": "rejected-correction"},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "TASK_CORRECTION_NOT_ALLOWED"
        assert client.get("/api/tasks").json() == before
        with app.state.repository.session_factory() as session:
            assert session.scalar(select(func.count()).select_from(IdempotencyRecordRow)) == 0


@pytest.mark.parametrize("source_id,expected", [("missing-task", 404), ("bad/id", 422), ("", 422)])
def test_correction_source_must_exist_and_have_a_valid_identifier(tmp_path, source_id, expected):
    with TestClient(create_app(database_url=database_url(tmp_path / "invalid.db"))) as client:
        before = client.get("/api/tasks").json()
        response = client.post(
            "/api/tasks",
            json={
                "title": "Correction",
                "description": "Correct facts",
                "correctionOfTaskId": source_id,
            },
        )
        assert response.status_code == expected
        assert client.get("/api/tasks").json() == before


@pytest.mark.parametrize(
    "change,code",
    [
        ({"status": "in_progress"}, "TASK_CORRECTION_NOT_ALLOWED"),
        ({"projectId": "changed-project"}, "TASK_CORRECTION_SOURCE_CHANGED"),
    ],
)
def test_correction_rechecks_source_inside_commit(tmp_path, monkeypatch, change, code):
    app = create_app(database_url=database_url(tmp_path / "changed.db"))
    original = app.state.repository.enqueue_event

    def race(envelope, idempotency=None, **kwargs):
        source_state(app, "task-demo", **change)
        return original(envelope, idempotency, **kwargs)

    with TestClient(app) as client:
        source_state(app, "task-demo", status="completed")
        monkeypatch.setattr(app.state.repository, "enqueue_event", race)
        response = client.post(
            "/api/tasks",
            json={
                "title": "Raced correction",
                "description": "Correct facts",
                "correctionOfTaskId": "task-demo",
            },
            headers={"Idempotency-Key": "raced-correction"},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == code
        assert not any(
            task["title"] == "Raced correction" for task in client.get("/api/tasks").json()["data"]
        )
        with app.state.repository.session_factory() as session:
            assert session.scalar(select(func.count()).select_from(OutboxEventRow)) == 0
            assert session.scalar(select(func.count()).select_from(IdempotencyRecordRow)) == 0


def test_correction_rollback_and_lost_acknowledgement_are_atomic(tmp_path, monkeypatch):
    app = create_app(database_url=database_url(tmp_path / "atomic.db"))
    body = {
        "title": "Atomic correction",
        "description": "Correct facts",
        "correctionOfTaskId": "task-demo",
    }
    headers = {"Idempotency-Key": "atomic-correction"}

    def fail_commit(_):
        raise RuntimeError("fixture commit failure")

    async def lose_acknowledgement(_):
        raise RuntimeError("fixture lost response")

    with TestClient(app, raise_server_exceptions=False) as client:
        before = source_state(app, "task-demo", status="completed")
        with monkeypatch.context() as patch:
            patch.setattr(UnitOfWork, "commit", fail_commit)
            assert client.post("/api/tasks", json=body, headers=headers).status_code == 500
        with app.state.repository.session_factory() as session:
            assert session.scalar(select(TaskRow.id).where(TaskRow.title == body["title"])) is None
            assert session.scalar(select(func.count()).select_from(OutboxEventRow)) == 0
            assert session.scalar(select(func.count()).select_from(IdempotencyRecordRow)) == 0
        with monkeypatch.context() as patch:
            patch.setattr(app.state.broker, "_publish", lose_acknowledgement)
            assert client.post("/api/tasks", json=body, headers=headers).status_code == 500
        replay = client.post("/api/tasks", json=body, headers=headers)
        assert replay.status_code == 201
        corrected = replay.json()["data"]
        assert client.get(f"/api/tasks/{corrected['id']}").json()["data"] == corrected
        assert client.get("/api/tasks/task-demo").json()["data"] == before
        with app.state.repository.session_factory() as session:
            assert (
                session.scalar(
                    select(func.count()).select_from(TaskRow).where(TaskRow.title == body["title"])
                )
                == 1
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(AuditEventRow)
                    .where(AuditEventRow.task_id == corrected["id"])
                )
                == 1
            )
            assert session.scalar(select(func.count()).select_from(OutboxEventRow)) == 1


def test_legacy_create_serialization_and_stored_idempotency_response_remain_compatible(tmp_path):
    body = {"title": "Legacy request", "description": "The old exact payload", "priority": "medium"}
    assert CreateTaskRequest.model_validate(body).model_dump(mode="json") == body
    assert (
        CreateTaskRequest.model_validate(body | {"correctionOfTaskId": None}).model_dump(
            mode="json"
        )
        == body
    )
    app = create_app(database_url=database_url(tmp_path / "legacy.db"))
    with TestClient(app) as client:
        created = client.post("/api/tasks", json=body, headers={"Idempotency-Key": "legacy"})
        assert "correctionOfTaskId" not in created.json()["data"]
        with app.state.repository.session_factory() as session:
            record = session.scalars(select(IdempotencyRecordRow)).one()
            assert record.canonical_request_hash == app.state.repository.request_hash(body)
            assert "correctionOfTaskId" not in record.response_body["data"]
        replay = client.post("/api/tasks", json=body, headers={"Idempotency-Key": "legacy"})
        assert replay.json() == created.json()


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [json.dumps(VALID_RESULT), "invalid fixture output"])
async def test_correction_preserves_existing_execution_and_does_not_start_another(
    tmp_path, content
):
    router = FakeRouter([content, content])
    run_id = "run-correction-source"
    app, client, actor, worker = worker_fixture(tmp_path, router=router, run_id=run_id)
    headers = {"X-Jarvis-Actor-Id": actor}
    try:
        await app.state.autonomous_worker_service.run_once(worker.id)
        source = client.get("/api/tasks/task-demo").json()
        assert source["data"]["status"] in {"completed", "under_review"}
        execution = client.get(
            "/api/model-executions", params={"taskId": "task-demo"}, headers=headers
        ).json()
        runtime = client.get(f"/api/agent-runtime/runs/{run_id}", headers=headers).json()
        response = client.post(
            "/api/tasks",
            json={
                "title": "Operator correction",
                "description": "Use corrected facts",
                "correctionOfTaskId": "task-demo",
            },
            headers={"Idempotency-Key": "runtime-correction"},
        )
        assert response.status_code == 201
        corrected = response.json()["data"]
        assert client.get("/api/tasks/task-demo").json() == source
        assert client.get(f"/api/agent-runtime/runs/{run_id}", headers=headers).json() == runtime
        assert await app.state.autonomous_worker_service.run_once(worker.id) is None
        assert (
            client.get(
                "/api/model-executions", params={"taskId": "task-demo"}, headers=headers
            ).json()
            == execution
        )
    finally:
        client.__exit__(None, None, None)
    with TestClient(create_app(database_url=database_url(tmp_path / f"{run_id}.db"))) as restarted:
        assert restarted.get("/api/tasks/task-demo").json() == source
        assert restarted.get(f"/api/tasks/{corrected['id']}").json()["data"] == corrected
        assert restarted.get(f"/api/agent-runtime/runs/{run_id}", headers=headers).json() == runtime
        assert (
            restarted.get(
                "/api/model-executions", params={"taskId": "task-demo"}, headers=headers
            ).json()
            == execution
        )
