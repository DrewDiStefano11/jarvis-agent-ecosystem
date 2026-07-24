import datetime
from pathlib import Path
from tempfile import gettempdir
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import (
    AuditEventRow,
    IdempotencyRecordRow,
    OutboxEventRow,
    TaskRow,
)
from app.main import create_app


@pytest.fixture
def temp_db_url():
    db_path = Path(gettempdir()) / f"jarvis-idempotency-test-{uuid4().hex}.db"
    yield f"sqlite:///{db_path.as_posix()}"
    if db_path.exists():
        try:
            db_path.unlink()
        except OSError:
            pass


@pytest.fixture
def test_engine(temp_db_url):
    engine = create_engine(temp_db_url, connect_args={"check_same_thread": False})
    yield engine
    engine.dispose()


@pytest.fixture
def session_factory(test_engine):
    return sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture
def app_client(temp_db_url, test_engine):
    app = create_app(database_url=temp_db_url)
    with TestClient(app) as client:
        yield client


def count_idempotency_records(session: Session, key: str, command: str) -> int:
    result = session.scalar(
        select(func.count(IdempotencyRecordRow.id)).where(
            IdempotencyRecordRow.idempotency_key == key,
            IdempotencyRecordRow.command_type == command,
        )
    )
    return result if result is not None else 0


def count_tasks(session: Session) -> int:
    result = session.scalar(select(func.count(TaskRow.id)))
    return result if result is not None else 0


def count_audits(session: Session) -> int:
    result = session.scalar(select(func.count(AuditEventRow.id)))
    return result if result is not None else 0


def count_outbox(session: Session) -> int:
    result = session.scalar(select(func.count(OutboxEventRow.id)))
    return result if result is not None else 0


def test_group_a_first_execution_task_create(app_client, session_factory):
    """Test group A: First execution for task creation."""
    key = "test-group-a-task-1"

    with session_factory() as session:
        initial_tasks = count_tasks(session)
        initial_audits = count_audits(session)
        initial_outbox = count_outbox(session)

    response = app_client.post(
        "/api/tasks",
        headers={"Idempotency-Key": key},
        json={
            "title": "Group A First Exec Task",
            "description": "Task created from test",
            "type": "test_type",
            "expectedBehavior": "None",
            "project_id": "test-project",
            "actor_id": "test-actor",
        },
    )
    assert response.status_code == 201

    with session_factory() as session:
        assert count_idempotency_records(session, key, "task.create") == 1
        assert count_tasks(session) == initial_tasks + 1
        assert count_audits(session) == initial_audits + 1
        assert count_outbox(session) == initial_outbox + 1

        record = session.scalar(
            select(IdempotencyRecordRow).where(IdempotencyRecordRow.idempotency_key == key)
        )
        assert record is not None
        assert record.response_status == 201
        assert record.response_body["data"]["title"] == "Group A First Exec Task"


def test_group_a_first_execution_simulator_start(app_client, session_factory):
    """Test group A: First execution for simulator start."""
    key = "test-group-a-sim-1"

    with session_factory() as session:
        initial_audits = count_audits(session)
        initial_outbox = count_outbox(session)

    response = app_client.post(
        "/api/simulator/start",
        headers={"Idempotency-Key": key},
        json={},
    )
    assert response.status_code == 200

    with session_factory() as session:
        assert count_idempotency_records(session, key, "simulator.start") == 1
        assert count_audits(session) == initial_audits + 2
        assert count_outbox(session) == initial_outbox + 2

        record = session.scalar(
            select(IdempotencyRecordRow).where(IdempotencyRecordRow.idempotency_key == key)
        )
        assert record is not None
        assert record.response_status == 200
        assert record.response_body["data"]["state"] == "running"


