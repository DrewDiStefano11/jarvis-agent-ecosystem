from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from alembic import command as migration
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import event, func, inspect, select, text
from sqlalchemy.exc import OperationalError

from app.core.errors import DomainError
from app.db.models import (
    AgentRuntimeRunRow,
    AuditEventRow,
    IdentityAgentRow,
    OfficeCommandRow,
    OfficePlacementRow,
    OutboxEventRow,
    SystemStateRow,
)
from app.main import create_app
from app.models.agent_runtime import AgentRunState
from app.models.identity import CreateAgentRequest
from app.models.office import OfficeCatalog, OfficeCommand
from app.office.geometry import split_motion
from app.office.repository import OfficeRepository


class Clock:
    def __init__(self):
        self.time = datetime.now(UTC) + timedelta(seconds=1)

    def __call__(self):
        return self.time

    def advance(self, seconds):
        self.time += timedelta(seconds=seconds)


@pytest.fixture
def office(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_AUTONOMOUS_WORKER_ENABLED", "false")
    app = create_app(database_url=f"sqlite:///{(tmp_path / 'office.db').as_posix()}")
    clock = Clock()
    repo = app.state.office_service.repository
    repo.now = clock
    yield app, repo, clock
    app.state.engine.dispose()


def identity(app, suffix="first"):
    service = app.state.identity_service
    row = service.create_agent(
        CreateAgentRequest(
            stable_key=f"office-{suffix}", display_name=f"Office {suffix}", agent_type="worker"
        )
    )
    service.transition(row.id, "active")
    return row.id


def issue(repo, identity_id, action, version, station=None, sprite=None, command_id=None):
    return repo.command(
        identity_id,
        OfficeCommand(
            commandId=command_id or uuid4().hex,
            action=action,
            expectedVersion=version,
            stationId=station,
            spriteId=sprite,
        ),
    )


def assign(app, repo, suffix="first", station=None):
    actor = identity(app, suffix)
    issue(
        repo,
        actor,
        "assign",
        0,
        station or repo.catalog.routes[0].originId,
        repo.catalog.spriteIds[0],
    )
    return actor


def events(repo):
    with repo.sessions() as session:
        audits = list(
            session.scalars(select(AuditEventRow).where(AuditEventRow.event_type.like("office.%")))
        )
        outbox = list(
            session.scalars(
                select(OutboxEventRow).where(OutboxEventRow.event_type.like("office.%"))
            )
        )
        return audits, outbox


def assert_error(code, operation):
    with pytest.raises(DomainError) as caught:
        operation()
    assert caught.value.code == code


def test_assignment_survives_reopen_and_release_keeps_monotonic_version(office):
    app, repo, clock = office
    actor = assign(app, repo)
    reopened = OfficeRepository(repo.sessions, repo.catalog, clock)
    first = reopened.snapshot().placements[0]
    assert first.identityId == actor and first.version == 1
    assert first.spriteId in repo.catalog.spriteIds
    issue(reopened, actor, "release", 1)
    released = reopened.snapshot()
    assert released.placements == []
    assert released.placementVersions == {actor: 2}
    assert_error(
        "OFFICE_VERSION_CONFLICT",
        lambda: issue(repo, actor, "assign", 0, first.stationId, first.spriteId),
    )
    issue(repo, actor, "assign", 2, first.stationId, first.spriteId)
    assert_error(
        "OFFICE_VERSION_CONFLICT",
        lambda: issue(repo, actor, "move", 1, repo.catalog.routes[0].destinationId),
    )
    assert reopened.snapshot().placements[0].version == 3


def test_command_retry_is_once_across_sessions_and_conflict_is_atomic(office):
    app, repo, clock = office
    actor = identity(app)
    request = OfficeCommand(
        commandId="retry-me",
        action="assign",
        expectedVersion=0,
        stationId=repo.catalog.stations[0].id,
        spriteId=repo.catalog.spriteIds[0],
    )
    result = repo.command(actor, request)
    reopened = OfficeRepository(repo.sessions, repo.catalog, clock)
    assert reopened.command(actor, request) == result
    changed = request.model_copy(update={"stationId": repo.catalog.stations[1].id})
    assert_error("OFFICE_COMMAND_CONFLICT", lambda: reopened.command(actor, changed))
    second_actor = identity(app, "second")
    assert_error("OFFICE_COMMAND_CONFLICT", lambda: reopened.command(second_actor, request))
    audits, outbox = events(repo)
    assert len(audits) == len(outbox) == 1
    assert audits[0].sequence_number == outbox[0].sequence_number
    assert audits[0].event_session_id == outbox[0].event_session_id
    assert outbox[0].envelope["payload"]["identityId"] == actor
    with repo.sessions() as session:
        assert session.scalar(select(func.count()).select_from(OfficeCommandRow)) == 1


def test_failure_rolls_back_placement_command_audit_outbox_and_sequence(office, monkeypatch):
    app, repo, _ = office
    actor = identity(app)
    before = app.state.repository.current_event_cursor()
    emit = repo._event

    def fail_after_staging(*args, **kwargs):
        emit(*args, **kwargs)
        raise RuntimeError("simulated commit interruption")

    monkeypatch.setattr(repo, "_event", fail_after_staging)
    with pytest.raises(RuntimeError, match="commit interruption"):
        issue(repo, actor, "assign", 0, repo.catalog.stations[0].id, repo.catalog.spriteIds[0])
    assert repo.snapshot().placements == []
    assert events(repo) == ([], [])
    assert app.state.repository.current_event_cursor() == before
    with repo.sessions() as session:
        assert session.scalar(select(func.count()).select_from(OfficeCommandRow)) == 0


@pytest.mark.parametrize(
    "field,value",
    [("points", [{"x": 100, "y": 100}]), ("routeId", "arbitrary-path"), ("durationMs", 1)],
)
def test_commands_cannot_supply_coordinates_routes_or_speed(field, value):
    with pytest.raises(ValidationError):
        OfficeCommand.model_validate(
            {
                "commandId": "unsafe",
                "action": "move",
                "expectedVersion": 1,
                "stationId": "POSITION_022",
                field: value,
            }
        )


def test_only_registered_stations_routes_and_original_sprites_are_accepted(office):
    app, repo, _ = office
    actor = identity(app)
    assert_error(
        "OFFICE_STATION_UNAVAILABLE",
        lambda: issue(repo, actor, "assign", 0, "../../arbitrary", repo.catalog.spriteIds[0]),
    )
    assert_error(
        "OFFICE_SPRITE_UNAVAILABLE",
        lambda: issue(
            repo, actor, "assign", 0, repo.catalog.stations[0].id, "https://example.test/custom.png"
        ),
    )
    issue(repo, actor, "assign", 0, repo.catalog.routes[0].originId, repo.catalog.spriteIds[0])
    target = next(
        station.id
        for station in repo.catalog.stations
        if (repo.catalog.routes[0].originId, station.id) not in repo.routes
        and station.id != repo.catalog.routes[0].originId
    )
    assert_error("OFFICE_ROUTE_UNAVAILABLE", lambda: issue(repo, actor, "move", 1, target))
    route = repo.catalog.routes[0]
    issue(repo, actor, "move", 1, route.destinationId)
    motion = repo.snapshot().placements[0].motion
    assert motion.points == route.points and motion.doorIds == route.doorIds


@pytest.mark.parametrize("same_command", [False, True])
def test_concurrent_assignment_serializes_reservation_and_idempotency(office, same_command):
    app, repo, clock = office
    first = identity(app)
    second = first if same_command else identity(app, "second")
    peers = [OfficeRepository(repo.sessions, repo.catalog, clock) for _ in range(2)]
    barrier = Barrier(2)

    def compete(index):
        barrier.wait(timeout=5)
        try:
            return issue(
                peers[index],
                [first, second][index],
                "assign",
                0,
                repo.catalog.stations[0].id,
                repo.catalog.spriteIds[0],
                "shared" if same_command else f"racer-{index}",
            )
        except DomainError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(compete, range(2)))
    assert len(repo.snapshot().placements) == 1
    if same_command:
        assert results[0] == results[1]
    else:
        assert results.count("OFFICE_STATION_OCCUPIED") == 1
    assert len(events(repo)[0]) == len(events(repo)[1]) == 1


