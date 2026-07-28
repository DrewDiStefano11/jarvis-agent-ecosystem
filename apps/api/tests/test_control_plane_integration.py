"""Integration tests for agent runtime control-plane durability boundaries.

Covers:
  - Cross-run checkpoint identity isolation
  - Parent-run flush ordering
  - Create-command idempotency ordering
  - Application health boundary
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.agent_runtime.errors import CommandConflictError, VersionConflictError
from app.agent_runtime.service import AgentRuntimeService
from app.agent_runtime.sqlalchemy_repository import SqlAlchemyAgentRuntimeRepository
from app.db.models import (
    AgentRuntimeCheckpointRow,
    AgentRuntimeProcessedCommandRow,
    AgentRuntimeRunRow,
)
from app.db.session import create_database_engine
from app.models.agent_runtime import (
    CreateAgentRunCommand,
)
from tests.agent_runtime_testkit import (
    SequenceFactory,
    create_run,
    make_spec,
    ts,
)


def database_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def _run_sql_repository(url: str) -> SqlAlchemyAgentRuntimeRepository:
    engine = create_database_engine(url)
    return SqlAlchemyAgentRuntimeRepository(sessionmaker(engine))


def _run_service(url: str) -> AgentRuntimeService:
    repo = _run_sql_repository(url)
    return AgentRuntimeService(
        repo,
        utc_clock=lambda: ts(10_000),
        run_id_factory=SequenceFactory("run"),
        attempt_id_factory=SequenceFactory("attempt"),
        event_id_factory=SequenceFactory("event"),
        checkpoint_id_factory=SequenceFactory("checkpoint"),
    )


def _migrate_head(url: str) -> None:
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")


# ──────────────────────────────────────────────
# Step 3: Cross-run checkpoint repository tests
# ──────────────────────────────────────────────


@pytest.fixture
def cross_run_db(tmp_path: Path) -> str:
    url = database_url(tmp_path / "cross-run-checkpoints.db")
    _migrate_head(url)
    return url


def _count_checkpoints(url: str) -> int:
    engine = create_database_engine(url)
    with Session(engine) as s:
        return s.scalar(select(func.count()).select_from(AgentRuntimeCheckpointRow)) or 0


def _get_checkpoints_by_run(url: str, run_id: str) -> list[AgentRuntimeCheckpointRow]:
    engine = create_database_engine(url)
    with Session(engine) as s:
        return list(
            s.scalars(
                select(AgentRuntimeCheckpointRow).where(AgentRuntimeCheckpointRow.run_id == run_id)
            ).all()
        )


def test_cross_run_both_checkpoints_persist(cross_run_db: str) -> None:
    """Both checkpoints persist across two separate runs that share checkpoint-1."""
    svc = _run_service(cross_run_db)
    create_run(svc, specification=make_spec(run_id="run-a"), command_id="cmd-a-1")
    create_run(svc, specification=make_spec(run_id="run-b"), command_id="cmd-b-1")
    engine = create_database_engine(cross_run_db)
    with Session(engine) as s:
        s.add(
            AgentRuntimeCheckpointRow(
                checkpoint_id="checkpoint-1",
                run_id="run-a",
                attempt_id="att-a",
                checkpoint_sequence=1,
                contract_json="{}",
            )
        )
        s.add(
            AgentRuntimeCheckpointRow(
                checkpoint_id="checkpoint-1",
                run_id="run-b",
                attempt_id="att-b",
                checkpoint_sequence=1,
                contract_json="{}",
            )
        )
        s.commit()
    assert _count_checkpoints(cross_run_db) == 2


def test_cross_run_two_rows_exist(cross_run_db: str) -> None:
    """Two rows exist."""
    svc = _run_service(cross_run_db)
    create_run(svc, specification=make_spec(run_id="run-a"), command_id="cmd-a-1")
    create_run(svc, specification=make_spec(run_id="run-b"), command_id="cmd-b-1")
    engine = create_database_engine(cross_run_db)
    with Session(engine) as s:
        s.add(
            AgentRuntimeCheckpointRow(
                checkpoint_id="cp-1",
                run_id="run-a",
                attempt_id="att-a",
                checkpoint_sequence=1,
                contract_json="{}",
            )
        )
        s.add(
            AgentRuntimeCheckpointRow(
                checkpoint_id="cp-1",
                run_id="run-b",
                attempt_id="att-b",
                checkpoint_sequence=1,
                contract_json="{}",
            )
        )
        s.commit()
    assert _count_checkpoints(cross_run_db) == 2


def test_cross_run_each_row_correct_run(cross_run_db: str) -> None:
    """Each row is associated with the correct run."""
    svc = _run_service(cross_run_db)
    create_run(svc, specification=make_spec(run_id="run-a"), command_id="cmd-a-1")
    create_run(svc, specification=make_spec(run_id="run-b"), command_id="cmd-b-1")
    engine = create_database_engine(cross_run_db)
    with Session(engine) as s:
        s.add(
            AgentRuntimeCheckpointRow(
                checkpoint_id="cp-1",
                run_id="run-a",
                attempt_id="att-a",
                checkpoint_sequence=1,
                contract_json="{}",
            )
        )
        s.add(
            AgentRuntimeCheckpointRow(
                checkpoint_id="cp-1",
                run_id="run-b",
                attempt_id="att-b",
                checkpoint_sequence=1,
                contract_json="{}",
            )
        )
        s.commit()
    cps_a = _get_checkpoints_by_run(cross_run_db, "run-a")
    cps_b = _get_checkpoints_by_run(cross_run_db, "run-b")
    assert len(cps_a) == 1
    assert len(cps_b) == 1
    assert cps_a[0].run_id == "run-a"
    assert cps_b[0].run_id == "run-b"


def test_cross_run_restart_preserves_both_rows(cross_run_db: str) -> None:
    """Restarting and opening a second repository preserves both rows."""
    svc = _run_service(cross_run_db)
    create_run(svc, specification=make_spec(run_id="run-a"), command_id="cmd-a-1")
    create_run(svc, specification=make_spec(run_id="run-b"), command_id="cmd-b-1")
    engine = create_database_engine(cross_run_db)
    with Session(engine) as s:
        s.add(
            AgentRuntimeCheckpointRow(
                checkpoint_id="cp-1",
                run_id="run-a",
                attempt_id="att-a",
                checkpoint_sequence=1,
                contract_json="{}",
            )
        )
        s.add(
            AgentRuntimeCheckpointRow(
                checkpoint_id="cp-1",
                run_id="run-b",
                attempt_id="att-b",
                checkpoint_sequence=1,
                contract_json="{}",
            )
        )
        s.commit()
    assert _count_checkpoints(cross_run_db) == 2


def test_cross_run_a_loads_only_a_checkpoint(cross_run_db: str) -> None:
    """Run A loads only Run A's checkpoint."""
    svc = _run_service(cross_run_db)
    create_run(svc, specification=make_spec(run_id="run-a"), command_id="cmd-a-1")
    create_run(svc, specification=make_spec(run_id="run-b"), command_id="cmd-b-1")
    engine = create_database_engine(cross_run_db)
    with Session(engine) as s:
        s.add(
            AgentRuntimeCheckpointRow(
                checkpoint_id="cp-1",
                run_id="run-a",
                attempt_id="att-a",
                checkpoint_sequence=1,
                contract_json="{}",
            )
        )
        s.add(
            AgentRuntimeCheckpointRow(
                checkpoint_id="cp-1",
                run_id="run-b",
                attempt_id="att-b",
                checkpoint_sequence=1,
                contract_json="{}",
            )
        )
        s.commit()
    cps = _get_checkpoints_by_run(cross_run_db, "run-a")
    assert len(cps) == 1
    assert cps[0].run_id == "run-a"


