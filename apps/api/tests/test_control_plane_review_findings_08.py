"""Regressions for the runtime health, correlation, and create-precedence findings.

These cover three review findings on the runtime control plane:

* runtime persistence must degrade top-level `/api/health`;
* correlation IDs are preserved exactly through every layer, up to the shared
  120-character maximum;
* duplicate-create error precedence puts `run_already_exists` ahead of
  `version_conflict`.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, inspect, select, text
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
from app.models.agent_runtime import (
    MAX_CORRELATION_ID_LENGTH,
    AgentRunSnapshot,
    CreateAgentRunCommand,
    RuntimeEventEnvelope,
)
from app.models.domain import EventEnvelope
from app.repositories.sqlalchemy import SqlAlchemyRepository
from app.services.events import EventBroker
from tests.agent_runtime_testkit import SequenceFactory, create_run, make_spec, ts

ALEMBIC_HEAD = "20260728_08"
ALEMBIC_PREVIOUS = "20260727_07"
EXACT_CORRELATION_ID = "c" * MAX_CORRELATION_ID_LENGTH


def _database_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def _migration_config(url: str) -> Config:
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def _migrate_head(url: str) -> None:
    command.upgrade(_migration_config(url), "head")


def _runtime_service(session_factory) -> AgentRuntimeService:
    return AgentRuntimeService(
        SqlAlchemyAgentRuntimeRepository(session_factory),
        utc_clock=lambda: ts(10_000),
        run_id_factory=SequenceFactory("run"),
        attempt_id_factory=SequenceFactory("attempt"),
        event_id_factory=SequenceFactory("event"),
        checkpoint_id_factory=SequenceFactory("checkpoint"),
    )


@pytest.fixture
def runtime_database(tmp_path: Path):
    url = _database_url(tmp_path / "runtime-findings-08.db")
    _migrate_head(url)
    engine = create_database_engine(url)
    return url, engine, create_session_factory(engine)


def _row_counts(engine) -> dict[str, int]:
    with Session(engine) as session:
        return {
            "runs": session.scalar(select(func.count()).select_from(AgentRuntimeRunRow)) or 0,
            "events": session.scalar(select(func.count()).select_from(AgentRuntimeEventRow)) or 0,
            "processed": session.scalar(
                select(func.count()).select_from(AgentRuntimeProcessedCommandRow)
            )
            or 0,
            "audits": session.scalar(select(func.count()).select_from(AuditEventRow)) or 0,
            "outbox": session.scalar(select(func.count()).select_from(OutboxEventRow)) or 0,
        }


# ──────────────────────────────────────────────
# Finding 1: runtime persistence degrades top-level health
# ──────────────────────────────────────────────


class _RecordingRuntimeHealth:
    """Stub runtime repository health that records whether it was invoked."""

    def __init__(self, result: object = None, error: BaseException | None = None) -> None:
        self.result = result
        self.error = error
        self.calls = 0

    def __call__(self) -> object:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result


def test_health_healthy_runtime_persistence_keeps_top_level_healthy(tmp_path: Path) -> None:
    url = _database_url(tmp_path / "health-healthy.db")
    _migrate_head(url)
    app = create_app(delay_ms=1, database_url=url)
    with TestClient(app) as api:
        data = api.get("/api/health").json()["data"]
    assert data["status"] == "healthy"
    assert data["runtimePersistence"]["configured"] is True
    assert data["runtimePersistence"]["status"] == "healthy"


def test_health_unavailable_runtime_persistence_degrades_top_level(tmp_path: Path) -> None:
    url = _database_url(tmp_path / "health-unavailable.db")
    _migrate_head(url)
    app = create_app(delay_ms=1, database_url=url)
    app.state.agent_runtime_repository.health_status = _RecordingRuntimeHealth(  # type: ignore[method-assign]
        {"configured": False, "nonterminalRunCount": 0}
    )
    with TestClient(app) as api:
        data = api.get("/api/health").json()["data"]
    assert data["status"] == "degraded"
    assert data["runtimePersistence"]["status"] == "unavailable"
    assert data["runtimePersistence"]["reasonCode"] == "runtime_persistence_unavailable"


def test_health_corrupt_runtime_persistence_degrades_top_level(runtime_database) -> None:
    url, engine, session_factory = runtime_database
    create_run(
        _runtime_service(session_factory),
        specification=make_spec(run_id="run-corrupt-health"),
        command_id="command-corrupt-health",
    )
    # Corrupt the durable projection so the runtime component reports an
    # integrity failure rather than a healthy control plane.
    with engine.begin() as connection:
        connection.execute(text("UPDATE agent_runtime_runs SET state = 'corrupt_unknown_state'"))
    app = create_app(delay_ms=1, database_url=url)
    with TestClient(app) as api:
        data = api.get("/api/health").json()["data"]
    assert data["status"] == "degraded"
    assert data["runtimePersistence"]["status"] != "healthy"
    assert data["runtimePersistence"]["reasonCode"] == "runtime_projection_corrupt"


def test_health_runtime_query_exception_produces_bounded_degraded_response(tmp_path: Path) -> None:
    url = _database_url(tmp_path / "health-query-failed.db")
    _migrate_head(url)
    app = create_app(delay_ms=1, database_url=url)
    app.state.agent_runtime_repository.health_status = _RecordingRuntimeHealth(  # type: ignore[method-assign]
        error=RuntimeError("SELECT * FROM agent_runtime_runs failed at /var/data/runtime.db")
    )
    with TestClient(app) as api:
        response = api.get("/api/health")
    data = response.json()["data"]
    assert response.status_code == 200
    assert data["status"] == "degraded"
    assert data["runtimePersistence"]["reasonCode"] == "runtime_health_query_failed"
    assert data["runtimePersistence"]["status"] == "unavailable"
    assert set(data["runtimePersistence"]) == {
        "configured",
        "nonterminalRunCount",
        "status",
        "reasonCode",
    }


def test_health_malformed_runtime_result_produces_bounded_degraded_response(
    tmp_path: Path,
) -> None:
    url = _database_url(tmp_path / "health-malformed.db")
    _migrate_head(url)
    for malformed in (
        "not-a-dict",
        {"nonterminalRunCount": 0},
        {"configured": "yes", "nonterminalRunCount": 0},
        {"configured": True, "nonterminalRunCount": -1},
        {"configured": True, "nonterminalRunCount": "many"},
        {"configured": True, "nonterminalRunCount": 0, "status": 7},
    ):
        app = create_app(delay_ms=1, database_url=url)
        app.state.agent_runtime_repository.health_status = _RecordingRuntimeHealth(malformed)  # type: ignore[method-assign]
        with TestClient(app) as api:
            data = api.get("/api/health").json()["data"]
        assert data["status"] == "degraded", malformed
        assert data["runtimePersistence"]["reasonCode"] == "runtime_health_invalid_response"


def test_health_exhausted_runtime_outbox_degrades_top_level(tmp_path: Path) -> None:
    url = _database_url(tmp_path / "health-exhausted.db")
    _migrate_head(url)
    app = create_app(delay_ms=1, database_url=url)
    app.state.agent_runtime_repository.health_status = _RecordingRuntimeHealth(  # type: ignore[method-assign]
        {"configured": True, "nonterminalRunCount": 1, "outboxExhaustedCount": 2}
    )
    with TestClient(app) as api:
        data = api.get("/api/health").json()["data"]
    assert data["status"] == "degraded"
    assert data["runtimePersistence"]["reasonCode"] == "runtime_outbox_exhausted"


def test_health_stale_schema_does_not_invoke_runtime_health_and_degrades(tmp_path: Path) -> None:
    url = _database_url(tmp_path / "health-stale-08.db")
    _migrate_head(url)
    app = create_app(delay_ms=1, database_url=url)
    probe = _RecordingRuntimeHealth({"configured": True, "nonterminalRunCount": 0})
    app.state.agent_runtime_repository.health_status = probe  # type: ignore[method-assign]
    with TestClient(app) as api:
        app.state.repository.health_probe = lambda _revision: (True, False)
        data = api.get("/api/health").json()["data"]
    assert probe.calls == 0
    assert data["status"] == "degraded"
    assert data["schemaCurrent"] is False
    assert data["runtimePersistence"]["reasonCode"] == "schema_stale"


def test_health_unreachable_database_does_not_invoke_runtime_health(tmp_path: Path) -> None:
    url = _database_url(tmp_path / "health-unreachable-08.db")
    _migrate_head(url)
    app = create_app(delay_ms=1, database_url=url)
    probe = _RecordingRuntimeHealth({"configured": True, "nonterminalRunCount": 0})
    app.state.agent_runtime_repository.health_status = probe  # type: ignore[method-assign]
    app.state.repository.health_probe = lambda _revision: (False, False)
    with TestClient(app) as api:
        data = api.get("/api/health").json()["data"]
    assert probe.calls == 0
    assert data["status"] == "degraded"
    assert data["databaseReachable"] is False
    assert data["runtimePersistence"]["reasonCode"] == "database_unreachable"


def test_health_never_exposes_sql_paths_exceptions_or_tracebacks(tmp_path: Path) -> None:
    url = _database_url(tmp_path / "health-bounded-08.db")
    _migrate_head(url)
    app = create_app(delay_ms=1, database_url=url)
    app.state.agent_runtime_repository.health_status = _RecordingRuntimeHealth(  # type: ignore[method-assign]
        error=RuntimeError("SELECT 1 FROM agent_runtime_runs -- /tmp/secret.db\nTraceback here")
    )
    with TestClient(app) as api:
        body = api.get("/api/health").text
    lowered = body.lower()
    for leak in ("select ", "traceback", "/tmp/", "sqlite", ".db", "secret"):
        assert leak not in lowered


def test_health_unrelated_components_remain_present_when_runtime_degrades(tmp_path: Path) -> None:
    url = _database_url(tmp_path / "health-components-08.db")
    _migrate_head(url)
    app = create_app(delay_ms=1, database_url=url)
    app.state.agent_runtime_repository.health_status = _RecordingRuntimeHealth(  # type: ignore[method-assign]
        error=RuntimeError("boom")
    )
    with TestClient(app) as api:
        data = api.get("/api/health").json()["data"]
    for component in (
        "status",
        "service",
        "processAlive",
        "databaseReachable",
        "schemaCurrent",
        "outboxDispatcherRunning",
        "outboxExhaustedCount",
        "recoveryRequired",
        "contextAssemblerReady",
        "contextAssemblyCount",
        "activeWorkerCount",
        "activeLeaseCount",
        "expiredLeaseCount",
        "staleWorkerCount",
        "runtimePersistence",
        "simulated",
    ):
        assert component in data


def test_health_endpoint_does_not_mutate_runtime_data(runtime_database) -> None:
    url, engine, session_factory = runtime_database
    create_run(
        _runtime_service(session_factory),
        specification=make_spec(run_id="run-health-no-mutate"),
        command_id="command-health-no-mutate",
    )
    app = create_app(delay_ms=1, database_url=url)
    with TestClient(app) as api:
        before = _row_counts(engine)
        for _ in range(5):
            assert api.get("/api/health").status_code == 200
        assert _row_counts(engine) == before
    assert _row_counts(engine) == before


# ──────────────────────────────────────────────
# Finding 2: correlation IDs preserved exactly (shared 120 maximum)
# ──────────────────────────────────────────────


def test_correlation_id_of_exactly_120_characters_is_accepted() -> None:
    specification = make_spec(run_id="run-corr-120", correlation_id=EXACT_CORRELATION_ID)
    assert specification.correlation_id == EXACT_CORRELATION_ID
    assert len(specification.correlation_id) == 120


def test_correlation_id_of_121_characters_is_rejected_not_truncated() -> None:
    with pytest.raises(ValueError) as excinfo:
        make_spec(run_id="run-corr-121", correlation_id="c" * 121)
    assert "correlation_id" in str(excinfo.value)


def test_shared_event_envelope_rejects_oversized_correlation_id() -> None:
    with pytest.raises(ValueError):
        EventEnvelope(
            eventId="event-1",
            eventType="agent_runtime.run_created",
            timestamp=ts(0),
            sequenceNumber=1,
            correlationId="c" * 121,
            payload={},
        )


def test_correlation_id_is_exact_across_every_runtime_layer(runtime_database) -> None:
    url, engine, session_factory = runtime_database
    service = _runtime_service(session_factory)
    specification = make_spec(run_id="run-corr-exact", correlation_id=EXACT_CORRELATION_ID)
    result = create_run(service, specification=specification, command_id="command-corr-exact")

    # Authoritative runtime event.
    assert result.events[0].correlation_id == EXACT_CORRELATION_ID

    with Session(engine) as session:
        run_row = session.get(AgentRuntimeRunRow, "run-corr-exact")
        assert run_row is not None
        assert run_row.correlation_id == EXACT_CORRELATION_ID
        stored_snapshot = AgentRunSnapshot.model_validate_json(run_row.snapshot_json)
        assert stored_snapshot.specification.correlation_id == EXACT_CORRELATION_ID

        event_row = session.get(AgentRuntimeEventRow, result.events[0].event_id)
        assert event_row is not None
        assert event_row.correlation_id == EXACT_CORRELATION_ID
        assert (
            RuntimeEventEnvelope.model_validate_json(event_row.envelope_json).correlation_id
            == EXACT_CORRELATION_ID
        )

        outbox_row = session.get(OutboxEventRow, result.events[0].event_id)
        assert outbox_row is not None
        assert outbox_row.correlation_id == EXACT_CORRELATION_ID
        outer = EventEnvelope.model_validate(outbox_row.envelope)
        assert outer.correlationId == EXACT_CORRELATION_ID
        assert outer.payload["runtimeEvent"]["correlation_id"] == EXACT_CORRELATION_ID

        audit_row = session.scalar(
            select(AuditEventRow).where(AuditEventRow.event_type == "agent_runtime.command")
        )
        assert audit_row is not None
        assert audit_row.correlation_id == EXACT_CORRELATION_ID

    # WebSocket publication through the shared dispatcher.
    class _RecordingWebSocket:
        def __init__(self) -> None:
            self.messages: list[dict] = []

        async def send_json(self, message: dict) -> None:
            self.messages.append(message)

    websocket = _RecordingWebSocket()
    broker = EventBroker(SqlAlchemyRepository(session_factory))
    broker.clients.add(websocket)  # type: ignore[arg-type]
    asyncio.run(broker.dispatch_pending())
    assert len(websocket.messages) == 1
    assert websocket.messages[0]["correlationId"] == EXACT_CORRELATION_ID

    # Audit API serialization.
    app = create_app(delay_ms=1, database_url=url)
    with TestClient(app) as api:
        audit_rows = [
            row
            for row in api.get("/api/audit-events").json()["data"]
            if row["eventType"] == "agent_runtime.command"
        ]
    assert len(audit_rows) == 1
    assert audit_rows[0]["correlationId"] == EXACT_CORRELATION_ID


def test_correlation_id_remains_exact_after_repository_restart(runtime_database) -> None:
    _url, engine, session_factory = runtime_database
    specification = make_spec(run_id="run-corr-restart", correlation_id=EXACT_CORRELATION_ID)
    create_run(
        _runtime_service(session_factory),
        specification=specification,
        command_id="command-corr-restart",
    )
    restarted = SqlAlchemyAgentRuntimeRepository(create_session_factory(engine))
    reloaded = restarted.load_run("run-corr-restart")
    assert reloaded is not None
    assert reloaded.specification.correlation_id == EXACT_CORRELATION_ID
    assert restarted.list_events("run-corr-restart")[0].correlation_id == EXACT_CORRELATION_ID


def test_exact_replay_with_long_correlation_id_creates_no_duplicate_rows(runtime_database) -> None:
    _url, engine, session_factory = runtime_database
    service = _runtime_service(session_factory)
    specification = make_spec(run_id="run-corr-replay", correlation_id=EXACT_CORRELATION_ID)
    first = create_run(service, specification=specification, command_id="command-corr-replay")
    before = _row_counts(engine)
    replay = create_run(service, specification=specification, command_id="command-corr-replay")
    assert replay.idempotent_replay is True
    assert replay.snapshot == first.snapshot
    assert _row_counts(engine) == before


def test_orm_metadata_and_migrated_schema_agree_on_correlation_limit(runtime_database) -> None:
    _url, engine, _session_factory = runtime_database
    assert OutboxEventRow.__table__.c.correlation_id.type.length == MAX_CORRELATION_ID_LENGTH
    assert AuditEventRow.__table__.c.correlation_id.type.length == MAX_CORRELATION_ID_LENGTH
    inspector = inspect(engine)
    for table in ("outbox_events", "audit_events"):
        column = next(
            item for item in inspector.get_columns(table) if item["name"] == "correlation_id"
        )
        assert column["type"].length == MAX_CORRELATION_ID_LENGTH
        assert column["nullable"] is False


# ──────────────────────────────────────────────
# Finding 2: migration 20260728_08
# ──────────────────────────────────────────────


def _insert_audit_and_outbox(engine, *, correlation_id: str, suffix: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO audit_events (id, event_type, actor, correlation_id, "
                "sequence_number, event_session_id, timestamp, payload, schema_version) "
                "VALUES (:id, 'test.event', 'system', :correlation_id, 1, 'session-1', "
                "'2026-07-28 00:00:00', '{}', '1.0')"
            ),
            {"id": f"audit-{suffix}", "correlation_id": correlation_id},
        )
        connection.execute(
            text(
                "INSERT INTO outbox_events (id, event_type, envelope, correlation_id, "
                "event_session_id, sequence_number, status, created_at, publish_attempt_count) "
                "VALUES (:id, 'test.event', '{}', :correlation_id, :session, 1, 'pending', "
                "'2026-07-28 00:00:00', 0)"
            ),
            {
                "id": f"outbox-{suffix}",
                "correlation_id": correlation_id,
                "session": f"session-{suffix}",
            },
        )


def _correlation_values(engine) -> tuple[str, str]:
    with engine.connect() as connection:
        return (
            connection.scalar(text("SELECT correlation_id FROM audit_events")),
            connection.scalar(text("SELECT correlation_id FROM outbox_events")),
        )


def test_correlation_migration_blank_upgrade_to_head(tmp_path: Path) -> None:
    url = _database_url(tmp_path / "corr-blank.db")
    command.upgrade(_migration_config(url), "head")
    with create_engine(url).connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == ALEMBIC_HEAD


def test_correlation_migration_exactly_one_head(tmp_path: Path) -> None:
    config = _migration_config(_database_url(tmp_path / "corr-heads.db"))
    heads = ScriptDirectory.from_config(config).get_heads()
    assert heads == [ALEMBIC_HEAD]
    revision = ScriptDirectory.from_config(config).get_revision(ALEMBIC_HEAD)
    assert revision.down_revision == ALEMBIC_PREVIOUS


def test_correlation_migration_upgrade_from_previous_preserves_rows(tmp_path: Path) -> None:
    url = _database_url(tmp_path / "corr-upgrade.db")
    config = _migration_config(url)
    command.upgrade(config, ALEMBIC_PREVIOUS)
    engine = create_engine(url)
    _insert_audit_and_outbox(engine, correlation_id="legacy-correlation", suffix="legacy")
    command.upgrade(config, ALEMBIC_HEAD)
    assert _correlation_values(engine) == ("legacy-correlation", "legacy-correlation")
    inspector = inspect(engine)
    for table in ("outbox_events", "audit_events"):
        column = next(
            item for item in inspector.get_columns(table) if item["name"] == "correlation_id"
        )
        assert column["type"].length == MAX_CORRELATION_ID_LENGTH
    assert "ix_audit_events_correlation_id" in {
        index["name"] for index in inspector.get_indexes("audit_events")
    }
    assert any(
        set(constraint["column_names"]) == {"event_session_id", "sequence_number"}
        for constraint in inspector.get_unique_constraints("outbox_events")
    )
    assert {fk["referred_table"] for fk in inspector.get_foreign_keys("audit_events")} == {
        "agents",
        "tasks",
        "approvals",
    }


def test_correlation_migration_representable_downgrade_and_reupgrade(tmp_path: Path) -> None:
    url = _database_url(tmp_path / "corr-roundtrip.db")
    config = _migration_config(url)
    command.upgrade(config, ALEMBIC_HEAD)
    engine = create_engine(url)
    _insert_audit_and_outbox(engine, correlation_id="short-correlation", suffix="short")
    command.downgrade(config, ALEMBIC_PREVIOUS)
    with engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version")) == ALEMBIC_PREVIOUS
        )
    assert _correlation_values(engine) == ("short-correlation", "short-correlation")
    inspector = inspect(engine)
    column = next(
        item for item in inspector.get_columns("audit_events") if item["name"] == "correlation_id"
    )
    assert column["type"].length == 80
    command.upgrade(config, ALEMBIC_HEAD)
    assert _correlation_values(engine) == ("short-correlation", "short-correlation")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == ALEMBIC_HEAD


def test_correlation_migration_unrepresentable_downgrade_fails_before_ddl(tmp_path: Path) -> None:
    url = _database_url(tmp_path / "corr-guarded.db")
    config = _migration_config(url)
    command.upgrade(config, ALEMBIC_HEAD)
    engine = create_engine(url)
    _insert_audit_and_outbox(engine, correlation_id=EXACT_CORRELATION_ID, suffix="long")
    with pytest.raises(RuntimeError, match="cannot downgrade"):
        command.downgrade(config, ALEMBIC_PREVIOUS)
    assert _correlation_values(engine) == (EXACT_CORRELATION_ID, EXACT_CORRELATION_ID)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == ALEMBIC_HEAD
        leftovers = connection.execute(
            text(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND (name LIKE '%_new%' OR name LIKE '%_old%' OR name LIKE '_alembic_tmp%')"
            )
        ).all()
    assert leftovers == []
    inspector = inspect(engine)
    column = next(
        item for item in inspector.get_columns("audit_events") if item["name"] == "correlation_id"
    )
    assert column["type"].length == MAX_CORRELATION_ID_LENGTH


# ──────────────────────────────────────────────
# Finding 3: duplicate-create error precedence
# ──────────────────────────────────────────────


def _create_command(
    specification, *, command_id: str, expected_run_version: int = 0
) -> CreateAgentRunCommand:
    return CreateAgentRunCommand(
        specification=specification,
        command_id=command_id,
        expected_run_version=expected_run_version,
        timestamp=specification.created_at,
        actor_reference="operator-1",
        source_metadata={"source": "test"},
    )


def test_create_precedence_exact_replay_returns_stored_result(runtime_database) -> None:
    _url, engine, session_factory = runtime_database
    service = _runtime_service(session_factory)
    specification = make_spec(run_id="run-prec-replay")
    first = service.create_run(_create_command(specification, command_id="command-prec-replay"))
    before = _row_counts(engine)
    replay = service.create_run(_create_command(specification, command_id="command-prec-replay"))
    assert replay.idempotent_replay is True
    assert replay.snapshot == first.snapshot
    assert _row_counts(engine) == before


def test_create_precedence_changed_replay_with_nonzero_version_is_command_conflict(
    runtime_database,
) -> None:
    _url, engine, session_factory = runtime_database
    service = _runtime_service(session_factory)
    specification = make_spec(run_id="run-prec-changed")
    service.create_run(_create_command(specification, command_id="command-prec-changed"))
    before = _row_counts(engine)
    with pytest.raises(CommandConflictError) as excinfo:
        service.create_run(
            _create_command(
                specification, command_id="command-prec-changed", expected_run_version=3
            )
        )
    assert excinfo.value.code == "command_conflict"
    assert _row_counts(engine) == before


def test_create_precedence_changed_specification_is_command_conflict(runtime_database) -> None:
    _url, engine, session_factory = runtime_database
    service = _runtime_service(session_factory)
    service.create_run(
        _create_command(make_spec(run_id="run-prec-spec"), command_id="command-prec-spec")
    )
    before = _row_counts(engine)
    with pytest.raises(CommandConflictError):
        service.create_run(
            _create_command(
                make_spec(run_id="run-prec-spec", task_id="task-changed"),
                command_id="command-prec-spec",
            )
        )
    assert _row_counts(engine) == before


def test_create_precedence_existing_run_new_command_zero_version_is_run_already_exists(
    runtime_database,
) -> None:
    _url, engine, session_factory = runtime_database
    service = _runtime_service(session_factory)
    specification = make_spec(run_id="run-prec-dup-zero")
    service.create_run(_create_command(specification, command_id="command-prec-first"))
    before = _row_counts(engine)
    with pytest.raises(RunAlreadyExistsError) as excinfo:
        service.create_run(_create_command(specification, command_id="command-prec-second"))
    assert excinfo.value.code == "run_already_exists"
    assert _row_counts(engine) == before


def test_create_precedence_existing_run_new_command_nonzero_version_is_run_already_exists(
    runtime_database,
) -> None:
    _url, engine, session_factory = runtime_database
    service = _runtime_service(session_factory)
    specification = make_spec(run_id="run-prec-dup-nonzero")
    service.create_run(_create_command(specification, command_id="command-prec-first"))
    before = _row_counts(engine)
    with pytest.raises(RunAlreadyExistsError) as excinfo:
        service.create_run(
            _create_command(specification, command_id="command-prec-second", expected_run_version=7)
        )
    assert excinfo.value.code == "run_already_exists"
    assert _row_counts(engine) == before


def test_create_precedence_new_run_nonzero_version_is_version_conflict(runtime_database) -> None:
    _url, engine, session_factory = runtime_database
    service = _runtime_service(session_factory)
    before = _row_counts(engine)
    with pytest.raises(VersionConflictError) as excinfo:
        service.create_run(
            _create_command(
                make_spec(run_id="run-prec-new-vc"),
                command_id="command-prec-vc",
                expected_run_version=4,
            )
        )
    assert excinfo.value.code == "version_conflict"
    assert _row_counts(engine) == before


def test_create_precedence_brand_new_create_succeeds(runtime_database) -> None:
    _url, engine, session_factory = runtime_database
    service = _runtime_service(session_factory)
    result = service.create_run(
        _create_command(make_spec(run_id="run-prec-new"), command_id="command-prec-new")
    )
    assert result.idempotent_replay is False
    assert result.snapshot.version == 1
    counts = _row_counts(engine)
    assert counts["runs"] == 1
    assert counts["events"] == 1
    assert counts["processed"] == 1


def test_create_precedence_concurrent_duplicate_creation_commits_once(runtime_database) -> None:
    _url, engine, session_factory = runtime_database
    specification = make_spec(run_id="run-prec-concurrent")
    first_service = _runtime_service(session_factory)
    second_service = _runtime_service(session_factory)
    # Both services observe an empty database before either commits, which is
    # the durable race the create transaction must resolve.
    assert first_service.repository.load_run("run-prec-concurrent") is None
    assert second_service.repository.load_run("run-prec-concurrent") is None
    first_service.create_run(_create_command(specification, command_id="command-concurrent-a"))
    with pytest.raises((RunAlreadyExistsError, CommandConflictError, VersionConflictError)) as info:
        second_service.create_run(_create_command(specification, command_id="command-concurrent-b"))
    assert info.value.code in {"run_already_exists", "command_conflict", "version_conflict"}
    counts = _row_counts(engine)
    assert counts["runs"] == 1
    assert counts["events"] == 1
    assert counts["processed"] == 1


def test_create_precedence_survives_restart(runtime_database) -> None:
    _url, engine, session_factory = runtime_database
    specification = make_spec(run_id="run-prec-restart")
    _runtime_service(session_factory).create_run(
        _create_command(specification, command_id="command-prec-restart")
    )
    restarted = _runtime_service(create_session_factory(engine))
    replay = restarted.create_run(_create_command(specification, command_id="command-prec-restart"))
    assert replay.idempotent_replay is True
    with pytest.raises(RunAlreadyExistsError):
        restarted.create_run(
            _create_command(
                specification, command_id="command-prec-restart-2", expected_run_version=9
            )
        )
    with pytest.raises(CommandConflictError):
        restarted.create_run(
            _create_command(
                make_spec(run_id="run-prec-restart", task_id="task-other"),
                command_id="command-prec-restart",
            )
        )


def test_create_precedence_injected_persistence_failure_rolls_back_completely(
    runtime_database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _url, engine, session_factory = runtime_database
    service = _runtime_service(session_factory)
    before = _row_counts(engine)

    def _explode(self, session, command, snapshot, events):  # noqa: ANN001
        raise RuntimeError("injected audit persistence failure")

    monkeypatch.setattr(SqlAlchemyAgentRuntimeRepository, "_store_audit", _explode)
    with pytest.raises(RuntimeError, match="injected audit persistence failure"):
        service.create_run(
            _create_command(make_spec(run_id="run-prec-rollback"), command_id="command-rollback")
        )
    assert _row_counts(engine) == before