def test_move_reserves_destination_and_stop_preserves_exact_point_until_continue(office):
    app, repo, clock = office
    route = repo.catalog.routes[0]
    actor = assign(app, repo)
    other = identity(app, "second")
    issue(repo, actor, "move", 1, route.destinationId)
    initial = repo.snapshot().placements[0]
    clock.advance(initial.motion.durationMs / 3000)
    expected = split_motion(initial.motion, clock())[0]
    issue(repo, actor, "stop", 2)
    clock.advance(100)
    stopped = repo.snapshot().placements[0]
    assert stopped.movementState == "stopped" and stopped.position == expected
    assert repo.reconcile() is False
    assert_error(
        "OFFICE_STATION_OCCUPIED",
        lambda: issue(repo, other, "assign", 0, route.destinationId, repo.catalog.spriteIds[0]),
    )
    assert_error("OFFICE_STOPPED_ROUTE", lambda: issue(repo, actor, "move", 3, route.originId))
    issue(repo, actor, "move", 3, route.destinationId)
    continued = repo.snapshot().placements[0]
    assert continued.position == expected
    assert continued.motion.durationMs < initial.motion.durationMs
    clock.advance(continued.motion.durationMs / 1000 + 1)
    reopened = OfficeRepository(repo.sessions, repo.catalog, clock)
    assert reopened.reconcile() is True
    arrived = reopened.snapshot().placements[0]
    assert arrived.stationId == route.destinationId and arrived.position == route.points[-1]
    assert arrived.motion is None and arrived.movementState == "idle" and arrived.version == 5
    assert reopened.reconcile() is False
    assert [row.event_type for row in events(repo)[0]].count("office.arrived") == 1