def test_cross_run_b_loads_only_b_checkpoint(cross_run_db: str) -> None:
    """Run B loads only Run B's checkpoint."""
    svc = _run_service(cross_run_db)
    create_run(svc, specification=make_spec(run_id="run-a"), command_id="cmd-a-1")
    create_run(svc, specification=make_spec(run_id="run-b"), command_id="cmd-b-1")
    engine = create_database_engine(cross_run_db)
    with Session(engine) as s:
        s.add(
            AgentRuntimeCheckpointRow(
                checkpoint_id="cp-1",
                run_id="run-a",
                attempt_id="att-a",
                checkpoint_sequence=1,
                contract_json="{}",
            )
        )
        s.add(
            AgentRuntimeCheckpointRow(
                checkpoint_id="cp-1",
                run_id="run-b",
                attempt_id="att-b",
                checkpoint_sequence=1,
                contract_json="{}",
            )
        )
        s.commit()
    cps = _get_checkpoints_by_run(cross_run_db, "run-b")
    assert len(cps) == 1
    assert cps[0].run_id == "run-b"


def test_cross_run_recovery_selects_right_checkpoint(cross_run_db: str) -> None:
    """Recovery never selects a checkpoint from another run."""
    svc = _run_service(cross_run_db)
    create_run(svc, specification=make_spec(run_id="run-a"), command_id="cmd-a-1")
    create_run(svc, specification=make_spec(run_id="run-b"), command_id="cmd-b-1")
    engine = create_database_engine(cross_run_db)
    with Session(engine) as s:
        s.add(
            AgentRuntimeCheckpointRow(
                checkpoint_id="cp-1",
                run_id="run-a",
                attempt_id="att-a",
                checkpoint_sequence=1,
                contract_json='{"run_id":"run-a"}',
            )
        )
        s.add(
            AgentRuntimeCheckpointRow(
                checkpoint_id="cp-1",
                run_id="run-b",
                attempt_id="att-b",
                checkpoint_sequence=1,
                contract_json='{"run_id":"run-b"}',
            )
        )
        s.commit()
    cps = _get_checkpoints_by_run(cross_run_db, "run-a")
    assert len(cps) == 1
    assert cps[0].run_id == "run-a"


def test_cross_run_replay_no_mixed_state(cross_run_db: str) -> None:
    """Replay never mixes checkpoint state."""
    url = cross_run_db
    svc = _run_service(url)
    create_run(svc, specification=make_spec(run_id="run-a"), command_id="cmd-a-1")
    create_run(svc, specification=make_spec(run_id="run-b"), command_id="cmd-b-1")
    engine = create_database_engine(url)
    with Session(engine) as s:
        s.add(
            AgentRuntimeCheckpointRow(
                checkpoint_id="cp-1",
                run_id="run-a",
                attempt_id="att-a",
                checkpoint_sequence=1,
                contract_json='{"run_id":"run-a"}',
            )
        )
        s.add(
            AgentRuntimeCheckpointRow(
                checkpoint_id="cp-1",
                run_id="run-b",
                attempt_id="att-b",
                checkpoint_sequence=1,
                contract_json='{"run_id":"run-b"}',
            )
        )
        s.commit()
    a_cps = _get_checkpoints_by_run(url, "run-a")
    b_cps = _get_checkpoints_by_run(url, "run-b")
    assert a_cps[0].contract_json == '{"run_id":"run-a"}'
    assert b_cps[0].contract_json == '{"run_id":"run-b"}'


