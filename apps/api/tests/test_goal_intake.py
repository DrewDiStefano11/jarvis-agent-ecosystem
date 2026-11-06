"""Durable offline intake must not dispatch models or create duplicate native work."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db.models import AuditEventRow, OutboxEventRow, TaskRow
from app.main import create_app


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_AUTONOMOUS_WORKER_ENABLED", "false")
    monkeypatch.setenv("JARVIS_MODEL_VERIFIED_CLOUD_ENABLED", "false")
    monkeypatch.setenv("JARVIS_MODEL_EXECUTION_MODE", "disabled")
    return f"sqlite:///{(tmp_path / 'intake.db').as_posix()}"


def submit(client, text="Review the schedules and prepare a discrepancy report.", key="goal-once"):
    return client.post("/api/goals", json={"request": text}, headers={"Idempotency-Key": key})


def test_original_request_one_native_task_replay_and_restart(settings):
    original = "  Review schedules.\nKeep this original request unchanged.  "
    app = create_app(database_url=settings)
    with TestClient(app) as client:
        before = len(client.get("/api/tasks").json()["data"])
        response = submit(client, original)
        assert response.status_code == 201, response.text
        goal = response.json()["data"]
        assert goal["disposition"] == "draft"
        assert goal["messages"][0]["content"] == original
        task = client.get(f"/api/tasks/{goal['taskId']}").json()["data"]
        assert task["request"] == original and task["status"] == "queued"
        assert task["assignedAgentIds"] == []
        assert submit(client, original).json()["data"] == goal
        assert len(client.get("/api/tasks").json()["data"]) == before + 1
        assert submit(client, "A different request").status_code == 409
        with app.state.repository.session_factory() as session:
            assert (
                session.scalar(
                    select(func.count()).select_from(TaskRow).where(TaskRow.id == goal["taskId"])
                )
                == 1
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(AuditEventRow)
                    .where(AuditEventRow.task_id == goal["taskId"])
                )
                == 1
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(OutboxEventRow)
                    .where(OutboxEventRow.event_type == "task.created")
                )
                == 1
            )
    with TestClient(create_app(database_url=settings)) as client:
        assert client.get(f"/api/goals/{goal['id']}").json()["data"] == goal
        assert submit(client, original).json()["data"] == goal
        assert client.get("/api/goals").json()["data"] == [goal]


@pytest.mark.parametrize(
    "body",
    [
        {"request": "  "},
        {"request": "a" * 2001},
        {"request": "Valid request", "enableInference": True},
    ],
)
def test_invalid_or_authority_shaped_input_is_rejected(settings, body):
    with TestClient(create_app(database_url=settings)) as client:
        assert (
            client.post("/api/goals", json=body, headers={"Idempotency-Key": "invalid"}).status_code
            == 422
        )


def test_key_required_pagination_missing_and_nonlocal(settings):
    with TestClient(create_app(database_url=settings)) as client:
        assert client.post("/api/goals", json={"request": "Save a draft"}).status_code == 422
        assert client.get("/api/goals?limit=101").status_code == 422
        assert client.get("/api/goals?offset=-1").status_code == 422
        assert client.get("/api/goals/missing").status_code == 404
        first = submit(client, key="first").json()["data"]
        second = submit(client, key="second").json()["data"]
        assert client.get("/api/goals?limit=1").json()["data"] == [second]
        assert client.get("/api/goals?limit=1&offset=1").json()["data"] == [first]
    with TestClient(create_app(database_url=settings), client=("192.0.2.1", 50000)) as client:
        assert submit(client).status_code == 403
        assert client.get("/api/goals").status_code == 403


def test_intake_never_assembles_context_or_runs_a_model(settings, monkeypatch):
    app = create_app(database_url=settings)
    with TestClient(app) as client:

        def forbidden(*args, **kwargs):
            pytest.fail("Saving intake invoked context/model planning")

        monkeypatch.setattr(app.state.context_assembler, "assemble", forbidden)
        # Saving unexecuted input grants no run or tool authority.
        assert submit(client).status_code == 201
        assert app.state.repository.context_assemblies == {}


def test_failed_goal_commit_rolls_back_task_audit_outbox_and_claim(settings, monkeypatch):
    import app.repositories.sqlalchemy as native_repository

    app = create_app(database_url=settings)
    with TestClient(app, raise_server_exceptions=False) as client:
        before = client.get("/api/tasks").json()["data"]
        original = native_repository.persist_goal

        def interrupted(session, goal):
            original(session, goal)
            raise RuntimeError("Interrupted before task/event commit")

        monkeypatch.setattr(native_repository, "persist_goal", interrupted)
        assert submit(client).status_code == 500
        assert client.get("/api/goals").json()["data"] == []
        assert client.get("/api/tasks").json()["data"] == before
        with app.state.repository.session_factory() as session:
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(OutboxEventRow)
                    .where(OutboxEventRow.event_type == "task.created")
                )
                == 0
            )
        monkeypatch.setattr(native_repository, "persist_goal", original)
        assert submit(client).status_code == 201
        assert len(client.get("/api/tasks").json()["data"]) == len(before) + 1


def test_concurrent_admission_reuses_one_native_task(settings):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    first_app = create_app(database_url=settings)
    second_app = create_app(database_url=settings)
    barrier = Barrier(2)
    with TestClient(first_app) as first, TestClient(second_app) as second:

        def race(client):
            barrier.wait()
            return submit(client)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(race, client) for client in (first, second)]
            results = [future.result() for future in futures]
        assert all(result.status_code in {201, 409} for result in results)
        assert any(result.status_code == 201 for result in results)
        goals = first.get("/api/goals").json()["data"]
        assert len(goals) == 1
        assert submit(first).json()["data"]["taskId"] == goals[0]["taskId"]


def test_downgrade_refuses_populated_goal_history(settings):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    app = create_app(database_url=settings)
    with TestClient(app) as client:
        goal = submit(client).json()["data"]
        root = Path(__file__).parents[1]
        config = Config(str(root / "alembic.ini"))
        config.set_main_option("script_location", str(root / "migrations"))
        config.set_main_option("sqlalchemy.url", settings)
        with pytest.raises(RuntimeError, match="Preserve saved conversations"):
            command.downgrade(config, "20261102_groq")
        with app.state.repository.session_factory() as session:
            assert session.execute(text("PRAGMA foreign_key_check")).all() == []
        assert client.get(f"/api/goals/{goal['id']}").json()["data"] == goal