def crossing_catalog():
    points = {"a": (100, 300), "b": (500, 300), "c": (300, 100), "d": (300, 500)}
    return OfficeCatalog(
        version="isolated-geometry",
        sourceCommit="fixture",
        geometryHash="fixture",
        reviewScope="Synthetic crossing tests only",
        spriteIds=["original"],
        stations=[
            {
                "id": key,
                "label": key,
                "roomId": "test",
                "roomName": "test",
                "point": {"x": x, "y": y},
            }
            for key, (x, y) in points.items()
        ],
        routes=[
            {
                "id": f"{origin}:{destination}",
                "originId": origin,
                "destinationId": destination,
                "points": [
                    {"x": points[key][0], "y": points[key][1]} for key in (origin, destination)
                ],
                "doorIds": [],
                "length": 400,
            }
            for origin, destination in [("a", "b"), ("c", "d")]
        ],
    )


def test_stopped_identity_blocks_a_crossing_and_release_frees_route(office):
    app, original, clock = office
    repo = OfficeRepository(original.sessions, crossing_catalog(), clock)
    first = assign(app, repo, station="a")
    second = assign(app, repo, "second", station="c")
    issue(repo, first, "move", 1, "b")
    assert_error("OFFICE_AISLE_BUSY", lambda: issue(repo, second, "move", 1, "d"))
    with repo.sessions() as session:
        duration = repo._motion(session.get(OfficePlacementRow, first)).durationMs
    clock.advance(duration / 2000)
    issue(repo, first, "stop", 2)
    assert_error("OFFICE_ROUTE_OCCUPIED", lambda: issue(repo, second, "move", 1, "d"))
    issue(repo, first, "release", 3)
    issue(repo, second, "move", 1, "d")
    assert (
        next(row for row in repo.snapshot().placements if row.identityId == second).movementState
        == "moving"
    )