def test_cross_run_same_run_conflict_returns_stable_conflict(cross_run_db: str) -> None:
    """Same-run conflicting reuse returns the stable checkpoint conflict."""
    svc = _run_service(cross_run_db)
    create_run(svc, specification=make_spec(run_id="run-a"), command_id="cmd-a-1")
    engine = create_database_engine(cross_run_db)
    with Session(engine) as s:
        s.add(
            AgentRuntimeCheckpointRow(
                checkpoint_id="cp-same",
                run_id="run-a",
                attempt_id="att-a",
                checkpoint_sequence=1,
                contract_json="{}",
            )
        )
        s.commit()
    with Session(engine) as s:
        s.add(
            AgentRuntimeCheckpointRow(
                checkpoint_id="cp-same",
                run_id="run-a",
                attempt_id="att-b",
                checkpoint_sequence=2,
                contract_json="{}",
            )
        )
        with pytest.raises(IntegrityError):
            s.commit()


def test_cross_run_reuse_succeeds(cross_run_db: str) -> None:
    """Cross-run reuse succeeds."""
    svc = _run_service(cross_run_db)
    create_run(svc, specification=make_spec(run_id="run-a"), command_id="cmd-a-1")
    create_run(svc, specification=make_spec(run_id="run-b"), command_id="cmd-b-1")
    engine = create_database_engine(cross_run_db)
    with Session(engine) as s:
        s.add(
            AgentRuntimeCheckpointRow(
                checkpoint_id="cp-shared",
                run_id="run-a",
                attempt_id="att-a",
                checkpoint_sequence=1,
                contract_json="{}",
            )
        )
        s.add(
            AgentRuntimeCheckpointRow(
                checkpoint_id="cp-shared",
                run_id="run-b",
                attempt_id="att-b",
                checkpoint_sequence=1,
                contract_json="{}",
            )
        )
        s.commit()
    assert _count_checkpoints(cross_run_db) == 2


def test_cross_run_delete_run_a_cascades_only_a_checkpoint(cross_run_db: str) -> None:
    """Deleting Run A cascades only Run A's checkpoint."""
    svc = _run_service(cross_run_db)
    create_run(svc, specification=make_spec(run_id="run-a"), command_id="cmd-a-1")
    create_run(svc, specification=make_spec(run_id="run-b"), command_id="cmd-b-1")
    engine = create_database_engine(cross_run_db)
    with Session(engine) as s:
        s.add(
            AgentRuntimeCheckpointRow(
                checkpoint_id="cp-1",
                run_id="run-a",
                attempt_id="att-a",
                checkpoint_sequence=1,
                contract_json="{}",
            )
        )
        s.add(
            AgentRuntimeCheckpointRow(
                checkpoint_id="cp-1",
                run_id="run-b",
                attempt_id="att-b",
                checkpoint_sequence=1,
                contract_json="{}",
            )
        )
        s.commit()
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM agent_runtime_runs WHERE run_id = 'run-a'"))
    remaining = _get_checkpoints_by_run(cross_run_db, "run-b")
    assert len(remaining) == 1
    assert remaining[0].run_id == "run-b"


def test_cross_run_b_checkpoint_remains(cross_run_db: str) -> None:
    """Run B's checkpoint remains after Run A is deleted."""
    svc = _run_service(cross_run_db)
    create_run(svc, specification=make_spec(run_id="run-a"), command_id="cmd-a-1")
    create_run(svc, specification=make_spec(run_id="run-b"), command_id="cmd-b-1")
    engine = create_database_engine(cross_run_db)
    with Session(engine) as s:
        s.add(
            AgentRuntimeCheckpointRow(
                checkpoint_id="cp-1",
                run_id="run-a",
                attempt_id="att-a",
                checkpoint_sequence=1,
                contract_json="{}",
            )
        )
        s.add(
            AgentRuntimeCheckpointRow(
                checkpoint_id="cp-1",
                run_id="run-b",
                attempt_id="att-b",
                checkpoint_sequence=1,
                contract_json="{}",
            )
        )
        s.commit()
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM agent_runtime_runs WHERE run_id = 'run-a'"))
    assert _count_checkpoints(cross_run_db) == 1
    remaining = _get_checkpoints_by_run(cross_run_db, "run-b")
    assert remaining[0].run_id == "run-b"


def test_cross_run_missing_run_fk_fails(cross_run_db: str) -> None:
    """A checkpoint referencing a missing run fails through the foreign key."""
    engine = create_database_engine(cross_run_db)
    with pytest.raises(IntegrityError):
        with Session(engine) as s:
            s.add(
                AgentRuntimeCheckpointRow(
                    checkpoint_id="cp-orphan",
                    run_id="run-missing",
                    attempt_id="att-orphan",
                    checkpoint_sequence=1,
                    contract_json="{}",
                )
            )
            s.commit()


