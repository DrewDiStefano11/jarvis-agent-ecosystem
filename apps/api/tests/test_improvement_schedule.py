"""Durable scheduling uses native admission, never executable authority."""

from datetime import UTC, datetime, timedelta

import pytest

from tests.test_improvement_backlog import backlog_app  # noqa: F401


@pytest.fixture
def schedule_app(backlog_app):  # noqa: F811
    from app.models.identity import AssignPermissionRequest, CreatePermissionRequest
    from app.models.improvement_schedule import ImprovementSchedulePolicy
    from app.self_improvement.schedule import ImprovementScheduler
    from tests.test_self_improvement import baseline

    app, actor, backlog, _, _, _ = backlog_app
    identity = app.state.identity_service
    permission = identity.create_definition(
        "permission",
        CreatePermissionRequest(
            stable_key="self_improvement.schedule",
            display_name="Analyze scheduled improvement",
            resource_type="administrative_function",
            action="analyze_improvement",
        ),
    )
    scope = dict(
        permission_id=permission.id,
        resource_type="administrative_function",
        resource_id="improvement_schedule",
    )
    identity.assign_permission(actor.actor_id, AssignPermissionRequest(effect="allow", **scope))
    policy = ImprovementSchedulePolicy(
        enabled=True,
        actor_id=actor.actor_id,
        repo_sha="a" * 40,
        configuration_fingerprint="b" * 64,
        safety_fingerprint="c" * 64,
        auto_admit=True,
    )
    evidence = baseline()
    calls = []

    class Source:
        def collect(self):
            calls.append("collect")
            return evidence.sources[0], evidence.observations

    scheduler = ImprovementScheduler(
        app.state.repository,
        app.state.broker,
        identity,
        policy,
        source_factory=lambda start, end: Source(),
        resources=lambda: True,
    )
    return app, actor, scheduler, policy, calls, scope


@pytest.mark.asyncio
async def test_default_off_does_not_collect_or_write(schedule_app):
    from app.models.improvement_schedule import ImprovementSchedulePolicy

    app, _, scheduler, _, calls, _ = schedule_app
    scheduler.policy = ImprovementSchedulePolicy()
    assert await scheduler.tick(datetime(2026, 10, 27, tzinfo=UTC)) is None
    assert calls == []
    assert scheduler.records() == []


@pytest.mark.asyncio
async def test_one_admission_per_window_and_restart_replays(schedule_app):
    from app.self_improvement.schedule import ImprovementScheduler

    app, _, scheduler, policy, calls, _ = schedule_app
    now = datetime(2026, 10, 27, 12, tzinfo=UTC)
    first = await scheduler.tick(now)
    assert first.outcome == "selected" and first.task_id
    restarted = ImprovementScheduler(
        app.state.repository,
        app.state.broker,
        app.state.identity_service,
        policy,
        source_factory=lambda *args: pytest.fail("recaptured"),
        resources=lambda: True,
    )
    assert await restarted.tick(now + timedelta(seconds=30)) == first
    assert calls == ["collect"]
    assert len(scheduler.backlog.backlog.entries()) == 1
    assert len(scheduler.records()) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", ["resource", "stop", "deny"])
async def test_controls_fail_closed_before_capture(schedule_app, gate):
    from app.core.errors import DomainError
    from app.models.identity import AssignPermissionRequest

    app, actor, scheduler, _, calls, scope = schedule_app
    if gate == "resource":
        scheduler.resources = lambda: False
    elif gate == "stop":
        app.state.repository.emergency_stop = True
        app.state.repository.persist()
    else:
        app.state.identity_service.assign_permission(
            actor.actor_id, AssignPermissionRequest(effect="deny", **scope)
        )
    if gate == "resource":
        assert await scheduler.tick(datetime(2026, 10, 27, tzinfo=UTC)) is None
    else:
        with pytest.raises(DomainError):
            await scheduler.tick(datetime(2026, 10, 27, tzinfo=UTC))
    assert calls == [] and scheduler.records() == []


@pytest.mark.asyncio
async def test_crash_after_capture_recovers_frozen_evidence(schedule_app, monkeypatch):
    app, _, scheduler, _, calls, _ = schedule_app
    now = datetime(2026, 10, 27, 12, tzinfo=UTC)
    analyze = scheduler.analyzer.analyze
    monkeypatch.setattr(
        scheduler.analyzer,
        "analyze",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("process interrupted")),
    )
    with pytest.raises(RuntimeError, match="interrupted"):
        await scheduler.tick(now)
    assert len(scheduler.records()) == 1
    monkeypatch.setattr(scheduler.analyzer, "analyze", analyze)
    scheduler.source_factory = lambda *a: pytest.fail("must use frozen capture")
    result = await scheduler.tick(now + timedelta(hours=2))
    assert result.outcome == "selected"
    assert calls == ["collect"] and len(scheduler.records()) == 2


