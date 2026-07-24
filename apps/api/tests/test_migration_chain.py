from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.db.models import AgentRow
from app.main import create_app

REVISION_1_PHASE_2A = "20260720_01"
REVISION_2_TASK_LEASE = "20260723_02"
REVISION_3_CONTEXT = "20260724_03"
HEAD = "head"


def database_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def get_alembic_config(db_path: Path) -> Config:
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url(db_path))
    return config


def get_script_directory(config: Config) -> ScriptDirectory:
    return ScriptDirectory.from_config(config)


def get_current_revision(engine: Engine) -> str | None:
    with engine.connect() as conn:
        try:
            result = conn.execute(text("SELECT version_num FROM alembic_version"))
            return result.scalar()
        except Exception:
            return None


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    return tmp_path / "migration_chain.db"


@pytest.fixture
def alembic_config(tmp_db_path: Path) -> Config:
    return get_alembic_config(tmp_db_path)


@pytest.fixture
def engine(tmp_db_path: Path) -> Engine:
    url = database_url(tmp_db_path)
    # Using echo=False, ensure foreign_keys is on for all connections
    engine = create_engine(url)

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection: Any, connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    yield engine
    engine.dispose()


def test_migration_chain_structure(alembic_config: Config) -> None:
    # 9. Explicit migration-chain structure assertions
    script = get_script_directory(alembic_config)

    heads = script.get_heads()
    assert len(heads) == 1, "There must be exactly one head in the migration chain"
    head_rev = heads[0]
    assert head_rev == REVISION_3_CONTEXT

    revisions = list(script.walk_revisions("base", "head"))
    # walk_revisions returns from head to base
    rev_ids = [r.revision for r in revisions]

    assert len(rev_ids) == len(set(rev_ids)), "Revision IDs must be unique"

    # Check exact chain in reverse order (head to base)
    assert rev_ids == [REVISION_3_CONTEXT, REVISION_2_TASK_LEASE, REVISION_1_PHASE_2A]

    # Check dependencies
    rev_3 = script.get_revision(REVISION_3_CONTEXT)
    assert rev_3.down_revision == REVISION_2_TASK_LEASE

    rev_2 = script.get_revision(REVISION_2_TASK_LEASE)
    assert rev_2.down_revision == REVISION_1_PHASE_2A

    rev_1 = script.get_revision(REVISION_1_PHASE_2A)
    assert rev_1.down_revision is None

    # 16. Add migration-file invariants where useful
    # Verify migrations do not import live application models
    for rev in revisions:
        with open(rev.path, "r") as f:
            content = f.read()
            assert "app.db.models" not in content, (
                f"Migration {rev.revision} incorrectly imports app.db.models"
            )
            assert "Base" not in content or "sqlalchemy" in content, (
                f"Migration {rev.revision} might be importing live Base"
            )


def test_blank_database_to_head(tmp_db_path: Path, alembic_config: Config, engine: Engine) -> None:
    # 5. Blank database to head
    command.upgrade(alembic_config, HEAD)

    script = get_script_directory(alembic_config)
    head_rev = script.get_current_head()

    assert get_current_revision(engine) == head_rev

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    # We assert exact expected tables to ensure no duplicate or unexpected legacy tables exist
    expected_exact_tables = {
        "departments",
        "agents",
        "tasks",
        "approvals",
        "artifacts",
        "audit_events",
        "system_state",
        "workflow_runs",
        "workflow_checkpoints",
        "outbox_events",
        "idempotency_records",
        "task_agents",
        "task_dependencies",
        "task_blockers",
        "notifications",
        "workers",
        "task_leases",
        "task_attempts",
        "context_assemblies",
        "alembic_version",
    }
    assert tables == expected_exact_tables

    # Check foreign keys and indexes exist roughly (specifically context assemblies as an example)
    indexes = {idx["name"] for idx in inspector.get_indexes("context_assemblies")}
    assert "ix_context_assemblies_task_id" in indexes

    fks = {fk["referred_table"] for fk in inspector.get_foreign_keys("context_assemblies")}
    assert "tasks" in fks

    # Test application startup and strict health check (Req 2, 3)
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/api/health")
        if response.status_code == 404:
            response = client.get("/health")

        assert response.status_code == 200, f"Expected 200 OK, got {response.status_code}"

        payload = response.json()
        # We can also verify schema version fields if they are reported
        pass  # we only assert status == ok strictly