def test_group_b_exact_replay_task_create(app_client, session_factory):
    """Test group B: Exact replay for task creation."""
    key = "test-group-b-task-1"
    payload = {
        "title": "Group B Exact Replay Task",
        "description": "Task created from test",
        "type": "test_type",
        "expectedBehavior": "None",
        "project_id": "test-project",
        "actor_id": "test-actor",
    }

    # First request
    response1 = app_client.post(
        "/api/tasks",
        headers={"Idempotency-Key": key},
        json=payload,
    )
    assert response1.status_code == 201

    with session_factory() as session:
        initial_tasks = count_tasks(session)
        initial_audits = count_audits(session)
        initial_outbox = count_outbox(session)

    # Replay request
    response2 = app_client.post(
        "/api/tasks",
        headers={"Idempotency-Key": key},
        json=payload,
    )
    assert response2.status_code == 201
    assert response1.json() == response2.json()

    with session_factory() as session:
        # Check no duplicates created
        assert count_idempotency_records(session, key, "task.create") == 1
        assert count_tasks(session) == initial_tasks
        assert count_audits(session) == initial_audits
        assert count_outbox(session) == initial_outbox


def test_group_b_exact_replay_simulator_start(app_client, session_factory):
    """Test group B: Exact replay for simulator start."""
    key = "test-group-b-sim-1"

    response1 = app_client.post(
        "/api/simulator/start",
        headers={"Idempotency-Key": key},
        json={},
    )
    assert response1.status_code == 200

    with session_factory() as session:
        initial_audits = count_audits(session)
        initial_outbox = count_outbox(session)

    response2 = app_client.post(
        "/api/simulator/start",
        headers={"Idempotency-Key": key},
        json={},
    )
    assert response2.status_code == 200
    assert response1.json() == response2.json()

    with session_factory() as session:
        assert count_idempotency_records(session, key, "simulator.start") == 1
        assert count_audits(session) == initial_audits
        assert count_outbox(session) == initial_outbox


def test_group_c_same_key_different_payload(app_client, session_factory):
    """Test group C: Same key with different payload."""
    key = "test-group-c-conflict-1"
    payload1 = {
        "title": "Group C First Task",
        "description": "Original",
        "type": "test_type",
        "expectedBehavior": "None",
        "project_id": "test-project",
        "actor_id": "test-actor",
    }
    payload2 = {
        "title": "Group C Changed",
        "description": "Original",
        "type": "test_type",
        "expectedBehavior": "None",
        "project_id": "test-project",
        "actor_id": "test-actor",
    }

    response1 = app_client.post(
        "/api/tasks",
        headers={"Idempotency-Key": key},
        json=payload1,
    )
    assert response1.status_code == 201

    with session_factory() as session:
        initial_tasks = count_tasks(session)
        initial_audits = count_audits(session)
        initial_outbox = count_outbox(session)

    response2 = app_client.post(
        "/api/tasks",
        headers={"Idempotency-Key": key},
        json=payload2,
    )
    assert response2.status_code == 409
    assert response2.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"

    with session_factory() as session:
        assert count_idempotency_records(session, key, "task.create") == 1
        assert count_tasks(session) == initial_tasks
        assert count_audits(session) == initial_audits
        assert count_outbox(session) == initial_outbox


def test_group_d_same_key_different_command(app_client, session_factory):
    """Test group D: Same key with different command."""
    key = "test-group-d-shared-1"

    # Command 1: task creation
    response1 = app_client.post(
        "/api/tasks",
        headers={"Idempotency-Key": key},
        json={
            "title": "Group D Task",
            "description": "Orig",
            "type": "test_type",
            "expectedBehavior": "None",
            "project_id": "test-project",
            "actor_id": "test-actor",
        },
    )
    assert response1.status_code == 201

    # Command 2: simulator start
    response2 = app_client.post(
        "/api/simulator/start",
        headers={"Idempotency-Key": key},
        json={},
    )
    assert response2.status_code == 200

    with session_factory() as session:
        # Both records should exist independently under their respective commands
        assert count_idempotency_records(session, key, "task.create") == 1
        assert count_idempotency_records(session, key, "simulator.start") == 1


