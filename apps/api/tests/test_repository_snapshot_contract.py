import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base
from app.main import app
from app.models.domain import Agent, Approval, Artifact, AuditEvent, Department, Notification, Task
from app.repositories.sqlalchemy import SqlAlchemyRepository


def database_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"

@pytest.fixture
def temp_db_url(tmp_path: Path) -> str:
    db_path = tmp_path / f"test-{uuid4().hex}.db"
    return database_url(db_path)

@pytest.fixture
def repository(temp_db_url: str) -> SqlAlchemyRepository:
    engine = create_engine(temp_db_url)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    repo = SqlAlchemyRepository(SessionLocal)

    # Actually seed data!
    from app.services.seed import build_seed
    seed = build_seed()
    repo.departments = {x["id"]: Department.model_validate(x) for x in seed["departments"]}
    repo.agents = {x["id"]: Agent.model_validate(x) for x in seed["agents"]}
    repo.tasks = {x["id"]: Task.model_validate(x) for x in seed["tasks"]}
    repo.approvals = {x["id"]: Approval.model_validate(x) for x in seed["approvals"]}
    repo.artifacts = {x["id"]: Artifact.model_validate(x) for x in seed["artifacts"]}
    repo.notifications = {x["id"]: Notification.model_validate(x) for x in seed["notifications"]}
    repo.audit = [AuditEvent.model_validate(x) for x in seed["audit"]]

    yield repo
    engine.dispose()

def assert_no_objects(value: object) -> None:
    from pydantic import BaseModel
    if isinstance(value, dict):
        for _k, v in value.items():
            assert_no_objects(v)
    elif isinstance(value, list):
        for item in value:
            assert_no_objects(item)
    else:
        assert not isinstance(value, BaseModel), f"Found BaseModel instance: {type(value)}"
        assert not isinstance(value, datetime), f"Found datetime instance: {type(value)}"
        assert type(value).__name__ not in ("UUID", "set"), f"Found invalid type: {type(value)}"

def test_exact_top_level_schema(repository: SqlAlchemyRepository) -> None:
    snapshot = repository.snapshot()
    expected_keys = {
        "departments",
        "agents",
        "tasks",
        "approvals",
        "artifacts",
        "notifications",
        "auditEvents",
        "emergencyStop",
    }
    assert set(snapshot.keys()) == expected_keys

def test_json_safe_serialization(repository: SqlAlchemyRepository) -> None:
    snapshot = repository.snapshot()
    # It should successfully serialize to JSON string
    json_str = json.dumps(snapshot)
    assert isinstance(json_str, str)
    # Recursively ensure no pydantic models, datetimes, etc are present
    assert_no_objects(snapshot)

def test_field_aliases_and_casing(repository: SqlAlchemyRepository) -> None:
    snapshot = repository.snapshot()

    # Verify camelCase aliases on some known items
    if snapshot["tasks"]:
        first_task = snapshot["tasks"][0]
        # internal is schema_version, public is schemaVersion
        assert "schemaVersion" in first_task
        assert "schema_version" not in first_task

    if snapshot["auditEvents"]:
        first_audit = snapshot["auditEvents"][0]
        assert "eventType" in first_audit
        assert "event_type" not in first_audit

def test_mutation_isolation(repository: SqlAlchemyRepository) -> None:
    snapshot = repository.snapshot()

    # Mutate a top level collection
    snapshot["tasks"] = []
    # Mutate one returned entity dictionary
    if snapshot["agents"]:
        snapshot["agents"][0]["status"] = "offline"
    # Mutate an audit detail structure
    if snapshot["auditEvents"]:
        snapshot["auditEvents"][0]["summary"] = "Mutated summary"

    second_snapshot = repository.snapshot()

    assert len(second_snapshot["tasks"]) > 0
    if second_snapshot["agents"]:
        assert second_snapshot["agents"][0]["status"] != "offline"
    if second_snapshot["auditEvents"]:
        assert second_snapshot["auditEvents"][0]["summary"] != "Mutated summary"