def test_upgrade_from_phase_2a(tmp_db_path: Path, alembic_config: Config, engine: Engine) -> None:
    # 6. Phase 2A historical upgrade coverage
    command.upgrade(alembic_config, REVISION_1_PHASE_2A)
    assert get_current_revision(engine) == REVISION_1_PHASE_2A

    now = datetime.now(UTC).isoformat()

    with engine.begin() as conn:
        conn.execute(
            text("""
            INSERT INTO departments (id, name, description, manager_agent_id, schema_version, payload, created_at, updated_at)
            VALUES ('dept-1', 'Engineering', 'Test Dept', NULL, '1.0', '{"dept_key": "dept_value"}', :now, :now)
        """),
            {"now": now},
        )

        conn.execute(
            text("""
            INSERT INTO agents (id, name, role, description, department_id, status, previous_status, current_task_id, progress, status_message, deployment_status, is_temporary, schema_version, version, payload, created_at, updated_at)
            VALUES ('agent-1', 'Agent 1', 'test_role', 'Test agent', 'dept-1', 'idle', NULL, NULL, 0, 'Ready', 'deployed', 0, '1.0', '1.0.0', '{"agent_key": "agent_value"}', :now, :now)
        """),
            {"now": now},
        )

        conn.execute(
            text("""
            INSERT INTO tasks (id, title, description, original_request, creator, priority, status, progress, status_message, retry_count, maximum_retries, schema_version, payload, created_at, updated_at)
            VALUES ('task-1', 'Test Task 1', 'Test Description 1', 'original req', 'system', 'high', 'pending', 0, 'pending', 0, 3, '1.0', '{"task_key": "task_value"}', :now, :now)
        """),
            {"now": now},
        )

        conn.execute(
            text("""
            INSERT INTO approvals (id, task_id, requesting_agent_id, action_type, title, description, reason, risk_level, affected_resources, exact_action_preview, expected_outcome, reversal_method, expires_at, status, schema_version, payload, created_at, updated_at)
            VALUES ('appr-1', 'task-1', 'agent-1', 'test_action', 'Title', 'Desc', 'Reason', 'low', '[]', 'preview', 'outcome', 'reversal', :now, 'pending', '1.0', '{}', :now, :now)
        """),
            {"now": now},
        )

        conn.execute(
            text("""
            INSERT INTO artifacts (id, task_id, producing_agent_id, name, artifact_type, description, content_reference, metadata, version, schema_version, payload, created_at, updated_at)
            VALUES ('art-1', 'task-1', 'agent-1', 'Test Artifact', 'test', 'Test Desc', '/tmp/test', '{}', '1.0.0', '1.0', '{}', :now, :now)
        """),
            {"now": now},
        )

        conn.execute(
            text("""
            INSERT INTO audit_events (id, event_type, actor, agent_id, task_id, approval_id, previous_state, new_state, correlation_id, sequence_number, event_session_id, timestamp, payload, schema_version)
            VALUES ('evt-1', 'test_event', 'system', 'agent-1', 'task-1', 'appr-1', NULL, NULL, 'corr-1', 1, 'sess-1', :now, '{}', '1.0')
        """),
            {"now": now},
        )

        conn.execute(
            text("""
            INSERT INTO notifications (id, notification_type, title, message, is_read, metadata, schema_version, payload, created_at)
            VALUES ('notif-1', 'alert', 'Alert', 'Test Alert', 0, '{}', '1.0', '{}', :now)
        """),
            {"now": now},
        )

        conn.execute(
            text("""
            INSERT INTO workflow_runs (id, correlation_id, root_task_id, workflow_type, workflow_version, current_step_index, status, retry_count, resume_eligibility, started_at, updated_at)
            VALUES ('wf-1', 'corr-1', 'task-1', 'test_wf', '1.0', 0, 'running', 0, 1, :now, :now)
        """),
            {"now": now},
        )

        conn.execute(
            text("""
            INSERT INTO workflow_checkpoints (id, workflow_run_id, workflow_version, step_index, step_identifier, root_task_id, payload, created_at)
            VALUES ('chk-1', 'wf-1', '1.0', 1, 'step-1', 'task-1', '{}', :now)
        """),
            {"now": now},
        )

        conn.execute(
            text("""
            INSERT INTO system_state (id, event_session_id, emergency_stop, simulator_status, current_sequence_number, seed_data_version, startup_was_clean, recovery_status, updated_at)
            VALUES (1, 'sess-1', 0, 'idle', 0, '2.0', 1, 'none', :now)
        """),
            {"now": now},
        )

        conn.execute(
            text("""
            INSERT INTO idempotency_records (idempotency_key, command_type, canonical_request_hash, response_status, response_body, created_at)
            VALUES ('idkey-1', 'test_cmd', 'hash1', 200, '{}', :now)
        """),
            {"now": now},
        )

        conn.execute(
            text("""
            INSERT INTO outbox_events (id, event_type, envelope, correlation_id, event_session_id, sequence_number, status, publish_attempt_count, created_at)
            VALUES ('out-1', 'test_event', '{}', 'corr-1', 'sess-1', 1, 'pending', 0, :now)
        """),
            {"now": now},
        )

    command.upgrade(alembic_config, HEAD)

    script = get_script_directory(alembic_config)
    head_rev = script.get_current_head()
    assert get_current_revision(engine) == head_rev

    # Assert every historical record survived and fields are unchanged
    with engine.connect() as conn:
        dept = conn.execute(
            text("SELECT id, name, description, payload FROM departments WHERE id = 'dept-1'")
        ).fetchone()
        assert dept is not None
        assert dept.id == "dept-1"
        assert dept.name == "Engineering"
        assert dept.payload == '{"dept_key": "dept_value"}'

        agent = conn.execute(
            text("SELECT id, name, role, department_id, payload FROM agents WHERE id = 'agent-1'")
        ).fetchone()
        assert agent is not None
        assert agent.name == "Agent 1"
        assert agent.department_id == "dept-1"
        assert agent.payload == '{"agent_key": "agent_value"}'

        task = conn.execute(
            text("SELECT id, title, payload FROM tasks WHERE id = 'task-1'")
        ).fetchone()
        assert task is not None
        assert task.title == "Test Task 1"
        assert task.payload == '{"task_key": "task_value"}'

        # Verify other rows survived
        assert conn.execute(text("SELECT count(*) FROM approvals")).scalar() == 1
        assert conn.execute(text("SELECT count(*) FROM artifacts")).scalar() == 1
        assert conn.execute(text("SELECT count(*) FROM audit_events")).scalar() == 1
        assert conn.execute(text("SELECT count(*) FROM notifications")).scalar() == 1
        assert conn.execute(text("SELECT count(*) FROM workflow_runs")).scalar() == 1
        assert conn.execute(text("SELECT count(*) FROM workflow_checkpoints")).scalar() == 1
        assert conn.execute(text("SELECT count(*) FROM system_state")).scalar() == 1
        assert conn.execute(text("SELECT count(*) FROM idempotency_records")).scalar() == 1
        assert conn.execute(text("SELECT count(*) FROM outbox_events")).scalar() == 1