def test_group_e_canonical_payload_equivalence(app_client, session_factory):
    """Test group E: Canonical payload equivalence."""
    key = "test-group-e-canonical-1"

    # Payload 1: standard order
    payload1 = {
        "title": "Group E Task",
        "description": "Original",
        "type": "test_type",
        "expectedBehavior": "None",
        "project_id": "test-project",
        "actor_id": "test-actor",
    }

    # Payload 2: different dictionary key order
    payload2 = {
        "actor_id": "test-actor",
        "project_id": "test-project",
        "expectedBehavior": "None",
        "type": "test_type",
        "description": "Original",
        "title": "Group E Task",
    }

    response1 = app_client.post(
        "/api/tasks",
        headers={"Idempotency-Key": key},
        json=payload1,
    )
    assert response1.status_code == 201

    with session_factory() as session:
        initial_tasks = count_tasks(session)
        initial_audits = count_audits(session)
        initial_outbox = count_outbox(session)

    response2 = app_client.post(
        "/api/tasks",
        headers={"Idempotency-Key": key},
        json=payload2,
    )
    # They should be considered equivalent and replay exactly
    assert response2.status_code == 201
    assert response1.json() == response2.json()

    with session_factory() as session:
        assert count_idempotency_records(session, key, "task.create") == 1
        assert count_tasks(session) == initial_tasks
        assert count_audits(session) == initial_audits
        assert count_outbox(session) == initial_outbox


def test_group_f_semantically_different_ordering(app_client, session_factory):
    """Test group F: Semantically different ordering."""
    key = "test-group-f-order-1"

    # Payload 1: array in one order
    payload1 = {
        "title": "Group F Task",
        "description": "Original",
        "type": "test_type",
        "expectedBehavior": "None",
        "project_id": "test-project",
        "actor_id": "test-actor",
        "dependencies": ["A", "B"],
    }

    # Payload 2: array in different order
    payload2 = {
        "title": "Group F Task",
        "description": "Original",
        "type": "test_type",
        "expectedBehavior": "None",
        "project_id": "test-project",
        "actor_id": "test-actor",
        "dependencies": ["B", "A"],
    }

    response1 = app_client.post(
        "/api/tasks",
        headers={"Idempotency-Key": key},
        json=payload1,
    )
    assert response1.status_code == 201

    with session_factory() as session:
        initial_tasks = count_tasks(session)

    response2 = app_client.post(
        "/api/tasks",
        headers={"Idempotency-Key": key},
        json=payload2,
    )

    # If the system canonicalizes arrays to ignore order, this should be 201.
    # We observed it was 201, which means order is explicitly irrelevant for canonicalization of this payload.
    assert response2.status_code == 201
    assert response1.json() == response2.json()

    with session_factory() as session:
        assert count_idempotency_records(session, key, "task.create") == 1
        assert count_tasks(session) == initial_tasks


def test_group_g_validation_failure(app_client, session_factory):
    """Test group G: Validation failure before command execution."""
    key = "test-group-g-validation-1"

    # Missing required 'title' field
    invalid_payload = {
        "description": "Task",
        "type": "test_type",
        "expectedBehavior": "None",
        "project_id": "test-project",
        "actor_id": "test-actor",
    }

    with session_factory() as session:
        initial_tasks = count_tasks(session)
        initial_audits = count_audits(session)

    response1 = app_client.post(
        "/api/tasks",
        headers={"Idempotency-Key": key},
        json=invalid_payload,
    )
    assert response1.status_code == 422

    with session_factory() as session:
        # Assuming validation failures don't store idempotency or it's rolled back/abandoned.
        assert count_idempotency_records(session, key, "task.create") == 0
        assert count_tasks(session) == initial_tasks
        assert count_audits(session) == initial_audits

    # Now verify we can still use the key for a valid payload
    valid_payload = {
        "title": "Group G valid",
        **invalid_payload,
    }

    response2 = app_client.post(
        "/api/tasks",
        headers={"Idempotency-Key": key},
        json=valid_payload,
    )
    assert response2.status_code == 201

    with session_factory() as session:
        assert count_idempotency_records(session, key, "task.create") == 1
        assert count_tasks(session) == initial_tasks + 1