@pytest.mark.parametrize("reason", ["emergency", "disabled", "suspended", "retired"])
def test_lifecycle_and_emergency_freeze_before_reconcile_without_teleport(office, reason):
    app, repo, clock = office
    actor = assign(app, repo)
    issue(repo, actor, "move", 1, repo.catalog.routes[0].destinationId)
    clock.advance(0.5)
    expected = repo.snapshot().placements[0].position
    with repo.sessions.begin() as session:
        if reason == "emergency":
            system = session.get(SystemStateRow, 1)
            system.emergency_stop = True
            system.updated_at = clock()
        else:
            row = session.get(IdentityAgentRow, actor)
            row.updated_at = clock()
            if reason == "disabled":
                row.is_enabled = False
            else:
                row.lifecycle_state = reason
    clock.advance(50)
    before = repo.snapshot().placements[0]
    assert before.position == expected and before.movementState == "stopped"
    assert repo.reconcile() is True
    assert repo.snapshot().placements[0].position == expected
    expected_code = "EMERGENCY_STOP_ACTIVE" if reason == "emergency" else "AGENT_INACTIVE"
    assert_error(
        expected_code, lambda: issue(repo, actor, "move", 3, repo.catalog.routes[0].destinationId)
    )


@pytest.mark.parametrize(
    "state,label",
    [
        (
            state,
            {
                "claimed": "working",
                "starting": "working",
                "running": "working",
                "pause_requested": "working",
                "cancel_requested": "working",
                "cancelling": "working",
                "paused": "waiting",
                "blocked": "waiting",
                "queued": "queued",
                "succeeded": "completed",
                "failed": "failed",
                "timed_out": "failed",
                "abandoned": "failed",
            }.get(state, "idle"),
        )
        for state in AgentRunState
    ],
)
def test_activity_projects_real_run_state_without_reading_private_payloads(office, state, label):
    app, repo, clock = office
    actor = assign(app, repo)
    add_run(repo, actor, state, clock())
    queries = []

    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        queries.append(statement)

    event.listen(app.state.engine, "before_cursor_execute", capture)
    try:
        snapshot = repo.snapshot()
    finally:
        event.remove(app.state.engine, "before_cursor_execute", capture)
    assert snapshot.placements[0].activity == label
    assert "PRIVATE" not in snapshot.model_dump_json()
    run_queries = [sql for sql in queries if "agent_runtime_runs" in sql]
    assert run_queries and all(
        "specification_json" not in sql and "snapshot_json" not in sql for sql in run_queries
    )


def add_run(repo, actor, state, timestamp, run_id=None):
    with repo.sessions.begin() as session:
        session.add(
            AgentRuntimeRunRow(
                run_id=run_id or uuid4().hex,
                task_id="PRIVATE-task",
                agent_id=actor,
                state=state,
                version=1,
                event_sequence_number=1,
                attempt_count=0,
                recovery_status="none",
                created_at=timestamp,
                updated_at=timestamp,
                specification_json='{"PRIVATE":"request"}',
                snapshot_json='{"PRIVATE":"output"}',
            )
        )


def test_activity_prioritizes_active_work_then_latest_history_deterministically(office):
    app, repo, clock = office
    actor = assign(app, repo)
    add_run(repo, actor, "running", clock(), "running-old")
    clock.advance(1)
    add_run(repo, actor, "queued", clock(), "queued-new")
    add_run(repo, actor, "failed", clock(), "failed-new")
    assert repo.snapshot().placements[0].activity == "working"
    with repo.sessions.begin() as session:
        session.get(AgentRuntimeRunRow, "running-old").state = "succeeded"
    assert repo.snapshot().placements[0].activity == "queued"
    with repo.sessions.begin() as session:
        session.get(AgentRuntimeRunRow, "queued-new").state = "succeeded"
    assert repo.snapshot().placements[0].activity == "completed"