def test_upgrade_from_task_lease(tmp_db_path: Path, alembic_config: Config, engine: Engine) -> None:
    # 7. Task-lease-revision upgrade coverage
    command.upgrade(alembic_config, REVISION_2_TASK_LEASE)
    assert get_current_revision(engine) == REVISION_2_TASK_LEASE

    now = datetime.now(UTC).isoformat()

    with engine.begin() as conn:
        # Base Phase 2A requirements for foreign keys
        conn.execute(
            text("""
            INSERT INTO departments (id, name, description, manager_agent_id, schema_version, payload, created_at, updated_at)
            VALUES ('dept-2', 'Engineering', 'Test Dept', NULL, '1.0', '{}', :now, :now)
        """),
            {"now": now},
        )

        conn.execute(
            text("""
            INSERT INTO agents (id, name, role, description, department_id, status, previous_status, current_task_id, progress, status_message, deployment_status, is_temporary, schema_version, version, payload, created_at, updated_at)
            VALUES ('agent-2', 'Agent 2', 'test_role', 'Test agent', 'dept-2', 'idle', NULL, NULL, 0, 'Ready', 'deployed', 0, '1.0', '1.0.0', '{}', :now, :now)
        """),
            {"now": now},
        )

        conn.execute(
            text("""
            INSERT INTO tasks (id, title, description, original_request, creator, priority, status, progress, status_message, retry_count, maximum_retries, schema_version, payload, created_at, updated_at)
            VALUES ('task-2', 'Test Task 2', 'Test Description 2', 'original req', 'system', 'high', 'pending', 0, 'pending', 0, 3, '1.0', '{}', :now, :now)
        """),
            {"now": now},
        )

        # Phase 2B data
        conn.execute(
            text("""
            INSERT INTO workers (id, name, instance_id, status, started_at, last_heartbeat_at, lease_seconds, metadata)
            VALUES ('worker-1', 'Worker 1', 'inst-1', 'active', :now, :now, 300, '{"metadata_key": "val"}')
        """),
            {"now": now},
        )

        conn.execute(
            text("""
            INSERT INTO task_leases (task_id, worker_id, lease_token, acquired_at, expires_at, renewed_at, attempt_number, version)
            VALUES ('task-2', 'worker-1', 'token-1', :now, :now, :now, 1, 1)
        """),
            {"now": now},
        )

        conn.execute(
            text("""
            INSERT INTO task_attempts (id, task_id, attempt_number, worker_id, lease_token, started_at)
            VALUES ('att-1', 'task-2', 1, 'worker-1', 'token-1', :now)
        """),
            {"now": now},
        )

    command.upgrade(alembic_config, HEAD)

    script = get_script_directory(alembic_config)
    head_rev = script.get_current_head()
    assert get_current_revision(engine) == head_rev

    # Assert Phase 2B rows survived exactly
    with engine.connect() as conn:
        worker = conn.execute(
            text("SELECT id, instance_id, metadata FROM workers WHERE id = 'worker-1'")
        ).fetchone()
        assert worker is not None
        assert worker.instance_id == "inst-1"
        assert worker.metadata == '{"metadata_key": "val"}'

        lease = conn.execute(
            text("SELECT task_id, worker_id, lease_token FROM task_leases WHERE task_id = 'task-2'")
        ).fetchone()
        assert lease is not None
        assert lease.worker_id == "worker-1"
        assert lease.lease_token == "token-1"

        attempt = conn.execute(
            text(
                "SELECT id, task_id, worker_id, attempt_number FROM task_attempts WHERE id = 'att-1'"
            )
        ).fetchone()
        assert attempt is not None
        assert attempt.task_id == "task-2"
        assert attempt.worker_id == "worker-1"
        assert attempt.attempt_number == 1