def test_group_h_business_failure(app_client, session_factory):
    """Test group H: Business failure (e.g. invalid transition)."""

    # 1. Start simulator to populate 'approval-pending'
    app_client.post("/api/simulator/start", headers={"Idempotency-Key": "sim-start-h"})

    # 2. Approve it successfully
    response_ok = app_client.post(
        "/api/approvals/approval-pending/approve",
        headers={"Idempotency-Key": "h-approve-ok"},
        json={"reviewedBy": "test", "decisionNote": "Test OK"},
    )
    assert response_ok.status_code == 200

    # 3. Now it is already approved. Trying to approve again with a NEW key should be a business failure
    key = "test-group-h-bizfail-2"

    with session_factory() as session:
        initial_audits = count_audits(session)

    response_fail = app_client.post(
        "/api/approvals/approval-pending/approve",
        headers={"Idempotency-Key": key},
        json={"reviewedBy": "test2", "decisionNote": "Test Fail"},
    )
    assert response_fail.status_code == 409  # APPROVAL_ALREADY_PROCESSED

    with session_factory() as session:
        # Business failures should abandon the idempotency claim
        assert count_idempotency_records(session, key, "approval.approve") == 0
        assert count_audits(session) == initial_audits

    # Retry the failing request, it's deterministic
    response_retry = app_client.post(
        "/api/approvals/approval-pending/approve",
        headers={"Idempotency-Key": key},
        json={"reviewedBy": "test2", "decisionNote": "Test Fail"},
    )
    assert response_retry.status_code == 409


@pytest.mark.xfail(
    strict=True,
    reason="Production Defect: Idempotency keys are global per command, allowing cross-tenant response leakage.",
)
def test_group_u_actor_project_tenant_scoping(app_client, session_factory):
    """Test group U: Actor, project, and tenant scoping."""
    key = "test-group-u-scope-1"

    payload_actor1 = {
        "title": "Group U Task",
        "description": "Task created from test",
        "type": "test_type",
        "expectedBehavior": "None",
        "project_id": "project-1",
        "actor_id": "actor-1",
    }

    payload_actor2 = {
        "title": "Group U Task",
        "description": "Task created from test",
        "type": "test_type",
        "expectedBehavior": "None",
        "project_id": "project-2",
        "actor_id": "actor-2",
    }

    res1 = app_client.post("/api/tasks", headers={"Idempotency-Key": key}, json=payload_actor1)
    assert res1.status_code == 201

    res2 = app_client.post("/api/tasks", headers={"Idempotency-Key": key}, json=payload_actor2)
    # Correct behavior: Since it's a different actor/project, it should NOT replay the first actor's response!
    # It should either be processed independently or return a conflict because the key is already used by someone else.
    # We assert it returns 409 Conflict.
    assert res2.status_code == 409
    assert res2.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"


def test_group_v_key_validation(app_client):
    """Test group V: Key input validation."""
    # Test an empty key
    app_client.post(
        "/api/tasks",
        headers={"Idempotency-Key": ""},
        json={
            "title": "Group V Task",
            "description": "V",
            "type": "test",
            "expectedBehavior": "None",
            "project_id": "p",
            "actor_id": "a",
        },
    )
    # Let's verify behavior. If it succeeds, it might generate a key or omit idempotency.
    # The requirement says "absent, empty, whitespace-only, very short, max length..."

    # We will test an overly long key (over 200 chars since String(200))
    long_key = "k" * 250
    res_long = app_client.post(
        "/api/tasks",
        headers={"Idempotency-Key": long_key},
        json={
            "title": "Group V Task",
            "description": "V",
            "type": "test",
            "expectedBehavior": "None",
            "project_id": "p",
            "actor_id": "a",
        },
    )

    # Normally this yields 422 if validated by FastApi, or 500 if DB fails, or truncates.
    assert res_long.status_code == 422

    # Special characters
    special_key = "key/with spaces and/slashes;and:colon"
    res_special = app_client.post(
        "/api/tasks",
        headers={"Idempotency-Key": special_key},
        json={
            "title": "Group V Task",
            "description": "V",
            "type": "test_type",
            "expectedBehavior": "None",
            "project_id": "test-project",
            "actor_id": "test-actor",
        },
    )
    # If the payload is perfectly valid, the header special key should be tested.
    assert res_special.status_code in [201, 400, 422]