def test_snapshot_independence(repository: SqlAlchemyRepository) -> None:
    snap1 = repository.snapshot()
    snap2 = repository.snapshot()

    # Value equality
    assert snap1 == snap2
    # Not the same dictionary
    assert snap1 is not snap2

    # Collections are distinct
    assert snap1["tasks"] is not snap2["tasks"]

    if snap1["tasks"]:
        # Entity dictionaries are distinct
        assert snap1["tasks"][0] is not snap2["tasks"][0]

def test_repository_detachment(repository: SqlAlchemyRepository) -> None:
    snap1 = repository.snapshot()

    # Mutate a repository model
    task_keys = list(repository.tasks.keys())
    if task_keys:
        first_task_id = task_keys[0]
        original_status = repository.tasks[first_task_id].status

        repository.tasks[first_task_id].status = "completed"

        # Assert already-returned snapshot does not change
        assert snap1["tasks"][0]["status"] == original_status

        # Take new snapshot and assert it reflects mutation
        snap2 = repository.snapshot()
        assert snap2["tasks"][0]["status"] == "completed"

def test_stable_ordering(repository: SqlAlchemyRepository) -> None:
    snap1 = repository.snapshot()
    snap2 = repository.snapshot()

    assert [t["id"] for t in snap1["tasks"]] == [t["id"] for t in snap2["tasks"]]
    assert [t["id"] for t in snap1["agents"]] == [t["id"] for t in snap2["agents"]]

def test_no_database_reads_writes(repository: SqlAlchemyRepository, monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    def fake_session_factory(*args, **kwargs):
        nonlocal called
        called = True
        raise RuntimeError("Database session factory called during snapshot")

    monkeypatch.setattr(repository, "session_factory", fake_session_factory)

    _ = repository.snapshot()

    assert not called, "Database session was opened during snapshot"

def test_legacy_wire_equivalence(repository: SqlAlchemyRepository) -> None:
    # Generate optimized snapshot
    optimized_snapshot = repository.snapshot()

    # Generate legacy snapshot
    legacy_snapshot_raw = deepcopy(
        {
            "departments": list(repository.departments.values()),
            "agents": list(repository.agents.values()),
            "tasks": list(repository.tasks.values()),
            "approvals": list(repository.approvals.values()),
            "artifacts": list(repository.artifacts.values()),
            "notifications": list(repository.notifications.values()),
            "auditEvents": repository.audit,
            "emergencyStop": repository.emergency_stop,
        }
    )

    from app.main import _json_snapshot
    legacy_snapshot = _json_snapshot(legacy_snapshot_raw)

    # We serialize both via JSON to account for full wire encoding
    # using fastapi.encoders.jsonable_encoder or just json.dumps if they are both dicts of primitives
    # _json_snapshot uses model_dump(mode="json") implicitly for BaseModel so we should be close.
    # Actually, they might be directly comparable dicts now.
    import json

    assert json.dumps(optimized_snapshot) == json.dumps(legacy_snapshot)

def test_sensitive_field_inspection(repository: SqlAlchemyRepository) -> None:
    snapshot = repository.snapshot()
    json_str = json.dumps(snapshot)

    # Ensure no leaking of internal fields
    assert "sqlite://" not in json_str
    assert "password" not in json_str.lower()
    assert "secret" not in json_str.lower()
    # Ensure no local file paths
    # Just basic sanity checks on fields that shouldn't exist




def test_websocket_integration(monkeypatch: pytest.MonkeyPatch) -> None:
    # Ensure no database creation here if it affects state, but TestClient starts app normally
    with TestClient(app) as client:
        with client.websocket_connect("/ws/events") as websocket:
            data = websocket.receive_json()
            assert data["eventType"] == "system.snapshot"
            payload = data["payload"]
            assert "snapshot" in payload
            snapshot = payload["snapshot"]
            expected_keys = {
                "departments",
                "agents",
                "tasks",
                "approvals",
                "artifacts",
                "notifications",
                "auditEvents",
                "emergencyStop",
            }
            assert set(snapshot.keys()) == expected_keys
            # Check camelCase
            if snapshot["tasks"]:
                assert "schemaVersion" in snapshot["tasks"][0]
            # Must be valid json (it just was parsed from json)
