"""Regressions for post-lock processed-command rechecking on non-create commands.

Two identical concurrent non-create commands can both observe no processed
command before either commits. On a database with real row-level locks the
loser blocks on the run lock, then must re-read the processed-command record
before validating the expected version; otherwise it observes the committed
version increment and wrongly returns ``version_conflict``.

SQLite does not reproduce that blocking behavior on its own, so these tests use
the repository's ``_awaiting_run_lock`` seam to force the exact ordering:

1. both callers complete the initial processed-command lookup;
2. caller A acquires the run lock and commits;
3. caller B resumes after the lock;
4. caller B performs the required second processed-command lookup;
5. caller B returns the stored replay result.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command as alembic_command
from alembic.config import Config
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent_runtime.errors import CommandConflictError, VersionConflictError
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
from app.models.agent_runtime import QueueAgentRunCommand
from tests.agent_runtime_testkit import SequenceFactory, create_run, make_spec, ts

RUN_ID = "run-concurrent"


def _database_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def _migrate_head(url: str) -> None:
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    alembic_command.upgrade(config, "head")


def _service(
    repository: SqlAlchemyAgentRuntimeRepository, *, event_prefix: str = "event"
) -> AgentRuntimeService:
    # Each concurrent caller needs its own event-ID namespace; two independent
    # callers never mint the same event ID in production.
    return AgentRuntimeService(
        repository,
        utc_clock=lambda: ts(10_000),
        run_id_factory=SequenceFactory("run"),
        attempt_id_factory=SequenceFactory("attempt"),
        event_id_factory=SequenceFactory(event_prefix),
        checkpoint_id_factory=SequenceFactory("checkpoint"),
    )


def _competing_service(engine) -> AgentRuntimeService:
    """Caller A: an independent repository/session against the same database."""
    return _service(
        SqlAlchemyAgentRuntimeRepository(create_session_factory(engine)),
        event_prefix="event-a",
    )


def _queue_command(command_id: str = "cmd-queue", expected_run_version: int = 1):
    return QueueAgentRunCommand(
        run_id=RUN_ID,
        command_id=command_id,
        expected_run_version=expected_run_version,
        timestamp=ts(1),
        actor_reference="scheduler-1",
        detail="Queued for execution",
        source_metadata={"source": "test"},
    )


@pytest.fixture
def runtime(tmp_path: Path):
    url = _database_url(tmp_path / "runtime-concurrent.db")
    _migrate_head(url)
    engine = create_database_engine(url)
    session_factory = create_session_factory(engine)
    repository = SqlAlchemyAgentRuntimeRepository(session_factory)
    service = _service(repository)
    create_run(service, specification=make_spec(run_id=RUN_ID), command_id="cmd-create")
    return url, engine, session_factory, repository, service


def _counts(engine) -> dict[str, int]:
    with Session(engine) as session:
        return {
            "events": session.scalar(select(func.count()).select_from(AgentRuntimeEventRow)) or 0,
            "processed": session.scalar(
                select(func.count()).select_from(AgentRuntimeProcessedCommandRow)
            )
            or 0,
            "audits": session.scalar(select(func.count()).select_from(AuditEventRow)) or 0,
            "outbox": session.scalar(select(func.count()).select_from(OutboxEventRow)) or 0,
        }


def _run_version(engine) -> int:
    with Session(engine) as session:
        row = session.get(AgentRuntimeRunRow, RUN_ID)
        assert row is not None
        return row.version


class _InterleaveAtRunLock:
    """Runs a competing transaction exactly once, in the run-lock contention window.

    Caller B has already completed its pre-lock processed-command lookup (which
    found nothing) before this fires, so caller A's commit lands strictly
    between B's first and second lookups, and B's lock resolves against
    post-commit state exactly as a blocking ``FOR UPDATE`` would.
    """

    def __init__(self, competitor) -> None:
        self.competitor = competitor
        self.fired = 0
        self.result = None

    def __call__(self, run_id: str, command_id: str) -> None:
        if self.fired:
            return
        self.fired += 1
        self.result = self.competitor()


def _install_interleave(monkeypatch: pytest.MonkeyPatch, competitor) -> _InterleaveAtRunLock:
    seam = _InterleaveAtRunLock(competitor)
    monkeypatch.setattr(
        SqlAlchemyAgentRuntimeRepository,
        "_awaiting_run_lock",
        lambda _self, run_id, command_id: seam(run_id, command_id),
    )
    return seam


def test_concurrent_identical_commands_produce_one_commit_and_one_replay(
    runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    url, engine, session_factory, _repository, service_b = runtime
    service_a = _competing_service(engine)

    seam = _install_interleave(monkeypatch, lambda: service_a.queue_run(_queue_command()))
    result_b = service_b.queue_run(_queue_command())

    assert seam.fired == 1
    result_a = seam.result
    # Caller A committed; caller B observed the record only after the lock.
    assert result_a.idempotent_replay is False
    assert result_b.idempotent_replay is True
    # Both callers receive equivalent stored results.
    assert result_b.run_id == result_a.run_id
    assert result_b.snapshot == result_a.snapshot
    assert [event.event_id for event in result_b.events] == [
        event.event_id for event in result_a.events
    ]


def test_concurrent_identical_commands_do_not_return_version_conflict(
    runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    _url, engine, _sf, _repository, service_b = runtime
    service_a = _competing_service(engine)
    _install_interleave(monkeypatch, lambda: service_a.queue_run(_queue_command()))
    # The pre-fix behavior raised VersionConflictError here.
    result = service_b.queue_run(_queue_command())
    assert result.idempotent_replay is True


def test_concurrent_identical_commands_append_no_duplicate_rows(
    runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    _url, engine, _sf, _repository, service_b = runtime
    service_a = _competing_service(engine)
    before = _counts(engine)
    version_before = _run_version(engine)

    _install_interleave(monkeypatch, lambda: service_a.queue_run(_queue_command()))
    service_b.queue_run(_queue_command())

    after = _counts(engine)
    # Exactly one event, processed command, audit row, and outbox row are added.
    assert after["events"] == before["events"] + 1
    assert after["processed"] == before["processed"] + 1
    assert after["audits"] == before["audits"] + 1
    assert after["outbox"] == before["outbox"] + 1
    # The run version increments exactly once.
    assert _run_version(engine) == version_before + 1

    with Session(engine) as session:
        queued = session.scalars(
            select(AgentRuntimeProcessedCommandRow).where(
                AgentRuntimeProcessedCommandRow.command_id == "cmd-queue"
            )
        ).all()
    assert len(queued) == 1


def test_concurrent_identical_commands_leave_one_attempt_and_checkpoint_projection(
    runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    _url, engine, _sf, _repository, service_b = runtime
    service_a = _competing_service(engine)
    _install_interleave(monkeypatch, lambda: service_a.queue_run(_queue_command()))
    service_b.queue_run(_queue_command())
    repository = SqlAlchemyAgentRuntimeRepository(create_session_factory(engine))
    assert repository.load_attempt_history(RUN_ID) == []
    assert repository.list_checkpoints(RUN_ID) == []
    assert len(repository.list_events(RUN_ID)) == 2


def test_concurrent_changed_command_id_reuse_returns_command_conflict(
    runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    _url, engine, _sf, _repository, service_b = runtime
    service_a = _competing_service(engine)
    _install_interleave(monkeypatch, lambda: service_a.queue_run(_queue_command()))
    before = _counts(engine)

    # Same command ID, different canonical contents, racing the committed command.
    changed = QueueAgentRunCommand(
        run_id=RUN_ID,
        command_id="cmd-queue",
        expected_run_version=1,
        timestamp=ts(1),
        actor_reference="scheduler-1",
        detail="A different queue detail",
        source_metadata={"source": "test"},
    )
    with pytest.raises(CommandConflictError) as excinfo:
        service_b.queue_run(changed)
    assert excinfo.value.code == "command_conflict"
    after = _counts(engine)
    assert after["events"] == before["events"] + 1
    assert after["processed"] == before["processed"] + 1


def test_concurrent_different_command_id_with_stale_version_returns_version_conflict(
    runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    _url, engine, _sf, _repository, service_b = runtime
    service_a = _competing_service(engine)
    _install_interleave(monkeypatch, lambda: service_a.queue_run(_queue_command()))

    # A genuinely different command with a now-stale expected version must still
    # be rejected: post-lock rechecking must not mask real version conflicts.
    with pytest.raises(VersionConflictError) as excinfo:
        service_b.queue_run(_queue_command(command_id="cmd-queue-other", expected_run_version=1))
    assert excinfo.value.code == "version_conflict"


def test_post_lock_replay_survives_repository_restart(
    runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    url, engine, _sf, _repository, service_b = runtime
    service_a = _competing_service(engine)
    _install_interleave(monkeypatch, lambda: service_a.queue_run(_queue_command()))
    first = service_b.queue_run(_queue_command())
    monkeypatch.undo()

    restarted_engine = create_database_engine(url)
    restarted = _service(SqlAlchemyAgentRuntimeRepository(create_session_factory(restarted_engine)))
    replay = restarted.queue_run(_queue_command())
    assert replay.idempotent_replay is True
    assert replay.snapshot == first.snapshot
    assert _counts(restarted_engine)["processed"] == _counts(engine)["processed"]


def test_rolled_back_competitor_does_not_produce_a_phantom_stored_result(
    runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    _url, engine, _sf, _repository, service_b = runtime
    service_a = _competing_service(engine)

    def failing_competitor():
        # Caller A begins the same command but its transaction fails and rolls
        # back, so caller B must not observe a nonexistent stored result.
        original = SqlAlchemyAgentRuntimeRepository._store_audit
        SqlAlchemyAgentRuntimeRepository._store_audit = lambda *_a, **_k: (_ for _ in ()).throw(
            RuntimeError("injected competitor failure")
        )
        try:
            service_a.queue_run(_queue_command())
        except RuntimeError:
            pass
        finally:
            SqlAlchemyAgentRuntimeRepository._store_audit = original

    before = _counts(engine)
    _install_interleave(monkeypatch, failing_competitor)
    result = service_b.queue_run(_queue_command())

    # Caller B proceeds normally and is the only committed writer.
    assert result.idempotent_replay is False
    after = _counts(engine)
    assert after["events"] == before["events"] + 1
    assert after["processed"] == before["processed"] + 1
    assert after["audits"] == before["audits"] + 1
    assert after["outbox"] == before["outbox"] + 1
    assert _run_version(engine) == 2


def test_injected_persistence_failure_leaves_no_partial_rows(
    runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    _url, engine, _sf, _repository, service_b = runtime
    before = _counts(engine)
    version_before = _run_version(engine)
    monkeypatch.setattr(
        SqlAlchemyAgentRuntimeRepository,
        "_store_audit",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("injected persistence failure")),
    )
    with pytest.raises(RuntimeError, match="injected persistence failure"):
        service_b.queue_run(_queue_command())
    assert _counts(engine) == before
    assert _run_version(engine) == version_before


def test_sequential_exact_replay_still_returns_the_stored_result(runtime) -> None:
    _url, engine, _sf, _repository, service = runtime
    first = service.queue_run(_queue_command())
    before = _counts(engine)
    replay = service.queue_run(_queue_command())
    assert first.idempotent_replay is False
    assert replay.idempotent_replay is True
    assert replay.snapshot == first.snapshot
    assert _counts(engine) == before


def test_create_command_precedence_remains_unchanged(runtime) -> None:
    """Post-lock rechecking must not alter duplicate-create precedence."""
    from app.agent_runtime.errors import RunAlreadyExistsError
    from app.models.agent_runtime import CreateAgentRunCommand

    _url, _engine, _sf, _repository, service = runtime
    specification = make_spec(run_id=RUN_ID)

    def create(command_id: str, expected_run_version: int) -> CreateAgentRunCommand:
        return CreateAgentRunCommand(
            specification=specification,
            command_id=command_id,
            expected_run_version=expected_run_version,
            timestamp=specification.created_at,
            actor_reference="operator-1",
            source_metadata={"source": "test"},
        )

    # Exact replay of the original create still returns the stored result.
    assert service.create_run(create("cmd-create", 0)).idempotent_replay is True
    # An existing run outranks expected_run_version, zero or nonzero.
    with pytest.raises(RunAlreadyExistsError):
        service.create_run(create("cmd-create-2", 0))
    with pytest.raises(RunAlreadyExistsError):
        service.create_run(create("cmd-create-3", 5))
