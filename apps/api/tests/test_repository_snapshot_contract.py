import json
from datetime import UTC, datetime

import pytest

from app.db.models import Base
from app.db.session import create_database_engine, create_session_factory
from app.models.domain import Task
from app.repositories.sqlalchemy import SqlAlchemyRepository


def test_snapshot_returns_json_safe_primitives():
    engine = create_database_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    repo = SqlAlchemyRepository(create_session_factory(engine), 60, 3)

    # Pre-seed is loaded on init. Ensure tasks exist
    repo.tasks["task-test-primitive"] = Task(
        id="task-test-primitive",
        title="Primitive Test Task",
        description="A task to test primitives",
        request="Do something",
        createdBy="test-user",
        assignedManagerId="manager-1",
        priority="medium",
        createdAt=datetime.now(UTC),
        updatedAt=datetime.now(UTC),
    )

    snapshot = repo.snapshot()

    # Assert top-level keys exist and are correct types
    assert isinstance(snapshot, dict)
    assert "tasks" in snapshot
    assert "auditEvents" in snapshot

    # Assert nested fields are primitive
    task_dicts = snapshot["tasks"]
    assert isinstance(task_dicts, list)

    test_task_dict = next(t for t in task_dicts if t["id"] == "task-test-primitive")
    assert isinstance(test_task_dict, dict)

    # Date should be string (json dump behavior)
    assert isinstance(test_task_dict["createdAt"], str)

    # Check that it completely passes json.dumps
    # This validates Test group A & B from prompt
    try:
        json_string = json.dumps(snapshot)
        assert isinstance(json_string, str)
    except TypeError as e:
        pytest.fail(f"Snapshot contains non-JSON safe elements: {e}")


def test_snapshot_mutation_isolation():
    engine = create_database_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    repo = SqlAlchemyRepository(create_session_factory(engine), 60, 3)

    repo.tasks["task-mut-1"] = Task(
        id="task-mut-1",
        title="Mutation Isolation Test",
        description="Test",
        request="Test",
        createdBy="user",
        assignedManagerId="manager",
        priority="high",
        createdAt=datetime.now(UTC),
        updatedAt=datetime.now(UTC),
    )

    snapshot_1 = repo.snapshot()

    # Mutate the returned snapshot (Top-level mutation)
    snapshot_1["tasks"] = []

    # Snapshot should be unaffected
    snapshot_2 = repo.snapshot()
    assert len(snapshot_2["tasks"]) > 0
    assert any(t["id"] == "task-mut-1" for t in snapshot_2["tasks"])

    # Deep nested mutation isolation
    task_dict = next(t for t in snapshot_2["tasks"] if t["id"] == "task-mut-1")
    task_dict["title"] = "MUTATED TITLE"

    snapshot_3 = repo.snapshot()
    fresh_task = next(t for t in snapshot_3["tasks"] if t["id"] == "task-mut-1")
    assert fresh_task["title"] == "Mutation Isolation Test"


def test_snapshot_to_snapshot_independence():
    engine = create_database_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    repo = SqlAlchemyRepository(create_session_factory(engine), 60, 3)

    repo.tasks["task-ind"] = Task(
        id="task-ind",
        title="Ind",
        description="Ind",
        request="Ind",
        createdBy="usr",
        assignedManagerId="mgr",
        priority="low",
        createdAt=datetime.now(UTC),
        updatedAt=datetime.now(UTC),
    )

    snapshot_a = repo.snapshot()
    snapshot_b = repo.snapshot()

    assert snapshot_a == snapshot_b
    assert snapshot_a is not snapshot_b

    # Lists inside shouldn't be identical references
    assert snapshot_a["tasks"] is not snapshot_b["tasks"]

    snapshot_a["tasks"][0]["title"] = "CHANGED"
    assert snapshot_a["tasks"][0]["title"] != snapshot_b["tasks"][0]["title"]


def test_snapshot_model_detachment():
    engine = create_database_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    repo = SqlAlchemyRepository(create_session_factory(engine), 60, 3)

    repo.tasks["task-detach"] = Task(
        id="task-detach",
        title="Detach",
        description="D",
        request="R",
        createdBy="u",
        assignedManagerId="m",
        priority="high",
        createdAt=datetime.now(UTC),
        updatedAt=datetime.now(UTC),
    )

    snapshot = repo.snapshot()
    task_rep = next(t for t in snapshot["tasks"] if t["id"] == "task-detach")

    # Ensure it's a dict and not the pydantic model instance
    assert type(task_rep) is dict

    # Update the actual model inside repo
    repo.tasks["task-detach"].title = "UPDATED TITLE"

    # Ensure the old snapshot value didn't magically update
    assert task_rep["title"] == "Detach"