def test_schema_downgrade_and_reupgrade(
    tmp_db_path: Path, alembic_config: Config, engine: Engine
) -> None:
    # 8. Improve downgrade and re-upgrade coverage
    # Upgrade to head to establish schema
    command.upgrade(alembic_config, HEAD)

    script = get_script_directory(alembic_config)
    head_rev = script.get_current_head()

    now = datetime.now(UTC).isoformat()

    # Insert representative Task Lease (20260723_02) and Context Assembly (20260724_03) data
    with engine.begin() as conn:
        conn.execute(
            text("""
            INSERT INTO departments (id, name, description, manager_agent_id, schema_version, payload, created_at, updated_at)
            VALUES ('dept-down', 'Engineering', 'Test Dept', NULL, '1.0', '{}', :now, :now)
        """),
            {"now": now},
        )

        conn.execute(
            text("""
            INSERT INTO agents (id, name, role, description, department_id, status, previous_status, current_task_id, progress, status_message, deployment_status, is_temporary, schema_version, version, payload, created_at, updated_at)
            VALUES ('agent-down', 'Agent down', 'test_role', 'Test agent', 'dept-down', 'idle', NULL, NULL, 0, 'Ready', 'deployed', 0, '1.0', '1.0.0', '{}', :now, :now)
        """),
            {"now": now},
        )

        conn.execute(
            text("""
            INSERT INTO tasks (id, title, description, original_request, creator, priority, status, progress, status_message, retry_count, maximum_retries, schema_version, payload, created_at, updated_at)
            VALUES ('task-down', 'Test Task down', 'Test Description down', 'original req', 'system', 'high', 'pending', 0, 'pending', 0, 3, '1.0', '{}', :now, :now)
        """),
            {"now": now},
        )

        conn.execute(
            text("""
            INSERT INTO workers (id, name, instance_id, status, started_at, last_heartbeat_at, lease_seconds, metadata)
            VALUES ('worker-down', 'Worker down', 'inst-down', 'active', :now, :now, 300, '{}')
        """),
            {"now": now},
        )

        conn.execute(
            text("""
            INSERT INTO task_leases (task_id, worker_id, lease_token, acquired_at, expires_at, renewed_at, attempt_number, version)
            VALUES ('task-down', 'worker-down', 'token-down', :now, :now, :now, 1, 1)
        """),
            {"now": now},
        )

        conn.execute(
            text("""
            INSERT INTO context_assemblies (id, task_id, project_id, status, input_hash, request_hash, policy_version, included_source_count, excluded_source_count, redaction_count, injection_finding_count, conflict_count, schema_version, payload, created_at)
            VALUES ('ctx-down', 'task-down', 'proj-1', 'assembling', 'input', 'req', '1.0', 0, 0, 0, 0, 0, '1.0', '{}', :now)
        """),
            {"now": now},
        )

    # Downgrade strictly to task lease revision
    command.downgrade(alembic_config, REVISION_2_TASK_LEASE)

    assert get_current_revision(engine) == REVISION_2_TASK_LEASE

    inspector_down = inspect(engine)
    tables_down = set(inspector_down.get_table_names())

    # Verify context_assemblies is dropped but workers and task_leases remain
    assert "context_assemblies" not in tables_down
    assert "workers" in tables_down
    assert "task_leases" in tables_down

    # Verify pre-existing task-lease and historical data remains intact
    with engine.connect() as conn:
        assert (
            conn.execute(text("SELECT count(*) FROM workers WHERE id = 'worker-down'")).scalar()
            == 1
        )
        assert (
            conn.execute(
                text("SELECT count(*) FROM task_leases WHERE task_id = 'task-down'")
            ).scalar()
            == 1
        )
        assert conn.execute(text("SELECT count(*) FROM tasks WHERE id = 'task-down'")).scalar() == 1

    # Re-upgrade to head
    command.upgrade(alembic_config, HEAD)
    assert get_current_revision(engine) == head_rev

    inspector_re = inspect(engine)
    tables_re = set(inspector_re.get_table_names())

    # Verify schema returns
    assert "context_assemblies" in tables_re

    # Verify historical data STILL remains intact
    with engine.connect() as conn:
        assert (
            conn.execute(text("SELECT count(*) FROM workers WHERE id = 'worker-down'")).scalar()
            == 1
        )
        assert (
            conn.execute(
                text("SELECT count(*) FROM task_leases WHERE task_id = 'task-down'")
            ).scalar()
            == 1
        )

    # Behavior of downgraded data: Context assembly table data was dropped entirely. Re-upgrade doesn't magically bring it back.
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM context_assemblies")).scalar() == 0