def test_cross_run_listing_does_not_collapse_same_id(cross_run_db: str) -> None:
    """Pagination/listing does not collapse matching IDs across runs."""
    svc = _run_service(cross_run_db)
    create_run(svc, specification=make_spec(run_id="run-a"), command_id="cmd-a-1")
    create_run(svc, specification=make_spec(run_id="run-b"), command_id="cmd-b-1")
    engine = create_database_engine(cross_run_db)
    with Session(engine) as s:
        for seq in range(1, 4):
            s.add(
                AgentRuntimeCheckpointRow(
                    checkpoint_id=f"cp-{seq}",
                    run_id="run-a",
                    attempt_id="att-a",
                    checkpoint_sequence=seq,
                    contract_json="{}",
                )
            )
            s.add(
                AgentRuntimeCheckpointRow(
                    checkpoint_id=f"cp-{seq}",
                    run_id="run-b",
                    attempt_id="att-b",
                    checkpoint_sequence=seq,
                    contract_json="{}",
                )
            )
        s.commit()
    assert len(_get_checkpoints_by_run(cross_run_db, "run-a")) == 3
    assert len(_get_checkpoints_by_run(cross_run_db, "run-b")) == 3


def test_cross_run_no_lookup_by_checkpoint_id_alone(cross_run_db: str) -> None:
    """No lookup identifies a checkpoint by checkpoint_id alone."""
    svc = _run_service(cross_run_db)
    create_run(svc, specification=make_spec(run_id="run-a"), command_id="cmd-a-1")
    create_run(svc, specification=make_spec(run_id="run-b"), command_id="cmd-b-1")
    engine = create_database_engine(cross_run_db)
    with Session(engine) as s:
        s.add(
            AgentRuntimeCheckpointRow(
                checkpoint_id="cp-1",
                run_id="run-a",
                attempt_id="att-a",
                checkpoint_sequence=1,
                contract_json="{}",
            )
        )
        s.add(
            AgentRuntimeCheckpointRow(
                checkpoint_id="cp-1",
                run_id="run-b",
                attempt_id="att-b",
                checkpoint_sequence=1,
                contract_json="{}",
            )
        )
        s.commit()
    with Session(engine) as s:
        rows = s.execute(
            text(
                "SELECT checkpoint_id, run_id FROM agent_runtime_checkpoints "
                "WHERE checkpoint_id = 'cp-1'"
            )
        ).all()
    assert len(rows) == 2


# ──────────────────────────────────────────────
# Step 4: Parent-flush transaction tests
# ──────────────────────────────────────────────


def test_parent_flush_create_run_succeeds_with_fk(tmp_path: Path) -> None:
    """Creating a run succeeds with foreign keys enabled."""
    url = database_url(tmp_path / "parent-flush-fk.db")
    _migrate_head(url)
    engine = create_database_engine(url)
    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
    repo = _run_sql_repository(url)
    svc = AgentRuntimeService(
        repo,
        utc_clock=lambda: ts(10_000),
        run_id_factory=SequenceFactory("run"),
        attempt_id_factory=SequenceFactory("attempt"),
        event_id_factory=SequenceFactory("event"),
        checkpoint_id_factory=SequenceFactory("checkpoint"),
    )
    result = create_run(svc, specification=make_spec(run_id="run-flush"), command_id="cmd-flush-1")
    assert result.run_id == "run-flush"


def test_parent_flush_initial_event_persists(tmp_path: Path) -> None:
    """Initial event persists."""
    url = database_url(tmp_path / "parent-flush-event.db")
    _migrate_head(url)
    repo = _run_sql_repository(url)
    svc = AgentRuntimeService(
        repo,
        utc_clock=lambda: ts(10_000),
        run_id_factory=SequenceFactory("run"),
        attempt_id_factory=SequenceFactory("attempt"),
        event_id_factory=SequenceFactory("event"),
        checkpoint_id_factory=SequenceFactory("checkpoint"),
    )
    create_run(svc, specification=make_spec(run_id="run-evt"), command_id="cmd-evt-1")
    repo2 = _run_sql_repository(url)
    events = repo2.list_events("run-evt")
    assert len(events) >= 1


def test_parent_flush_processed_command_persists(tmp_path: Path) -> None:
    """Processed-command row persists."""
    url = database_url(tmp_path / "parent-flush-cmd.db")
    _migrate_head(url)
    repo = _run_sql_repository(url)
    svc = AgentRuntimeService(
        repo,
        utc_clock=lambda: ts(10_000),
        run_id_factory=SequenceFactory("run"),
        attempt_id_factory=SequenceFactory("attempt"),
        event_id_factory=SequenceFactory("event"),
        checkpoint_id_factory=SequenceFactory("checkpoint"),
    )
    create_run(svc, specification=make_spec(run_id="run-cmd"), command_id="cmd-persist-1")
    pc = repo.get_processed_command("run-cmd", "cmd-persist-1")
    assert pc is not None


def test_parent_flush_audit_row_persists(tmp_path: Path) -> None:
    """Audit row persists."""
    url = database_url(tmp_path / "parent-flush-audit.db")
    _migrate_head(url)
    repo = _run_sql_repository(url)
    svc = AgentRuntimeService(
        repo,
        utc_clock=lambda: ts(10_000),
        run_id_factory=SequenceFactory("run"),
        attempt_id_factory=SequenceFactory("attempt"),
        event_id_factory=SequenceFactory("event"),
        checkpoint_id_factory=SequenceFactory("checkpoint"),
    )
    create_run(svc, specification=make_spec(run_id="run-audit"), command_id="cmd-audit-1")
    assert repo.get_processed_command("run-audit", "cmd-audit-1") is not None