def test_external_cursor_refresh_preserves_pending_domain_state(office):
    app, repo, _ = office
    cached = app.state.repository
    pending = {"id": "pending-checkpoint"}
    cached._pending_checkpoint = pending
    task = next(iter(cached.tasks.values()))
    task.title = "uncommitted domain edit"
    assign(app, repo)
    cached.refresh_event_cursor()
    assert cached._pending_checkpoint is pending
    assert cached.tasks[task.id] is task and task.title == "uncommitted domain edit"
    assert cached.sequence == cached.current_event_cursor()[1]


def test_office_and_domain_events_share_an_atomic_cursor(office):
    app, repo, _ = office
    actor = identity(app)
    barrier = Barrier(2)

    def emit_office():
        barrier.wait(timeout=5)
        return issue(
            repo, actor, "assign", 0, repo.catalog.stations[0].id, repo.catalog.spriteIds[0]
        )

    def emit_domain():
        barrier.wait(timeout=5)
        return asyncio.run(app.state.broker.emit("test.concurrent", {"safe": True}))

    before = app.state.repository.current_event_cursor()[1]
    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [pool.submit(emit_office), pool.submit(emit_domain)]
        for task in tasks:
            task.result(timeout=10)
    with repo.sessions() as session:
        sequences = list(
            session.scalars(
                select(OutboxEventRow.sequence_number).order_by(OutboxEventRow.sequence_number)
            )
        )
    assert sequences == [before + 1, before + 2]


def test_api_contract_loopback_and_immediate_inactive_stop(office):
    app, repo, clock = office
    actor = identity(app)
    route = repo.catalog.routes[0]
    with TestClient(app) as client:
        schema = client.get("/openapi.json").json()
        assert "/api/office/identities/{identity_id}/commands" in schema["paths"]
        assert (
            "placementVersions" in schema["components"]["schemas"]["OfficeSnapshot"]["properties"]
        )
        assert schema["components"]["schemas"]["OfficeCommand"]["additionalProperties"] is False
        response = client.post(
            f"/api/office/identities/{actor}/commands",
            json={
                "commandId": "api-assign",
                "action": "assign",
                "expectedVersion": 0,
                "stationId": route.originId,
                "spriteId": repo.catalog.spriteIds[0],
            },
        )
        assert response.status_code == 200
        issue(repo, actor, "move", 1, route.destinationId)
        clock.advance(0.5)
        assert client.post(f"/api/identity/agents/{actor}/suspend").status_code == 200
        frozen = client.get("/api/office").json()["data"]["placements"][0]
        assert frozen["movementState"] == "stopped"
        assert client.post(f"/api/identity/agents/{actor}/activate").status_code == 200
        clock.advance(30)
        assert (
            client.get("/api/office").json()["data"]["placements"][0]["position"]
            == frozen["position"]
        )
    remote_app = create_app(database_url=str(app.state.engine.url))
    with TestClient(remote_app, client=("198.51.100.5", 1234)) as remote:
        assert remote.get("/api/office").status_code == 403
        assert remote.post(f"/api/office/identities/{actor}/commands", json={}).status_code == 403


def test_api_emergency_stop_persists_motion_and_blocks_new_commands(office):
    app, repo, clock = office
    actor = assign(app, repo)
    with TestClient(app) as client:
        issue(repo, actor, "move", 1, repo.catalog.routes[0].destinationId)
        clock.advance(0.5)
        response = client.post("/api/system/emergency-stop")
        assert response.status_code == 200 and response.json()["data"]["emergencyStop"]
        frozen = repo.snapshot().placements[0]
        assert frozen.movementState == "stopped"
        clock.advance(100)
        assert repo.snapshot().placements[0].position == frozen.position
        assert_error(
            "EMERGENCY_STOP_ACTIVE",
            lambda: issue(
                repo, actor, "move", frozen.version, repo.catalog.routes[0].destinationId
            ),
        )
        assert client.post("/api/system/resume").status_code == 200
        assert repo.snapshot().placements[0].movementState == "stopped"


