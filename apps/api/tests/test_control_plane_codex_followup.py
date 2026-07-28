"""Regression coverage for the exact-head Codex runtime persistence findings."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, inspect, select, text
from sqlalchemy.orm import Session

from app.agent_runtime.errors import (
    CommandConflictError,
    RunAlreadyExistsError,
    VersionConflictError,
)
from app.agent_runtime.service import AgentRuntimeService
from app.agent_runtime.sqlalchemy_repository import SqlAlchemyAgentRuntimeRepository
from app.db.models import (
    AgentRuntimeEventRow,
    AgentRuntimeProcessedCommandRow,
    AgentRuntimeRunRow,
    AuditEventRow,
    OutboxEventRow,
)
from app.db.session import create_database_engine, create_session_factory
from app.main import create_app
from app.models.agent_runtime import CreateAgentRunCommand
from app.models.domain import MAX_CORRELATION_ID_LENGTH, EventEnvelope
from app.repositories.sqlalchemy import SqlAlchemyRepository
from app.services.events import EventBroker
from tests.agent_runtime_testkit import SequenceFactory, make_spec, ts

HEAD = "20260728_08"
PREVIOUS = "20260727_07"


def _database_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def _migration_config(database: Path) -> Config:
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", _database_url(database))
    return config


def _migrate(url: str, revision: str = "head") -> None:
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, revision)


def _runtime_service(session_factory) -> AgentRuntimeService:
    return AgentRuntimeService(
        SqlAlchemyAgentRuntimeRepository(session_factory),
        utc_clock=lambda: ts(10_000),
        run_id_factory=SequenceFactory("run"),
        attempt_id_factory=SequenceFactory("attempt"),
        event_id_factory=SequenceFactory("event"),
        checkpoint_id_factory=SequenceFactory("checkpoint"),
    )


def _create_command(
    *,
    run_id: str,
    command_id: str,
    expected_version: int,
    correlation_id: str = "corr-1",
) -> CreateAgentRunCommand:
    specification = make_spec(run_id=run_id, correlation_id=correlation_id)
    return CreateAgentRunCommand(
        specification=specification,
        command_id=command_id,
        expected_run_version=expected_version,
        timestamp=specification.created_at,
        actor_reference="operator-1",
        source_metadata={"source": "test"},
    )


def _runtime_counts(engine) -> tuple[int, int, int, int, int]:
    with Session(engine) as session:
        return (
            session.scalar(select(func.count()).select_from(AgentRuntimeRunRow)) or 0,
            session.scalar(select(func.count()).select_from(AgentRuntimeEventRow)) or 0,
            session.scalar(select(func.count()).select_from(AgentRuntimeProcessedCommandRow)) or 0,
            session.scalar(
                select(func.count())
                .select_from(AuditEventRow)
                .where(AuditEventRow.event_type == "agent_runtime.command")
            )
            or 0,
            session.scalar(
                select(func.count())
                .select_from(OutboxEventRow)
                .where(OutboxEventRow.event_type.like("agent_runtime.%"))
            )
            or 0,
        )


@pytest.fixture
def runtime_database(tmp_path: Path):
    url = _database_url(tmp_path / "codex-followup.db")
    _migrate(url)
    engine = create_database_engine(url)
    session_factory = create_session_factory(engine)
    return url, engine, session_factory


# Health aggregation


def test_health_healthy_runtime_keeps_top_level_healthy_and_components(tmp_path: Path) -> None:
    url = _database_url(tmp_path / "health-healthy.db")
    app = create_app(delay_ms=1, database_url=url)
    with TestClient(app) as api:
        response = api.get("/api/health")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "healthy"
    assert data["runtimePersistence"]["configured"] is True
    assert data["databaseReachable"] is True
    assert data["schemaCurrent"] is True
    assert "contextAssemblerReady" in data
    assert "activeWorkerCount" in data
    assert "outboxExhaustedCount" in data


def test_health_runtime_query_failure_degrades_without_leaking_or_mutating(
    tmp_path: Path,
) -> None:
    database = tmp_path / "health-runtime-query-failure.db"
    url = _database_url(database)
    app = create_app(delay_ms=1, database_url=url)
    engine = create_database_engine(url)
    with Session(engine) as session:
        before = session.scalar(select(func.count()).select_from(AgentRuntimeRunRow))

    def fail_health() -> dict:
        raise RuntimeError(f"SELECT secret FROM agent_runtime_runs at {database}")

    app.state.agent_runtime_repository.health_status = fail_health
    with TestClient(app) as api:
        response = api.get("/api/health")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "degraded"
    assert data["runtimePersistence"] == {
        "configured": False,
        "nonterminalRunCount": 0,
        "status": "unavailable",
        "reasonCode": "runtime_health_query_failed",
    }
    lowered = response.text.lower()
    assert "select secret" not in lowered
    assert "traceback" not in lowered
    assert str(database).lower() not in lowered
    with Session(engine) as session:
        after = session.scalar(select(func.count()).select_from(AgentRuntimeRunRow))
    assert after == before


def test_health_corrupt_runtime_projection_degrades_with_bounded_reason(tmp_path: Path) -> None:
    url = _database_url(tmp_path / "health-corrupt-runtime.db")
    app = create_app(delay_ms=1, database_url=url)
    app.state.agent_runtime_repository.health_status = lambda: {
        "configured": False,
        "nonterminalRunCount": 0,
        "status": "corrupt",
        "reasonCode": "runtime_projection_corrupt",
    }
    with TestClient(app) as api:
        response = api.get("/api/health")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "degraded"
    assert data["runtimePersistence"]["status"] == "corrupt"
    assert data["runtimePersistence"]["reasonCode"] == "runtime_projection_corrupt"


@pytest.mark.parametrize(
    ("probe", "reason_code"),
    [((True, False), "schema_stale"), ((False, False), "database_unreachable")],
)
def test_health_invalid_database_boundary_skips_runtime_query(
    tmp_path: Path,
    probe: tuple[bool, bool],
    reason_code: str,
) -> None:
    url = _database_url(tmp_path / f"health-{reason_code}.db")
    app = create_app(delay_ms=1, database_url=url)
    app.state.repository.health_probe = lambda _revision: probe

    def forbidden_runtime_query() -> dict:
        raise AssertionError("runtime health must not be queried")

    app.state.agent_runtime_repository.health_status = forbidden_runtime_query
    with TestClient(app) as api:
        response = api.get("/api/health")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "degraded"
    assert data["runtimePersistence"]["reasonCode"] == reason_code


def test_health_missing_runtime_projection_degrades_safely(tmp_path: Path) -> None:
    url = _database_url(tmp_path / "health-missing-runtime.db")
    app = create_app(delay_ms=1, database_url=url)
    with TestClient(app) as api:
        with app.state.engine.begin() as connection:
            connection.execute(text("DROP TABLE agent_runtime_runs"))
        response = api.get("/api/health")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "degraded"
    assert data["runtimePersistence"]["status"] == "unavailable"
    assert data["runtimePersistence"]["reasonCode"] == "runtime_persistence_unavailable"


# Correlation ID contract and durable path


class _RecordingWebSocket:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_json(self, message: dict) -> None:
        self.messages.append(message)


def test_full_length_runtime_correlation_id_is_preserved_everywhere_and_after_restart(
    runtime_database,
) -> None:
    _url, engine, session_factory = runtime_database
    shared_repository = SqlAlchemyRepository(session_factory)
    service = _runtime_service(session_factory)
    correlation_id = "c" * MAX_CORRELATION_ID_LENGTH
    command_body = _create_command(
        run_id="run-long-correlation",
        command_id="command-long-correlation",
        expected_version=0,
        correlation_id=correlation_id,
    )
    result = service.create_run(command_body)
    assert result.snapshot is not None
    assert result.snapshot.specification.correlation_id == correlation_id
    assert result.events[0].correlation_id == correlation_id

    with Session(engine) as session:
        run_row = session.get(AgentRuntimeRunRow, "run-long-correlation")
        event_row = session.scalar(
            select(AgentRuntimeEventRow).where(
                AgentRuntimeEventRow.run_id == "run-long-correlation"
            )
        )
        outbox_row = session.scalar(
            select(OutboxEventRow).where(OutboxEventRow.id == result.events[0].event_id)
        )
        audit_row = session.scalar(
            select(AuditEventRow).where(AuditEventRow.event_type == "agent_runtime.command")
        )
        assert run_row is not None and run_row.correlation_id == correlation_id
        assert event_row is not None and event_row.correlation_id == correlation_id
        assert outbox_row is not None and outbox_row.correlation_id == correlation_id
        outer = EventEnvelope.model_validate(outbox_row.envelope)
        assert outer.correlationId == correlation_id
        assert outer.payload["runtimeEvent"]["correlation_id"] == correlation_id
        assert audit_row is not None and audit_row.correlation_id == correlation_id

    websocket = _RecordingWebSocket()
    broker = EventBroker(shared_repository)
    broker.clients.add(websocket)  # type: ignore[arg-type]
    asyncio.run(broker.dispatch_pending())
    assert len(websocket.messages) == 1
    assert websocket.messages[0]["correlationId"] == correlation_id

    restarted_repository = SqlAlchemyAgentRuntimeRepository(session_factory)
    restarted_service = _runtime_service(session_factory)
    assert (
        restarted_repository.load_run("run-long-correlation").specification.correlation_id
        == correlation_id
    )
    assert (
        restarted_repository.list_events("run-long-correlation")[0].correlation_id == correlation_id
    )
    replay = restarted_service.create_run(command_body)
    assert replay.idempotent_replay is True
    assert replay.snapshot is not None
    assert replay.snapshot.specification.correlation_id == correlation_id
    assert _runtime_counts(engine) == (1, 1, 1, 1, 1)


def test_correlation_contract_accepts_120_and_rejects_121_characters() -> None:
    accepted = "a" * MAX_CORRELATION_ID_LENGTH
    assert make_spec(correlation_id=accepted).correlation_id == accepted
    envelope = EventEnvelope(
        eventId="event-boundary",
        eventType="agent_runtime.run_created",
        timestamp=ts(0),
        sequenceNumber=1,
        correlationId=accepted,
        source="agent_runtime",
        payload={},
    )
    assert envelope.correlationId == accepted
    with pytest.raises(ValidationError):
        make_spec(correlation_id="a" * (MAX_CORRELATION_ID_LENGTH + 1))
    with pytest.raises(ValidationError):
        envelope.model_copy(
            update={"correlationId": "a" * (MAX_CORRELATION_ID_LENGTH + 1)}
        ).__class__.model_validate(
            {
                **envelope.model_dump(mode="json"),
                "correlationId": "a" * (MAX_CORRELATION_ID_LENGTH + 1),
            }
        )


def test_correlation_migration_blank_head_matches_orm_metadata(tmp_path: Path) -> None:
    database = tmp_path / "correlation-blank.db"
    config = _migration_config(database)
    command.upgrade(config, "head")
    engine = create_database_engine(_database_url(database))
    inspector = inspect(engine)
    lengths = {
        table_name: next(
            column["type"].length
            for column in inspector.get_columns(table_name)
            if column["name"] == "correlation_id"
        )
        for table_name in ("outbox_events", "audit_events")
    }
    assert lengths == {"outbox_events": 120, "audit_events": 120}
    assert OutboxEventRow.__table__.c.correlation_id.type.length == 120
    assert AuditEventRow.__table__.c.correlation_id.type.length == 120
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == HEAD
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == [HEAD]


def _insert_shared_event_rows(engine, correlation_id: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """INSERT INTO outbox_events (
                id, event_type, envelope, correlation_id, event_session_id,
                sequence_number, status, created_at, published_at,
                publish_attempt_count, last_publish_error
                ) VALUES (
                'outbox-correlation', 'test.correlation', '{}', :correlation_id,
                'session-correlation', 1, 'pending', '2026-07-28 00:00:00',
                NULL, 0, NULL
                )"""
            ),
            {"correlation_id": correlation_id},
        )
        connection.execute(
            text(
                """INSERT INTO audit_events (
                id, event_type, actor, agent_id, task_id, approval_id,
                previous_state, new_state, correlation_id, sequence_number,
                event_session_id, timestamp, payload, schema_version
                ) VALUES (
                'audit-correlation', 'test.correlation', 'test', NULL, NULL,
                NULL, NULL, NULL, :correlation_id, 1, 'session-correlation',
                '2026-07-28 00:00:00', '{}', '1.0'
                )"""
            ),
            {"correlation_id": correlation_id},
        )


def test_correlation_migration_upgrade_from_v07_preserves_populated_rows(tmp_path: Path) -> None:
    database = tmp_path / "correlation-upgrade.db"
    config = _migration_config(database)
    command.upgrade(config, PREVIOUS)
    engine = create_database_engine(_database_url(database))
    correlation_id = "p" * 80
    _insert_shared_event_rows(engine, correlation_id)
    command.upgrade(config, HEAD)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT correlation_id FROM outbox_events")) == correlation_id
        assert connection.scalar(text("SELECT correlation_id FROM audit_events")) == correlation_id
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == HEAD


def test_correlation_migration_representable_downgrade_and_reupgrade(tmp_path: Path) -> None:
    database = tmp_path / "correlation-roundtrip.db"
    config = _migration_config(database)
    command.upgrade(config, HEAD)
    engine = create_database_engine(_database_url(database))
    correlation_id = "r" * 80
    _insert_shared_event_rows(engine, correlation_id)
    command.downgrade(config, PREVIOUS)
    assert {
        table_name: next(
            column["type"].length
            for column in inspect(engine).get_columns(table_name)
            if column["name"] == "correlation_id"
        )
        for table_name in ("outbox_events", "audit_events")
    } == {"outbox_events": 80, "audit_events": 80}
    command.upgrade(config, HEAD)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT correlation_id FROM outbox_events")) == correlation_id
        assert connection.scalar(text("SELECT correlation_id FROM audit_events")) == correlation_id
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == HEAD


@pytest.mark.parametrize("oversized_table", ["outbox_events", "audit_events"])
def test_correlation_migration_unrepresentable_downgrade_preserves_data_and_revision(
    tmp_path: Path,
    oversized_table: str,
) -> None:
    database = tmp_path / f"correlation-failed-{oversized_table}.db"
    config = _migration_config(database)
    command.upgrade(config, HEAD)
    engine = create_database_engine(_database_url(database))
    _insert_shared_event_rows(engine, "s" * 80)
    with engine.begin() as connection:
        connection.execute(
            text(f"UPDATE {oversized_table} SET correlation_id = :correlation_id"),  # noqa: S608
            {"correlation_id": "s" * MAX_CORRELATION_ID_LENGTH},
        )
    with pytest.raises(RuntimeError, match=f"cannot downgrade {oversized_table}"):
        command.downgrade(config, PREVIOUS)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == HEAD
        assert (
            connection.scalar(text(f"SELECT correlation_id FROM {oversized_table}"))  # noqa: S608
            == "s" * MAX_CORRELATION_ID_LENGTH
        )
        temporary_tables = connection.scalars(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '_alembic_tmp_%'")
        ).all()
        assert temporary_tables == []


# Create-command error precedence and concurrency


def test_create_exact_replay_and_changed_reuse_preserve_idempotency_precedence(
    runtime_database,
) -> None:
    _url, engine, session_factory = runtime_database
    service = _runtime_service(session_factory)
    exact = _create_command(
        run_id="run-idempotency", command_id="command-create", expected_version=0
    )
    first = service.create_run(exact)
    replay = service.create_run(exact)
    assert replay.idempotent_replay is True
    assert replay.snapshot == first.snapshot
    changed = exact.model_copy(update={"expected_run_version": 9})
    with pytest.raises(CommandConflictError) as error:
        service.create_run(changed)
    assert error.value.code == "command_conflict"
    assert _runtime_counts(engine) == (1, 1, 1, 1, 1)


@pytest.mark.parametrize("expected_version", [0, 9])
def test_existing_run_with_new_command_id_returns_run_already_exists_before_version(
    runtime_database,
    expected_version: int,
) -> None:
    _url, engine, session_factory = runtime_database
    service = _runtime_service(session_factory)
    service.create_run(
        _create_command(run_id="run-duplicate", command_id="command-first", expected_version=0)
    )
    before = _runtime_counts(engine)
    with pytest.raises(RunAlreadyExistsError) as error:
        service.create_run(
            _create_command(
                run_id="run-duplicate",
                command_id=f"command-new-{expected_version}",
                expected_version=expected_version,
            )
        )
    assert error.value.code == "run_already_exists"
    assert _runtime_counts(engine) == before


def test_new_run_with_nonzero_expected_version_returns_version_conflict_without_rows(
    runtime_database,
) -> None:
    _url, engine, session_factory = runtime_database
    service = _runtime_service(session_factory)
    with pytest.raises(VersionConflictError) as error:
        service.create_run(
            _create_command(run_id="run-new-version", command_id="command-new", expected_version=3)
        )
    assert error.value.code == "version_conflict"
    assert _runtime_counts(engine) == (0, 0, 0, 0, 0)


def test_duplicate_create_precedence_survives_repository_restart(runtime_database) -> None:
    _url, engine, session_factory = runtime_database
    first_service = _runtime_service(session_factory)
    first_service.create_run(
        _create_command(run_id="run-restart", command_id="command-first", expected_version=0)
    )
    restarted_service = _runtime_service(session_factory)
    with pytest.raises(RunAlreadyExistsError) as error:
        restarted_service.create_run(
            _create_command(run_id="run-restart", command_id="command-after", expected_version=7)
        )
    assert error.value.code == "run_already_exists"
    assert _runtime_counts(engine) == (1, 1, 1, 1, 1)


def test_concurrent_duplicate_create_has_one_success_and_one_stable_duplicate(
    runtime_database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _url, engine, session_factory = runtime_database
    repository = SqlAlchemyAgentRuntimeRepository(session_factory)
    service = AgentRuntimeService(
        repository,
        utc_clock=lambda: ts(10_000),
        run_id_factory=SequenceFactory("run"),
        attempt_id_factory=SequenceFactory("attempt"),
        event_id_factory=SequenceFactory("event"),
        checkpoint_id_factory=SequenceFactory("checkpoint"),
    )
    barrier = Barrier(2)
    original_commit = repository.commit_command

    def synchronized_commit(*args, **kwargs):
        if kwargs.get("create"):
            barrier.wait()
        return original_commit(*args, **kwargs)

    monkeypatch.setattr(repository, "commit_command", synchronized_commit)

    def submit(command_id: str) -> object:
        try:
            return service.create_run(
                _create_command(
                    run_id="run-concurrent",
                    command_id=command_id,
                    expected_version=0,
                )
            )
        except Exception as exc:  # noqa: BLE001
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(submit, ("command-a", "command-b")))
    assert sum(hasattr(outcome, "snapshot") for outcome in outcomes) == 1
    duplicates = [outcome for outcome in outcomes if isinstance(outcome, RunAlreadyExistsError)]
    assert len(duplicates) == 1
    assert duplicates[0].code == "run_already_exists"
    assert _runtime_counts(engine) == (1, 1, 1, 1, 1)


def test_duplicate_create_api_maps_run_already_exists_before_version(tmp_path: Path) -> None:
    url = _database_url(tmp_path / "duplicate-create-api.db")
    app = create_app(delay_ms=1, database_url=url)
    first = _create_command(
        run_id="run-api-duplicate", command_id="command-first", expected_version=0
    )
    duplicate = _create_command(
        run_id="run-api-duplicate", command_id="command-second", expected_version=4
    )
    with TestClient(app) as api:
        first_response = api.post("/api/agent-runtime/commands", json=first.model_dump(mode="json"))
        duplicate_response = api.post(
            "/api/agent-runtime/commands", json=duplicate.model_dump(mode="json")
        )
    assert first_response.status_code == 200
    assert duplicate_response.status_code == 409
    assert duplicate_response.json()["error"]["code"] == "run_already_exists"