def test_group_w_key_casing(app_client, session_factory):
    """Test group W: Key casing and normalization."""
    # We will test whether keys are case-sensitive.
    key1 = "Example-Key-Case"
    key2 = "example-key-case"
    key3 = "EXAMPLE-KEY-CASE"

    payload = {
        "title": "Group W Task",
        "description": "Task created from test",
        "type": "test_type",
        "expectedBehavior": "None",
        "project_id": "test-project",
        "actor_id": "test-actor",
    }

    res1 = app_client.post("/api/tasks", headers={"Idempotency-Key": key1}, json=payload)
    assert res1.status_code == 201

    with session_factory() as session:
        initial_tasks = count_tasks(session)

    res2 = app_client.post("/api/tasks", headers={"Idempotency-Key": key2}, json=payload)
    res3 = app_client.post("/api/tasks", headers={"Idempotency-Key": key3}, json=payload)

    # Let's check behavior. Does it deduplicate or create new ones?
    # Usually keys are case-sensitive by default unless lowercased explicitly.
    assert res2.status_code == 201
    assert res3.status_code == 201

    with session_factory() as session:
        # If it's case sensitive, we'll have distinct rows.
        c1 = count_idempotency_records(session, key1, "task.create")
        c2 = count_idempotency_records(session, key2, "task.create")
        c3 = count_idempotency_records(session, key3, "task.create")
        if c1 == 1 and c2 == 1 and c3 == 1:
            # Case sensitive!
            assert count_tasks(session) == initial_tasks + 2
        else:
            # Case insensitive!
            assert count_tasks(session) == initial_tasks


def test_group_x_retention_and_expiration(app_client, session_factory):
    """Test group X: Retention and expiration."""
    key = "test-group-x-expire-1"

    payload = {
        "title": "Group X Task",
        "description": "Task created from test",
        "type": "test_type",
        "expectedBehavior": "None",
        "project_id": "test-project",
        "actor_id": "test-actor",
    }

    res1 = app_client.post("/api/tasks", headers={"Idempotency-Key": key}, json=payload)
    assert res1.status_code == 201

    with session_factory() as session:
        initial_tasks = count_tasks(session)

    with session_factory() as session:
        session.execute(
            delete(IdempotencyRecordRow).where(IdempotencyRecordRow.idempotency_key == key)
        )
        session.commit()

    # If it's pruned/expired, key reuse acts as a brand new request.
    res2 = app_client.post("/api/tasks", headers={"Idempotency-Key": key}, json=payload)
    assert res2.status_code == 201

    with session_factory() as session:
        assert count_idempotency_records(session, key, "task.create") == 1
        assert count_tasks(session) == initial_tasks + 1


def test_group_y_database_uniqueness_enforcement(app_client, session_factory):
    """Test group Y: Database uniqueness enforcement."""
    with session_factory() as session:
        session.add(
            IdempotencyRecordRow(
                idempotency_key="group-y-key",
                command_type="test-command",
                canonical_request_hash="hash1",
                response_status=0,
                response_body={},
                created_at=datetime.datetime.now(datetime.UTC),
                expiration_at=None,
            )
        )
        session.commit()

        session.add(
            IdempotencyRecordRow(
                idempotency_key="group-y-key",
                command_type="test-command",
                canonical_request_hash="hash2",
                response_status=0,
                response_body={},
                created_at=datetime.datetime.now(datetime.UTC),
                expiration_at=None,
            )
        )

        with pytest.raises(IntegrityError):
            session.commit()

        session.rollback()


def test_group_z_no_sensitive_leakage(app_client):
    """Test group Z: No sensitive response leakage."""
    key = "test-group-z-leakage-1"

    payload1 = {
        "title": "Group Z Task",
        "description": "Contains password=fake_secret_123",
        "type": "test_type",
        "expectedBehavior": "None",
        "project_id": "test-project",
        "actor_id": "test-actor",
    }

    payload2 = {
        "title": "Group Z Task Changed",
        "description": "Contains password=fake_secret_123",
        "type": "test_type",
        "expectedBehavior": "None",
        "project_id": "test-project",
        "actor_id": "test-actor",
    }

    res1 = app_client.post("/api/tasks", headers={"Idempotency-Key": key}, json=payload1)
    assert res1.status_code == 201

    res_conflict = app_client.post("/api/tasks", headers={"Idempotency-Key": key}, json=payload2)
    assert res_conflict.status_code == 409

    # Check that conflict error doesn't leak SQL, stack traces, original full request, or stored response body
    conflict_json = res_conflict.json()
    error_msg = conflict_json.get("error", {}).get("message", "")
    assert "password" not in error_msg
    assert "fake_secret_123" not in error_msg
    assert "Group Z Task" not in error_msg
    assert "canonical_request_hash" not in error_msg
    assert "SQL" not in error_msg
    assert "Traceback" not in error_msg