@pytest.mark.asyncio
async def test_crash_after_admission_recovers_native_task_reference(schedule_app, monkeypatch):
    app, _, scheduler, _, _, _ = schedule_app
    now = datetime(2026, 10, 27, 12, tzinfo=UTC)
    complete = app.state.repository.complete_idempotency

    def interrupt(result, **kwargs):
        if result.command == "improvement.schedule":
            raise RuntimeError("lost cycle acknowledgement")
        return complete(result, **kwargs)

    monkeypatch.setattr(app.state.repository, "complete_idempotency", interrupt)
    with pytest.raises(RuntimeError, match="lost cycle"):
        await scheduler.tick(now)
    entry = scheduler.backlog.backlog.entries()[0]
    monkeypatch.setattr(app.state.repository, "complete_idempotency", complete)
    result = await scheduler.tick(now + timedelta(hours=2))
    assert result.outcome == "selected" and result.task_id == entry.task_id
    assert len(scheduler.backlog.backlog.entries()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["analysis", "admission", "result"])
@pytest.mark.parametrize("control", ["stop", "deny", "disable", "lease"])
async def test_every_write_rechecks_controls(schedule_app, monkeypatch, boundary, control):
    from sqlalchemy import select

    from app.core.errors import DomainError
    from app.db.models import IdempotencyRecordRow
    from app.models.identity import AssignPermissionRequest
    from app.self_improvement.repository import ImprovementRecordRow

    app, actor, scheduler, _, _, scope = schedule_app

    def revoke():
        if control == "stop":
            app.state.repository.emergency_stop = True
            app.state.repository.persist()
        elif control == "deny":
            app.state.identity_service.assign_permission(
                actor.actor_id, AssignPermissionRequest(effect="deny", **scope)
            )
        elif control == "disable":
            scheduler.policy = scheduler.policy.model_copy(update={"enabled": False})
        else:
            with app.state.repository.session_factory() as session, session.begin():
                row = session.scalar(
                    select(IdempotencyRecordRow).where(
                        IdempotencyRecordRow.command_type == "improvement.schedule"
                    )
                )
                row.expiration_at = datetime(2020, 1, 1, tzinfo=UTC)

    if boundary == "analysis":
        original = scheduler.analyzer.repository.save_analysis

        def save(*a, **k):
            revoke()
            return original(*a, **k)

        monkeypatch.setattr(scheduler.analyzer.repository, "save_analysis", save)
    elif boundary == "admission":
        original = app.state.broker.emit

        async def emit(*a, **k):
            revoke()
            return await original(*a, **k)

        monkeypatch.setattr(app.state.broker, "emit", emit)
    else:
        original = app.state.repository.complete_idempotency

        def complete(result, **k):
            if result.command == "improvement.schedule":
                revoke()
            return original(result, **k)

        monkeypatch.setattr(app.state.repository, "complete_idempotency", complete)
    with pytest.raises(DomainError):
        await scheduler.tick(datetime(2026, 10, 27, 12, tzinfo=UTC))
    with app.state.repository.session_factory() as session:
        kinds = [row.kind for row in session.scalars(select(ImprovementRecordRow))]
    assert "schedule_result" not in kinds
    if boundary in {"analysis", "admission"}:
        assert scheduler.backlog.backlog.entries() == []
    if boundary == "analysis":
        # Existing fixture analysis is unrelated; new capture cannot become baseline.
        assert kinds.count("analysis") == 1


@pytest.mark.asyncio
async def test_two_processes_share_native_cycle_claim(schedule_app, monkeypatch):
    import asyncio

    from app.core.errors import DomainError
    from app.self_improvement.schedule import ImprovementScheduler

    app, _, scheduler, policy, _, _ = schedule_app
    entered, release = asyncio.Event(), asyncio.Event()
    emit = app.state.broker.emit

    async def paused(*a, **k):
        entered.set()
        await release.wait()
        return await emit(*a, **k)

    monkeypatch.setattr(app.state.broker, "emit", paused)
    now = datetime(2026, 10, 27, 12, tzinfo=UTC)
    first = asyncio.create_task(scheduler.tick(now))
    await entered.wait()
    second = ImprovementScheduler(
        app.state.repository,
        app.state.broker,
        app.state.identity_service,
        policy,
        source_factory=lambda *a: pytest.fail("must reuse capture"),
        resources=lambda: True,
    )
    try:
        with pytest.raises(DomainError) as caught:
            await second.tick(now)
        assert caught.value.code == "IDEMPOTENCY_REQUEST_IN_PROGRESS"
    finally:
        release.set()
        result = await first
    assert result.outcome == "selected"
    assert len(scheduler.backlog.backlog.entries()) == 1