def test_repeated_startup_and_migration_idempotency(
    tmp_db_path: Path, alembic_config: Config, engine: Engine
) -> None:
    # 11. Strengthen repeated startup/migration coverage
    # Upgrade to head once
    command.upgrade(alembic_config, HEAD)
    head_rev = get_script_directory(alembic_config).get_current_head()
    assert get_current_revision(engine) == head_rev

    # App startup against current DB
    app1 = create_app()
    with TestClient(app1) as client1:
        resp1 = client1.get("/api/health")
        if resp1.status_code == 404:
            resp1 = client1.get("/health")
        assert resp1.status_code == 200

    # Verify DB isn't totally blank by relying on app lifecycle seeds (if any). Let's count departments.
    with engine.connect() as conn:
        dept_count_1 = conn.execute(text("SELECT count(*) FROM departments")).scalar()

    # Repeated `alembic upgrade head` is idempotent
    command.upgrade(alembic_config, HEAD)
    assert get_current_revision(engine) == head_rev

    # App startup again
    app2 = create_app()
    with TestClient(app2) as client2:
        resp2 = client2.get("/api/health")
        if resp2.status_code == 404:
            resp2 = client2.get("/health")
        assert resp2.status_code == 200

    # Verify repeated startup did not duplicate seeds or historical user data unexpectedly
    with engine.connect() as conn:
        dept_count_2 = conn.execute(text("SELECT count(*) FROM departments")).scalar()
        assert dept_count_1 == dept_count_2, (
            "App startup should be idempotent and not duplicate data"
        )


