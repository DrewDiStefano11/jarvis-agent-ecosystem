from __future__ import annotations

import json
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from tests.test_persistence import database_url


def migration_config(path: Path) -> Config:
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url(path))
    return config


def insert_runtime_fixture(path: Path, *, correlation_id: str) -> None:
    engine = create_engine(database_url(path))
    now = "2026-01-01T00:00:00+00:00"
    snapshot = {
        "specification": {
            "run_id": "run-migration",
            "task_id": "task-migration",
            "agent_id": "agent-migration",
            "requested_operation": "migrate",
            "created_at": now,
            "deadline": None,
            "parent_run_id": None,
            "correlation_id": "corr-migration",
            "causation_id": None,
            "idempotency_key": "idem-migration",
            "maximum_permitted_attempts": 3,
            "metadata": {},
            "requested_capabilities": [],
            "execution_constraints": None,
        },
        "state": "created",
        "version": 1,
        "event_sequence_number": 1,
        "attempt_count": 0,
        "active_attempt_id": None,
        "latest_checkpoint_id": None,
        "recovery_status": "none",
        "created_at": now,
        "updated_at": now,
        "queued_at": None,
        "claimed_at": None,
        "started_at": None,
        "last_heartbeat_at": None,
        "pause": None,
        "paused_at": None,
        "resumed_at": None,
        "blocking_reason": None,
        "cancellation": None,
        "cancellation_requested_at": None,
        "completed_at": None,
        "failure": None,
        "terminal_outcome": None,
    }
    event = {
        "event_id": "event-migration",
        "event_type": "run_created",
        "event_schema_version": "1.0",
        "run_id": "run-migration",
        "attempt_id": None,
        "sequence_number": 1,
        "run_version": 1,
        "timestamp": now,
        "actor_reference": "actor-migration",
        "command_id": "cmd-migration",
        "correlation_id": "corr-migration",
        "causation_id": None,
        "payload": {"detail": "Run created", "specification": snapshot["specification"]},
        "metadata": {},
    }
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO agent_runtime_runs "
                "(run_id, task_id, agent_id, correlation_id, parent_run_id, state, version, "
                "event_sequence_number, attempt_count, active_attempt_id, latest_checkpoint_id, "
                "recovery_status, created_at, updated_at, deadline, terminal_at, specification_json, snapshot_json) "
                "VALUES (:run_id, :task_id, :agent_id, :correlation_id, NULL, 'created', 1, 1, 0, "
                "NULL, NULL, 'none', :now, :now, NULL, NULL, :specification_json, :snapshot_json)"
            ),
            {
                "run_id": "run-migration",
                "task_id": "task-migration",
                "agent_id": "agent-migration",
                "correlation_id": "corr-migration",
                "now": now,
                "specification_json": json.dumps(snapshot["specification"], sort_keys=True),
                "snapshot_json": json.dumps(snapshot, sort_keys=True),
            },
        )
        connection.execute(
            text(
                "INSERT INTO agent_runtime_events "
                "(event_id, run_id, attempt_id, event_type, schema_version, sequence_number, run_version, "
                "timestamp, actor_reference, command_id, correlation_id, causation_id, payload_json, metadata_json, envelope_json) "
                "VALUES ('event-migration', 'run-migration', NULL, 'run_created', '1.0', 1, 1, :now, "
                "'actor-migration', 'cmd-migration', 'corr-migration', NULL, :payload, '{}', :envelope)"
            ),
            {
                "now": now,
                "payload": json.dumps(event["payload"], sort_keys=True),
                "envelope": json.dumps(event, sort_keys=True),
            },
        )
        connection.execute(
            text(
                "INSERT INTO agent_runtime_processed_commands "
                "(run_id, command_id, command_hash, command_type, verified_actor_id, authorization_json, result_json, processed_at) "
                "VALUES ('run-migration', 'cmd-migration', :hash, 'create', 'actor-migration', '{}', :result, :now)"
            ),
            {
                "hash": "a" * 64,
                "result": json.dumps(
                    {"run_id": "run-migration", "snapshot": snapshot, "events": [event]}
                ),
                "now": now,
            },
        )
        connection.execute(
            text(
                "INSERT INTO audit_events "
                "(id, event_type, actor, agent_id, task_id, approval_id, previous_state, new_state, "
                "correlation_id, sequence_number, event_session_id, timestamp, payload, schema_version) "
                "VALUES ('audit-migration', 'agent_runtime.command', 'actor-migration', NULL, NULL, NULL, "
                "NULL, 'created', :correlation_id, 1, 'runtime-session', :now, :payload, '1.0')"
            ),
            {
                "correlation_id": correlation_id,
                "now": now,
                "payload": json.dumps({"payload": {"runId": "run-migration"}}),
            },
        )
        connection.execute(
            text(
                "INSERT INTO outbox_events "
                "(id, event_type, envelope, correlation_id, event_session_id, sequence_number, status, "
                "created_at, published_at, publish_attempt_count, last_publish_error) "
                "VALUES ('outbox-migration', 'agent_runtime.run_created', :envelope, :correlation_id, "
                "'runtime-session', 99, 'pending', :now, NULL, 0, NULL)"
            ),
            {
                "envelope": json.dumps({"eventId": "outbox-migration"}),
                "correlation_id": correlation_id,
                "now": now,
            },
        )
    engine.dispose()


def test_unsafe_runtime_downgrade_fails_before_destructive_ddl(tmp_path: Path) -> None:
    path = tmp_path / "unsafe-downgrade.db"
    config = migration_config(path)
    command.upgrade(config, "head")
    oversized = "c" * 120
    insert_runtime_fixture(path, correlation_id=oversized)

    with pytest.raises(RuntimeError):
        command.downgrade(config, "a87a487dd714")

    engine = create_engine(database_url(path))
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert "agent_runtime_runs" in tables
    assert "agent_runtime_events" in tables
    with engine.connect() as connection:
        assert connection.scalar(text("select version_num from alembic_version")) == "20260729_04"
        assert connection.scalar(text("select count(*) from agent_runtime_runs")) == 1
        assert connection.scalar(text("select count(*) from agent_runtime_events")) == 1
        assert (
            connection.scalar(
                text("select correlation_id from audit_events where id='audit-migration'")
            )
            == oversized
        )
        assert (
            connection.scalar(
                text("select correlation_id from outbox_events where id='outbox-migration'")
            )
            == oversized
        )
    assert inspector.get_columns("audit_events")[8]["type"].length == 120
    assert inspector.get_columns("outbox_events")[3]["type"].length == 120
    engine.dispose()


def test_safe_runtime_downgrade_and_upgrade_round_trip_preserves_representable_data(
    tmp_path: Path,
) -> None:
    path = tmp_path / "safe-downgrade.db"
    config = migration_config(path)
    command.upgrade(config, "head")
    insert_runtime_fixture(path, correlation_id="c" * 80)

    command.downgrade(config, "a87a487dd714")
    engine = create_engine(database_url(path))
    inspector = inspect(engine)
    assert "agent_runtime_runs" not in set(inspector.get_table_names())
    with engine.connect() as connection:
        assert connection.scalar(text("select version_num from alembic_version")) == "a87a487dd714"
        assert (
            connection.scalar(
                text("select correlation_id from audit_events where id='audit-migration'")
            )
            == "c" * 80
        )
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(database_url(path))
    with engine.connect() as connection:
        assert connection.scalar(text("select version_num from alembic_version")) == "20260905_06"
    assert "agent_runtime_runs" in set(inspect(engine).get_table_names())
    engine.dispose()