@pytest.mark.asyncio
async def test_next_window_respects_single_active_improvement_task(schedule_app):
    _, _, scheduler, _, calls, _ = schedule_app
    now = datetime(2026, 10, 27, 12, tzinfo=UTC)
    assert (await scheduler.tick(now)).outcome == "selected"
    assert (await scheduler.tick(now + timedelta(hours=1))).outcome == "blocked"
    assert len(calls) == 2
    assert len(scheduler.backlog.backlog.entries()) == 1


@pytest.mark.parametrize(
    "policy",
    [
        {"enabled": True},
        {
            "enabled": True,
            "actor_id": " operator ",
            "repo_sha": "a" * 40,
            "configuration_fingerprint": "b" * 64,
            "safety_fingerprint": "c" * 64,
        },
        {"interval_seconds": 1},
        {"lookback_seconds": 86401},
        {"auto_execute": True},
    ],
)
def test_policy_is_bounded_and_never_grants_execution(policy):
    from pydantic import ValidationError

    from app.models.improvement_schedule import ImprovementSchedulePolicy

    with pytest.raises(ValidationError):
        ImprovementSchedulePolicy(**policy)


@pytest.mark.asyncio
async def test_analysis_only_does_not_admit(schedule_app):
    _, _, scheduler, _, _, _ = schedule_app
    scheduler.policy = scheduler.policy.model_copy(update={"auto_admit": False})
    result = await scheduler.tick(datetime(2026, 10, 27, 12, tzinfo=UTC))
    assert result.outcome == "analyzed" and result.task_id is None
    assert scheduler.backlog.backlog.entries() == []


def test_api_lifecycle_default_has_no_scheduler_task(schedule_app):
    from fastapi.testclient import TestClient

    app, _, _, _, _, _ = schedule_app
    with TestClient(app):
        assert app.state.improvement_schedule_task is None
    assert not app.state.improvement_scheduler.policy.enabled


@pytest.mark.asyncio
async def test_schedule_status_requires_explicit_read_grant(schedule_app):
    from fastapi.testclient import TestClient

    from app.models.identity import AssignPermissionRequest, CreatePermissionRequest

    app, actor, scheduler, _, _, _ = schedule_app
    app.state.improvement_scheduler = scheduler
    result = await scheduler.tick(datetime(2026, 10, 27, 12, tzinfo=UTC))
    headers = {"X-Jarvis-Actor-Id": actor.actor_id}
    # Avoid opening a second event loop after broker use; HTTP read is synchronous.
    with TestClient(app) as client:
        assert client.get("/api/self-improvement/schedule", headers=headers).status_code == 403
        permission = app.state.identity_service.create_definition(
            "permission",
            CreatePermissionRequest(
                stable_key="self_improvement.schedule_read",
                display_name="Read schedule evidence",
                resource_type="administrative_function",
                action="read_improvement",
            ),
        )
        app.state.identity_service.assign_permission(
            actor.actor_id,
            AssignPermissionRequest(
                permission_id=permission.id,
                effect="allow",
                resource_type="administrative_function",
                resource_id="improvement_schedule",
            ),
        )
        response = client.get("/api/self-improvement/schedule", headers=headers)
        assert response.status_code == 200
        assert response.json()["data"]["result"] == result.model_dump(mode="json")
        assert "observations" not in response.text
        assert "ImprovementScheduleStatus" in app.openapi()["components"]["schemas"]


@pytest.mark.asyncio
async def test_empty_runtime_window_never_invents_work(schedule_app):
    from app.models.self_improvement import SourceProvenance
    from app.self_improvement.engine import digest

    _, _, scheduler, _, _, _ = schedule_app

    class Empty:
        def collect(self):
            return SourceProvenance(
                source_type="runtime_history",
                source_id="durable-runtime",
                digest=digest([]),
                schema_version="1.0",
                repo_sha="unknown",
            ), ()

    scheduler.source_factory = lambda *a: Empty()
    result = await scheduler.tick(datetime(2026, 10, 27, 12, tzinfo=UTC))
    assert result.outcome == "empty"
    assert scheduler.backlog.backlog.entries() == []


def test_settings_validate_bounded_schedule_without_enabling_it(monkeypatch):
    from pydantic import ValidationError

    from app.core.config import Settings

    monkeypatch.delenv("JARVIS_IMPROVEMENT_SCHEDULE_JSON", raising=False)
    assert not Settings(_env_file=None).improvement_schedule_policy.enabled
    with pytest.raises(ValidationError):
        Settings(_env_file=None, JARVIS_IMPROVEMENT_SCHEDULE_JSON='{"enabled":true}')