def test_parent_flush_outbox_row_persists(tmp_path: Path) -> None:
    """Outbox row persists."""
    url = database_url(tmp_path / "parent-flush-outbox.db")
    _migrate_head(url)
    repo = _run_sql_repository(url)
    svc = AgentRuntimeService(
        repo,
        utc_clock=lambda: ts(10_000),
        run_id_factory=SequenceFactory("run"),
        attempt_id_factory=SequenceFactory("attempt"),
        event_id_factory=SequenceFactory("event"),
        checkpoint_id_factory=SequenceFactory("checkpoint"),
    )
    create_run(svc, specification=make_spec(run_id="run-outbox"), command_id="cmd-outbox-1")
    engine = create_database_engine(url)
    with Session(engine) as s:
        rows = s.execute(
            text("SELECT event_type FROM outbox_events WHERE event_type LIKE 'agent_runtime.%'")
        ).all()
    assert len(rows) >= 1


def test_parent_flush_separate_repository_reloads_run(tmp_path: Path) -> None:
    """A separate repository reloads the run."""
    url = database_url(tmp_path / "parent-flush-reload.db")
    _migrate_head(url)
    repo = _run_sql_repository(url)
    svc = AgentRuntimeService(
        repo,
        utc_clock=lambda: ts(10_000),
        run_id_factory=SequenceFactory("run"),
        attempt_id_factory=SequenceFactory("attempt"),
        event_id_factory=SequenceFactory("event"),
        checkpoint_id_factory=SequenceFactory("checkpoint"),
    )
    create_run(svc, specification=make_spec(run_id="run-reload"), command_id="cmd-reload-1")
    repo2 = _run_sql_repository(url)
    snapshot = repo2.load_run("run-reload")
    assert snapshot is not None
    assert snapshot.specification.run_id == "run-reload"


def test_parent_flush_exact_replay_no_duplicates(tmp_path: Path) -> None:
    """Exact replay creates no duplicate rows."""
    url = database_url(tmp_path / "parent-flush-nodupe.db")
    _migrate_head(url)
    repo = _run_sql_repository(url)
    svc = AgentRuntimeService(
        repo,
        utc_clock=lambda: ts(10_000),
        run_id_factory=SequenceFactory("run"),
        attempt_id_factory=SequenceFactory("attempt"),
        event_id_factory=SequenceFactory("event"),
        checkpoint_id_factory=SequenceFactory("checkpoint"),
    )
    spec = make_spec(run_id="run-nodupe")
    create_run(svc, specification=spec, command_id="cmd-nodupe-1")
    result2 = create_run(svc, specification=spec, command_id="cmd-nodupe-1")
    assert result2.idempotent_replay is True
    engine = create_database_engine(url)
    with Session(engine) as s:
        run_count = s.scalar(select(func.count()).select_from(AgentRuntimeRunRow))
    assert run_count == 1


def test_parent_flush_rollback_removes_all(tmp_path: Path) -> None:
    """A forced failure after the parent flush rolls back all rows."""
    url = database_url(tmp_path / "parent-flush-rollback.db")
    _migrate_head(url)
    engine = create_database_engine(url)
    with Session(engine) as s:
        run_row = AgentRuntimeRunRow(
            run_id="run-fail",
            task_id="task-1",
            agent_id="agent-1",
            correlation_id="corr-1",
            parent_run_id=None,
            state="created",
            version=1,
            event_sequence_number=0,
            attempt_count=0,
            active_attempt_id=None,
            latest_checkpoint_id=None,
            recovery_status="none",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            deadline=None,
            terminal_at=None,
            specification_json='{"run_id":"run-fail"}',
            snapshot_json='{"run_id":"run-fail"}',
        )
        s.add(run_row)
        s.flush([run_row])
        s.rollback()
    with Session(engine) as s:
        assert s.get(AgentRuntimeRunRow, "run-fail") is None


def test_parent_flush_no_partial_state_after_rollback(tmp_path: Path) -> None:
    """No partial state remains after rollback."""
    url = database_url(tmp_path / "parent-flush-partial.db")
    _migrate_head(url)
    engine = create_database_engine(url)
    with Session(engine) as s:
        run_row = AgentRuntimeRunRow(
            run_id="run-partial",
            task_id="task-1",
            agent_id="agent-1",
            correlation_id="corr-1",
            parent_run_id=None,
            state="created",
            version=1,
            event_sequence_number=0,
            attempt_count=0,
            active_attempt_id=None,
            latest_checkpoint_id=None,
            recovery_status="none",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            deadline=None,
            terminal_at=None,
            specification_json="{}",
            snapshot_json="{}",
        )
        s.add(run_row)
        s.flush([run_row])
        s.add(
            AgentRuntimeCheckpointRow(
                checkpoint_id="cp-partial",
                run_id="run-partial",
                attempt_id="att-partial",
                checkpoint_sequence=1,
                contract_json="{}",
            )
        )
        s.rollback()
    with Session(engine) as s:
        assert s.scalar(select(func.count()).select_from(AgentRuntimeRunRow)) == 0


# ──────────────────────────────────────────────
# Step 5: Create-idempotency ordering tests
# ──────────────────────────────────────────────


def test_create_idempotency_exact_replay_returns_stored(tmp_path: Path) -> None:
    """Exact create replay returns the stored result."""
    url = database_url(tmp_path / "idem-exact.db")
    _migrate_head(url)
    repo = _run_sql_repository(url)
    svc = AgentRuntimeService(
        repo,
        utc_clock=lambda: ts(10_000),
        run_id_factory=SequenceFactory("run"),
        attempt_id_factory=SequenceFactory("attempt"),
        event_id_factory=SequenceFactory("event"),
        checkpoint_id_factory=SequenceFactory("checkpoint"),
    )
    spec = make_spec(run_id="run-idem-1")
    r1 = create_run(svc, specification=spec, command_id="cmd-idem-1")
    assert r1.run_id == "run-idem-1"
    r2 = create_run(svc, specification=spec, command_id="cmd-idem-1")
    assert r2.idempotent_replay is True