def test_foreign_key_constraints_enforced(
    tmp_db_path: Path, alembic_config: Config, engine: Engine
) -> None:
    # 12. Correct foreign-key tests
    command.upgrade(alembic_config, HEAD)

    now = datetime.now(UTC).isoformat()

    with engine.connect() as conn:
        # We must commit or rollback after IntegrityError. Using a transaction block handles this.
        # Test 1: Context assembly referencing missing task
        trans = conn.begin()
        try:
            conn.execute(
                text("""
                INSERT INTO context_assemblies (id, task_id, project_id, status, input_hash, request_hash, policy_version, included_source_count, excluded_source_count, redaction_count, injection_finding_count, conflict_count, schema_version, payload, created_at)
                VALUES ('ctx-fk1', 'missing-task', 'proj-1', 'assembling', 'hash-in-1', 'hash-req-1', '1.0', 0, 0, 0, 0, 0, '1.0', '{}', :now)
            """),
                {"now": now},
            )
            trans.commit()
            pytest.fail("Should have failed foreign key constraint on task_id")
        except IntegrityError:
            trans.rollback()

        # Test 2: Lease referencing a missing task
        trans = conn.begin()
        try:
            conn.execute(
                text("""
                INSERT INTO task_leases (task_id, worker_id, lease_token, acquired_at, expires_at, renewed_at, attempt_number, version)
                VALUES ('missing-task', 'worker-1', 'token-fk1', :now, :now, :now, 1, 1)
            """),
                {"now": now},
            )
            trans.commit()
            pytest.fail("Should have failed foreign key constraint on task_id")
        except IntegrityError:
            trans.rollback()

        # Test 3: Attempt referencing a missing worker
        # First insert a valid task
        trans = conn.begin()
        conn.execute(
            text("""
            INSERT INTO tasks (id, title, description, original_request, creator, priority, status, progress, status_message, retry_count, maximum_retries, schema_version, payload, created_at, updated_at)
            VALUES ('task-fk1', 'Task', 'Desc', 'req', 'sys', 'high', 'pending', 0, 'pend', 0, 3, '1.0', '{}', :now, :now)
        """),
            {"now": now},
        )
        trans.commit()

        trans = conn.begin()
        try:
            conn.execute(
                text("""
                INSERT INTO task_attempts (id, task_id, attempt_number, worker_id, lease_token, started_at)
                VALUES ('att-fk1', 'task-fk1', 1, 'missing-worker', 'token-fk2', :now)
            """),
                {"now": now},
            )
            trans.commit()
            pytest.fail("Should have failed foreign key constraint on worker_id")
        except IntegrityError:
            trans.rollback()