@pytest.mark.parametrize("crash_boundary", ["capture", "admission"])
def test_real_process_crash_recovers_frozen_cycle_and_native_admission(
    schedule_app, tmp_path, crash_boundary
):
    import json
    import os
    import subprocess
    import sys
    import time
    from pathlib import Path

    from sqlalchemy import select

    from app.db.models import TaskRow

    app, _, scheduler, policy, _, _ = schedule_app
    now = datetime.now(UTC)
    epoch = int(now.timestamp()) // policy.interval_seconds * policy.interval_seconds
    end = datetime.fromtimestamp(epoch, UTC)
    with app.state.repository.session_factory() as session, session.begin():
        task = session.scalar(select(TaskRow).order_by(TaskRow.id).limit(1))
        task.status = "failed"
        task.updated_at = end - timedelta(seconds=60)
    script = tmp_path / "scheduler_process.py"
    script.write_text(
        """
import asyncio, json, os, sys
from datetime import datetime
from app.db.session import create_database_engine, create_session_factory
from app.identity.service import IdentityService
from app.models.improvement_schedule import ImprovementSchedulePolicy
from app.repositories.sqlalchemy import SqlAlchemyRepository
from app.self_improvement.schedule import ImprovementScheduler
from app.services.events import EventBroker
database, policy_json, now, boundary = sys.argv[1:]
engine = create_database_engine(database)
sessions = create_session_factory(engine)
repository = SqlAlchemyRepository(sessions, idempotency_lease_seconds=1)
broker = EventBroker(repository)
scheduler = ImprovementScheduler(repository, broker, IdentityService(sessions),
    ImprovementSchedulePolicy.model_validate_json(policy_json), resources=lambda: True)
if boundary == "capture":
    scheduler.analyzer.analyze = lambda *a, **k: os._exit(73)
elif boundary == "admission":
    async def lose_ack(*a, **k):
        os._exit(74)
    broker._publish = lose_ack
result = asyncio.run(scheduler.tick(datetime.fromisoformat(now)))
print(result.model_dump_json(), flush=True)
engine.dispose()
""",
        encoding="utf-8",
    )
    args = [
        sys.executable,
        str(script),
        app.state.settings.database_url,
        policy.model_dump_json(),
        now.isoformat(),
    ]
    env = {**os.environ, "PYTHONPATH": str(Path.cwd())}
    crashed = subprocess.run(
        [*args, crash_boundary], cwd=Path.cwd(), env=env, capture_output=True, text=True, timeout=30
    )
    assert crashed.returncode == (73 if crash_boundary == "capture" else 74), crashed.stderr
    captures = [record for record in scheduler.records() if "snapshot_digest" in record]
    assert len(captures) == 1
    first_snapshot = captures[0]
    # Evidence changes after the actual process dies; recovery must use its capture.
    with app.state.repository.session_factory() as session, session.begin():
        task = session.scalar(select(TaskRow).order_by(TaskRow.id).limit(1))
        task.status = "completed"
    time.sleep(1.05)  # Expire the dead process's native one-second test claim.
    recovered = subprocess.run(
        [*args, "recover"], cwd=Path.cwd(), env=env, capture_output=True, text=True, timeout=30
    )
    assert recovered.returncode == 0, recovered.stderr
    result = json.loads(recovered.stdout)
    assert result["outcome"] == "selected"
    assert [record for record in scheduler.records() if "snapshot_digest" in record] == captures
    assert first_snapshot["observations"]
    entries = scheduler.backlog.backlog.entries()
    assert len(entries) == 1 and entries[0].task_id == result["task_id"]


def test_enabled_api_lifecycle_starts_and_cancels_only_its_scheduler(schedule_app):
    from fastapi.testclient import TestClient

    app, _, scheduler, policy, _, _ = schedule_app
    scheduler.resources = lambda: False
    app.state.improvement_scheduler = scheduler
    app.state.settings.improvement_schedule_json = policy.model_dump_json()
    with TestClient(app):
        task = app.state.improvement_schedule_task
        assert task is not None and not task.done()
    assert task.done() and task.cancelled()
    assert scheduler.records() == []


@pytest.mark.asyncio
async def test_inflight_cancellation_releases_only_native_uncommitted_claims(
    schedule_app, monkeypatch
):
    import asyncio

    from sqlalchemy import select

    from app.db.models import IdempotencyRecordRow

    app, _, scheduler, _, _, _ = schedule_app
    entered = asyncio.Event()
    emit = app.state.broker.emit

    async def pause(*a, **k):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(app.state.broker, "emit", pause)
    now = datetime(2026, 10, 27, 12, tzinfo=UTC)
    pending = asyncio.create_task(scheduler.tick(now))
    await entered.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert scheduler.backlog.backlog.entries() == []
    with app.state.repository.session_factory() as session:
        assert not session.scalars(
            select(IdempotencyRecordRow).where(IdempotencyRecordRow.response_status == 0)
        ).all()
    monkeypatch.setattr(app.state.broker, "emit", emit)
    assert (await scheduler.tick(now)).outcome == "selected"