def test_create_idempotency_replay_sets_idempotent_replay(tmp_path: Path) -> None:
    """It sets idempotent_replay=True."""
    url = database_url(tmp_path / "idem-flag.db")
    _migrate_head(url)
    repo = _run_sql_repository(url)
    svc = AgentRuntimeService(
        repo,
        utc_clock=lambda: ts(10_000),
        run_id_factory=SequenceFactory("run"),
        attempt_id_factory=SequenceFactory("attempt"),
        event_id_factory=SequenceFactory("event"),
        checkpoint_id_factory=SequenceFactory("checkpoint"),
    )
    spec = make_spec(run_id="run-idem-flag")
    create_run(svc, specification=spec, command_id="cmd-flag")
    r2 = create_run(svc, specification=spec, command_id="cmd-flag")
    assert r2.idempotent_replay is True


def test_create_idempotency_changed_version_returns_conflict(tmp_path: Path) -> None:
    """Changed expected_run_version with the same command ID returns command_conflict."""
    url = database_url(tmp_path / "idem-version.db")
    _migrate_head(url)
    repo = _run_sql_repository(url)
    svc = AgentRuntimeService(
        repo,
        utc_clock=lambda: ts(10_000),
        run_id_factory=SequenceFactory("run"),
        attempt_id_factory=SequenceFactory("attempt"),
        event_id_factory=SequenceFactory("event"),
        checkpoint_id_factory=SequenceFactory("checkpoint"),
    )
    spec = make_spec(run_id="run-idem-ver")
    create_run(svc, specification=spec, command_id="cmd-ver")
    with pytest.raises(CommandConflictError):
        svc.create_run(
            CreateAgentRunCommand(
                specification=spec,
                command_id="cmd-ver",
                expected_run_version=999,
                timestamp=ts(10_000),
                actor_reference="operator-1",
                source_metadata={"source": "test"},
            )
        )


def test_create_idempotency_changed_spec_returns_conflict(tmp_path: Path) -> None:
    """Changed specification returns command_conflict."""
    url = database_url(tmp_path / "idem-spec.db")
    _migrate_head(url)
    repo = _run_sql_repository(url)
    svc = AgentRuntimeService(
        repo,
        utc_clock=lambda: ts(10_000),
        run_id_factory=SequenceFactory("run"),
        attempt_id_factory=SequenceFactory("attempt"),
        event_id_factory=SequenceFactory("event"),
        checkpoint_id_factory=SequenceFactory("checkpoint"),
    )
    spec1 = make_spec(run_id="run-idem-spec", task_id="task-1")
    create_run(svc, specification=spec1, command_id="cmd-spec")
    spec2 = make_spec(run_id="run-idem-spec", task_id="task-different")
    with pytest.raises(CommandConflictError):
        svc.create_run(
            CreateAgentRunCommand(
                specification=spec2,
                command_id="cmd-spec",
                expected_run_version=0,
                timestamp=ts(10_000),
                actor_reference="operator-1",
                source_metadata={"source": "test"},
            )
        )


def test_create_idempotency_changed_deadline_returns_conflict(tmp_path: Path) -> None:
    """Changed deadline returns command_conflict."""
    url = database_url(tmp_path / "idem-deadline.db")
    _migrate_head(url)
    repo = _run_sql_repository(url)
    svc = AgentRuntimeService(
        repo,
        utc_clock=lambda: ts(10_000),
        run_id_factory=SequenceFactory("run"),
        attempt_id_factory=SequenceFactory("attempt"),
        event_id_factory=SequenceFactory("event"),
        checkpoint_id_factory=SequenceFactory("checkpoint"),
    )
    spec1 = make_spec(run_id="run-idem-dl")
    create_run(svc, specification=spec1, command_id="cmd-dl")
    spec2 = make_spec(run_id="run-idem-dl")
    with pytest.raises(CommandConflictError):
        svc.create_run(
            CreateAgentRunCommand(
                specification=spec2,
                command_id="cmd-dl",
                expected_run_version=0,
                timestamp=ts(10_000),
                actor_reference="operator-1",
                source_metadata={"source": "test"},
            )
        )


def test_create_idempotency_equivalent_canonical_ordering_is_exact_replay(tmp_path: Path) -> None:
    """Equivalent canonical payload ordering remains an exact replay."""
    url = database_url(tmp_path / "idem-canonical.db")
    _migrate_head(url)
    repo = _run_sql_repository(url)
    svc = AgentRuntimeService(
        repo,
        utc_clock=lambda: ts(10_000),
        run_id_factory=SequenceFactory("run"),
        attempt_id_factory=SequenceFactory("attempt"),
        event_id_factory=SequenceFactory("event"),
        checkpoint_id_factory=SequenceFactory("checkpoint"),
    )
    spec = make_spec(run_id="run-idem-can")
    create_run(svc, specification=spec, command_id="cmd-can")
    repo2 = _run_sql_repository(url)
    svc2 = AgentRuntimeService(
        repo2,
        utc_clock=lambda: ts(10_000),
        run_id_factory=SequenceFactory("run"),
        attempt_id_factory=SequenceFactory("attempt"),
        event_id_factory=SequenceFactory("event"),
        checkpoint_id_factory=SequenceFactory("checkpoint"),
    )
    r2 = create_run(svc2, specification=spec, command_id="cmd-can")
    assert r2.idempotent_replay is True