def test_migration_roundtrip_retains_identity_and_other_domain_data(office):
    app, repo, _ = office
    actor = assign(app, repo)
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).parents[1] / "migrations"))
    config.set_main_option("sqlalchemy.url", str(app.state.engine.url))
    migration.downgrade(config, "20260905_06")
    assert "office_placements" not in inspect(app.state.engine).get_table_names()
    with repo.sessions() as session:
        assert session.get(IdentityAgentRow, actor) is not None
    migration.upgrade(config, "head")
    with app.state.engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260905_08"
    assert "office_commands" in inspect(app.state.engine).get_table_names()
    assert repo.snapshot().placements == []


def test_periodic_reconcile_retries_transient_database_error(office, monkeypatch):
    app, repo, _ = office
    original = repo.reconcile
    calls = []

    def transient(**kwargs):
        calls.append(True)
        # Startup succeeds; the first periodic recovery fails, the next succeeds.
        if len(calls) == 2:
            raise OperationalError("test", {}, RuntimeError("database locked"))
        return original(**kwargs)

    monkeypatch.setattr(repo, "reconcile", transient)
    with TestClient(app) as client:
        client.portal.call(asyncio.sleep, 1.2)
        assert len(calls) >= 3
        assert not app.state.office_recovery_task.done()


def test_application_restart_finishes_persisted_route_once(office):
    app, repo, clock = office
    actor = identity(app)
    route = repo.catalog.routes[0]
    with TestClient(app):
        issue(repo, actor, "assign", 0, route.originId, repo.catalog.spriteIds[0])
        issue(repo, actor, "move", 1, route.destinationId)
        clock.advance(0.5)
        halfway = repo.snapshot().placements[0]
        assert halfway.movementState == "moving"
    clock.advance(60)
    restarted = create_app(database_url=str(app.state.engine.url))
    restarted.state.office_service.repository.now = clock
    with TestClient(restarted) as client:
        arrived = client.get("/api/office").json()["data"]["placements"][0]
        assert arrived["stationId"] == route.destinationId
        assert arrived["motion"] is None and arrived["version"] == 3
        assert client.get("/api/health").json()["data"]["schemaCurrent"] is True
    assert [row.event_type for row in events(repo)[0]].count("office.arrived") == 1


def test_restart_freezes_at_durable_emergency_event_not_later_process_marker(office):
    app, repo, clock = office
    clock.time = datetime.now(UTC) - timedelta(seconds=1)
    actor = assign(app, repo)
    route = repo.catalog.routes[0]
    issue(repo, actor, "move", 1, route.destinationId)
    motion = repo.snapshot().placements[0].motion
    # Simulate a crash after committing the emergency event but before office
    # reconciliation. The following process marker is deliberately much later.
    app.state.repository.emergency_stop = True
    asyncio.run(app.state.broker.emit("system.emergency_stop", {"active": True}))
    with repo.sessions.begin() as session:
        instant = session.scalar(
            select(OutboxEventRow.created_at).where(
                OutboxEventRow.event_type == "system.emergency_stop"
            )
        )
        state = session.get(SystemStateRow, 1)
        state.updated_at = instant + timedelta(days=1)
    expected = split_motion(motion, instant)[0]
    assert expected != route.points[0] and expected != route.points[-1]
    clock.advance(100)
    restarted = create_app(database_url=str(app.state.engine.url))
    restarted.state.office_service.repository.now = clock
    with TestClient(restarted) as client:
        stopped = client.get("/api/office").json()["data"]["placements"][0]
        assert stopped["position"] == expected.model_dump()
        assert stopped["movementState"] == "stopped" and stopped["version"] == 3


def test_idle_reconcile_does_not_take_write_lock_or_load_private_runtime(office):
    app, repo, _ = office
    assign(app, repo)
    queries = []

    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        queries.append(statement)

    event.listen(app.state.engine, "before_cursor_execute", capture)
    try:
        assert repo.reconcile() is False
    finally:
        event.remove(app.state.engine, "before_cursor_execute", capture)
    assert len(queries) == 1
    assert "office_placements.motion_json" in queries[0]
    assert "BEGIN IMMEDIATE" not in queries[0]