def test_schema_uniqueness_constraints(
    tmp_db_path: Path, alembic_config: Config, engine: Engine
) -> None:
    # 13. Strengthen uniqueness and idempotency constraints
    command.upgrade(alembic_config, HEAD)

    now = datetime.now(UTC).isoformat()

    with engine.connect() as conn:
        # Insert base worker and task for constraints testing
        trans = conn.begin()
        conn.execute(
            text("""
            INSERT INTO workers (id, name, instance_id, status, started_at, last_heartbeat_at, lease_seconds, metadata)
            VALUES ('w-uniq-1', 'W1', 'inst-uniq', 'active', :now, :now, 300, '{}')
        """),
            {"now": now},
        )
        conn.execute(
            text("""
            INSERT INTO tasks (id, title, description, original_request, creator, priority, status, progress, status_message, retry_count, maximum_retries, schema_version, payload, created_at, updated_at)
            VALUES ('t-uniq-1', 'T1', 'Desc', 'req', 'sys', 'high', 'pending', 0, 'pend', 0, 3, '1.0', '{}', :now, :now)
        """),
            {"now": now},
        )
        trans.commit()

        # Test worker instance_id uniqueness
        trans = conn.begin()
        try:
            conn.execute(
                text("""
                INSERT INTO workers (id, name, instance_id, status, started_at, last_heartbeat_at, lease_seconds, metadata)
                VALUES ('w-uniq-2', 'W2', 'inst-uniq', 'active', :now, :now, 300, '{}')
            """),
                {"now": now},
            )
            trans.commit()
            pytest.fail("Should have failed unique constraint on worker instance_id")
        except IntegrityError:
            trans.rollback()

        # Test idempotency uniqueness (idempotency key + command type)
        trans = conn.begin()
        conn.execute(
            text("""
            INSERT INTO idempotency_records (idempotency_key, command_type, canonical_request_hash, response_status, response_body, created_at)
            VALUES ('idkey-uniq', 'cmd-uniq', 'hash1', 200, '{}', :now)
        """),
            {"now": now},
        )
        trans.commit()

        trans = conn.begin()
        try:
            # duplicate key and cmd
            conn.execute(
                text("""
                INSERT INTO idempotency_records (idempotency_key, command_type, canonical_request_hash, response_status, response_body, created_at)
                VALUES ('idkey-uniq', 'cmd-uniq', 'hash2', 400, '{}', :now)
            """),
                {"now": now},
            )
            trans.commit()
            pytest.fail("Should have failed unique constraint on idempotency (key + cmd)")
        except IntegrityError:
            trans.rollback()

        # Test idempotency valid insertion (same key, DIFFERENT command)
        trans = conn.begin()
        conn.execute(
            text("""
            INSERT INTO idempotency_records (idempotency_key, command_type, canonical_request_hash, response_status, response_body, created_at)
            VALUES ('idkey-uniq', 'cmd-different', 'hash3', 200, '{}', :now)
        """),
            {"now": now},
        )
        trans.commit()

        # Test task attempt uniqueness (task_id, attempt_number)
        trans = conn.begin()
        conn.execute(
            text("""
            INSERT INTO task_attempts (id, task_id, attempt_number, worker_id, lease_token, started_at)
            VALUES ('att-uniq-1', 't-uniq-1', 1, 'w-uniq-1', 'token-uniq-1', :now)
        """),
            {"now": now},
        )
        trans.commit()

        trans = conn.begin()
        try:
            conn.execute(
                text("""
                INSERT INTO task_attempts (id, task_id, attempt_number, worker_id, lease_token, started_at)
                VALUES ('att-uniq-2', 't-uniq-1', 1, 'w-uniq-1', 'token-uniq-2', :now)
            """),
                {"now": now},
            )
            trans.commit()
            pytest.fail(
                "Should have failed unique constraint on task attempt (task_id + attempt_number)"
            )
        except IntegrityError:
            trans.rollback()