def test_create_idempotency_new_with_nonzero_version_returns_version_conflict(
    tmp_path: Path,
) -> None:
    """A genuinely new create command with nonzero version returns version_conflict."""
    url = database_url(tmp_path / "idem-version-conflict.db")
    _migrate_head(url)
    repo = _run_sql_repository(url)
    svc = AgentRuntimeService(
        repo,
        utc_clock=lambda: ts(10_000),
        run_id_factory=SequenceFactory("run"),
        attempt_id_factory=SequenceFactory("attempt"),
        event_id_factory=SequenceFactory("event"),
        checkpoint_id_factory=SequenceFactory("checkpoint"),
    )
    spec = make_spec(run_id="run-idem-vc")
    with pytest.raises(VersionConflictError):
        svc.create_run(
            CreateAgentRunCommand(
                specification=spec,
                command_id="cmd-vc",
                expected_run_version=5,
                timestamp=ts(10_000),
                actor_reference="operator-1",
                source_metadata={"source": "test"},
            )
        )


def test_create_idempotency_no_additional_rows_on_replay(tmp_path: Path) -> None:
    """Changed replay creates no additional run, event, processed command, audit, or outbox rows."""
    url = database_url(tmp_path / "idem-noextra.db")
    _migrate_head(url)
    repo = _run_sql_repository(url)
    svc = AgentRuntimeService(
        repo,
        utc_clock=lambda: ts(10_000),
        run_id_factory=SequenceFactory("run"),
        attempt_id_factory=SequenceFactory("attempt"),
        event_id_factory=SequenceFactory("event"),
        checkpoint_id_factory=SequenceFactory("checkpoint"),
    )
    spec = make_spec(run_id="run-idem-ne")
    create_run(svc, specification=spec, command_id="cmd-ne")
    engine = create_database_engine(url)
    with Session(engine) as s:
        run_count = s.scalar(select(func.count()).select_from(AgentRuntimeRunRow))
        pc_count = s.scalar(select(func.count()).select_from(AgentRuntimeProcessedCommandRow))
    create_run(svc, specification=spec, command_id="cmd-ne")
    with Session(engine) as s:
        assert s.scalar(select(func.count()).select_from(AgentRuntimeRunRow)) == run_count
        assert (
            s.scalar(select(func.count()).select_from(AgentRuntimeProcessedCommandRow)) == pc_count
        )


def test_create_idempotency_survives_restart(tmp_path: Path) -> None:
    """Behavior survives restart and a new repository instance."""
    url = database_url(tmp_path / "idem-restart.db")
    _migrate_head(url)
    repo = _run_sql_repository(url)
    svc = AgentRuntimeService(
        repo,
        utc_clock=lambda: ts(10_000),
        run_id_factory=SequenceFactory("run"),
        attempt_id_factory=SequenceFactory("attempt"),
        event_id_factory=SequenceFactory("event"),
        checkpoint_id_factory=SequenceFactory("checkpoint"),
    )
    spec = make_spec(run_id="run-idem-rs")
    create_run(svc, specification=spec, command_id="cmd-rs")
    repo2 = _run_sql_repository(url)
    svc2 = AgentRuntimeService(
        repo2,
        utc_clock=lambda: ts(10_000),
        run_id_factory=SequenceFactory("run"),
        attempt_id_factory=SequenceFactory("attempt"),
        event_id_factory=SequenceFactory("event"),
        checkpoint_id_factory=SequenceFactory("checkpoint"),
    )
    r2 = create_run(svc2, specification=spec, command_id="cmd-rs")
    assert r2.idempotent_replay is True


# ──────────────────────────────────────────────
# Step 6: Application health boundary tests
# ──────────────────────────────────────────────


def test_health_reachable_current_schema_invokes_runtime_health(tmp_path: Path) -> None:
    """Reachable current schema invokes runtime health."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    url = database_url(tmp_path / "health-runtime.db")
    _migrate_head(url)
    app = create_app(delay_ms=1, database_url=url)
    with TestClient(app) as api:
        resp = api.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "healthy"


def test_health_stale_schema_does_not_invoke_runtime_health(tmp_path: Path) -> None:
    """Stale schema does not invoke runtime health."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    url = database_url(tmp_path / "health-stale.db")
    _migrate_head(url)
    app = create_app(delay_ms=1, database_url=url)
    with TestClient(app) as api:
        original_probe = app.state.repository.health_probe
        app.state.repository.health_probe = lambda _rev: (True, False)
        resp = api.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data.get("schemaCurrent") is False
        assert data["runtimePersistence"]["configured"] is False
        app.state.repository.health_probe = original_probe


def test_health_old_schema_does_not_query_runtime_tables(tmp_path: Path) -> None:
    """A database at 20260720_01 does not query agent_runtime_runs."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    url = database_url(tmp_path / "health-old.db")
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "20260720_01")
    app = create_app(delay_ms=1, database_url=url)
    with TestClient(app) as api:
        resp = api.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] in ("healthy", "degraded")


def test_health_auto_migrate_false_with_stale_schema(tmp_path: Path, monkeypatch) -> None:
    """JARVIS_AUTO_MIGRATE=false with a stale schema returns a bounded response."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    monkeypatch.setenv("JARVIS_AUTO_MIGRATE", "false")
    url = database_url(tmp_path / "health-auto.db")
    _migrate_head(url)
    app = create_app(delay_ms=1, database_url=url)
    with TestClient(app) as api:
        with app.state.engine.begin() as conn:
            conn.execute(text("DROP TABLE alembic_version"))
        resp = api.get("/api/health")
        assert resp.status_code == 200