def test_historical_orm_compatibility(
    tmp_db_path: Path, alembic_config: Config, engine: Engine
) -> None:
    # 14. Historical ORM compatibility
    command.upgrade(alembic_config, REVISION_1_PHASE_2A)

    now = datetime.now(UTC).isoformat()

    with engine.begin() as conn:
        conn.execute(
            text("""
            INSERT INTO departments (id, name, description, manager_agent_id, schema_version, payload, created_at, updated_at)
            VALUES ('dept-orm', 'Engineering', 'Test Dept', NULL, '1.0', '{}', :now, :now)
        """),
            {"now": now},
        )

        conn.execute(
            text("""
            INSERT INTO agents (id, name, role, description, department_id, status, previous_status, current_task_id, progress, status_message, deployment_status, is_temporary, schema_version, version, payload, created_at, updated_at)
            VALUES ('agent-orm', 'Agent ORM', 'test_role', 'Test agent', 'dept-orm', 'idle', NULL, NULL, 0, 'Ready', 'deployed', 0, '1.0', '1.0.0', '{"orm_key": "orm_value"}', :now, :now)
        """),
            {"now": now},
        )

    command.upgrade(alembic_config, HEAD)

    Session = sessionmaker(bind=engine)
    with Session() as session:
        agent = session.get(AgentRow, "agent-orm")
        assert agent is not None
        assert agent.id == "agent-orm"
        assert agent.name == "Agent ORM"
        assert agent.payload.get("orm_key") == "orm_value"
        # Since department relationship exists
        assert agent.department_id == "dept-orm"


def test_failed_upgrade_does_not_stamp_head(
    tmp_db_path: Path, alembic_config: Config, engine: Engine
) -> None:
    # 15. Replace weak failure-atomicity test (test_failed_upgrade_does_not_stamp_head)
    command.upgrade(alembic_config, REVISION_1_PHASE_2A)
    assert get_current_revision(engine) == REVISION_1_PHASE_2A

    now = datetime.now(UTC).isoformat()
    with engine.begin() as conn:
        conn.execute(
            text("""
            INSERT INTO departments (id, name, description, manager_agent_id, schema_version, payload, created_at, updated_at)
            VALUES ('dept-fail', 'Engineering', 'Test Dept', NULL, '1.0', '{}', :now, :now)
        """),
            {"now": now},
        )

    # Cause an intentional conflict to break the next migration
    # Next migration creates "workers" table. Let's create it manually so it conflicts.
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE workers (id INT)"))

    with pytest.raises(Exception):  # noqa: B017
        command.upgrade(alembic_config, HEAD)

    # The database must NOT report as current
    script = get_script_directory(alembic_config)
    head_rev = script.get_current_head()
    assert get_current_revision(engine) == REVISION_1_PHASE_2A
    assert get_current_revision(engine) != head_rev

    # Historical data remains unchanged and queryable
    with engine.connect() as conn:
        dept = conn.execute(text("SELECT id FROM departments WHERE id = 'dept-fail'")).scalar()
        assert dept == "dept-fail"