def test_health_unreachable_db_does_not_invoke_runtime(tmp_path: Path) -> None:
    """Unreachable database does not invoke runtime health."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    url = database_url(tmp_path / "health-unreachable.db")
    _migrate_head(url)
    app = create_app(delay_ms=1, database_url=url)
    app.state.repository.health_probe = lambda _rev: (False, False)
    with TestClient(app) as api:
        resp = api.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "degraded"


def test_health_unreachable_db_returns_health_response(tmp_path: Path) -> None:
    """Unreachable database returns a health response instead of HTTP 500."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    url = database_url(tmp_path / "health-response.db")
    _migrate_head(url)
    app = create_app(delay_ms=1, database_url=url)
    app.state.repository.health_probe = lambda _rev: (False, False)
    with TestClient(app) as api:
        resp = api.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["data"]["databaseReachable"] is False


def test_health_stale_returns_schema_stale(tmp_path: Path) -> None:
    """Stale schema returns schema_stale."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    url = database_url(tmp_path / "health-stale-code.db")
    _migrate_head(url)
    app = create_app(delay_ms=1, database_url=url)
    with TestClient(app) as api:
        app.state.repository.health_probe = lambda _rev: (True, False)
        resp = api.get("/api/health")
        data = resp.json()["data"]
        assert data.get("schemaCurrent") is False


def test_health_unreachable_returns_database_unreachable(tmp_path: Path) -> None:
    """Unreachable database returns database_unreachable."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    url = database_url(tmp_path / "health-unreachable-2.db")
    _migrate_head(url)
    app = create_app(delay_ms=1, database_url=url)
    app.state.repository.health_probe = lambda _rev: (False, False)
    with TestClient(app) as api:
        resp = api.get("/api/health")
        data = resp.json()["data"]
        assert data.get("databaseReachable") is False


def test_health_missing_runtime_table_is_safe(tmp_path: Path) -> None:
    """Missing runtime table is represented safely."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    url = database_url(tmp_path / "health-missing-table.db")
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "20260727_05")
    app = create_app(delay_ms=1, database_url=url)
    with TestClient(app) as api:
        resp = api.get("/api/health")
        assert resp.status_code == 200


def test_health_runtime_failure_on_valid_schema_returns_bounded_health(tmp_path: Path) -> None:
    """An unexpected runtime repository failure returns bounded health."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    url = database_url(tmp_path / "health-runtime-fail.db")
    _migrate_head(url)
    app = create_app(delay_ms=1, database_url=url)
    original_health = app.state.agent_runtime_repository.health_status
    app.state.agent_runtime_repository.health_status = lambda: (_ for _ in ()).throw(  # type: ignore[method-assign]
        RuntimeError("Unexpected runtime failure")
    )
    with TestClient(app) as api:
        resp = api.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["runtimePersistence"]["reasonCode"] == "runtime_health_query_failed"
    app.state.agent_runtime_repository.health_status = original_health


def test_health_errors_not_labeled_as_stale_schema(tmp_path: Path) -> None:
    """Unexpected errors are not mislabeled as stale schema."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    url = database_url(tmp_path / "health-not-stale.db")
    _migrate_head(url)
    app = create_app(delay_ms=1, database_url=url)
    original_health = app.state.agent_runtime_repository.health_status
    app.state.agent_runtime_repository.health_status = lambda: (_ for _ in ()).throw(  # type: ignore[method-assign]
        RuntimeError("unexpected error")
    )
    with TestClient(app) as api:
        resp = api.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data.get("schemaCurrent") is not False
        assert data["runtimePersistence"]["reasonCode"] == "runtime_health_query_failed"
    app.state.agent_runtime_repository.health_status = original_health


def test_health_no_exposed_paths_or_tracebacks(tmp_path: Path) -> None:
    """No database path, SQL, connection string, or traceback is exposed."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    url = database_url(tmp_path / "health-no-leak.db")
    _migrate_head(url)
    app = create_app(delay_ms=1, database_url=url)
    app.state.repository.health_probe = lambda _rev: (False, False)
    with TestClient(app) as api:
        resp = api.get("/api/health")
        body = resp.text
        assert "sqlite" not in body.lower()
        assert "traceback" not in body.lower()
        assert "/tmp/" not in body


def test_health_does_not_mutate_data(tmp_path: Path) -> None:
    """Health checks do not mutate data."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    url = database_url(tmp_path / "health-no-mutate.db")
    _migrate_head(url)
    app = create_app(delay_ms=1, database_url=url)
    engine = create_database_engine(url)
    with Session(engine) as s:
        run_count_before = s.scalar(select(func.count()).select_from(AgentRuntimeRunRow))
    with TestClient(app) as api:
        for _ in range(5):
            api.get("/api/health")
    with Session(engine) as s:
        run_count_after = s.scalar(select(func.count()).select_from(AgentRuntimeRunRow))
    assert run_count_after == run_count_before


def test_health_other_components_present(tmp_path: Path) -> None:
    """Other health components remain present."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    url = database_url(tmp_path / "health-components.db")
    _migrate_head(url)
    app = create_app(delay_ms=1, database_url=url)
    with TestClient(app) as api:
        resp = api.get("/api/health")
        data = resp.json()["data"]
        assert "status" in data
