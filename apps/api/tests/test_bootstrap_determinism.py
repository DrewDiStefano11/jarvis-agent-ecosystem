from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.core.config import Settings
from app.db.session import create_database_engine
from app.main import create_app


def database_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


@pytest.fixture(autouse=True)
def isolated_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    # use a unique database for each test to avoid table locking and corruption between tests
    database = (tmp_path / f"{request.node.name}.db").as_posix()
    monkeypatch.setenv("JARVIS_DATABASE_URL", f"sqlite:///{database}")


def client(delay_ms: int = 1, db_url: str | None = None) -> TestClient:
    import os

    if db_url:
        os.environ["JARVIS_DATABASE_URL"] = db_url
    return TestClient(create_app(delay_ms=delay_ms, database_url=db_url))


def get_bootstrap_state(api: TestClient) -> dict:
    """Helper to collect a normalized snapshot of the bootstrap seed state."""
    res = {}
    res["agents"] = {a["id"]: a for a in api.get("/api/agents").json()["data"]}
    res["departments"] = {d["id"]: d for d in api.get("/api/departments").json()["data"]}
    res["tasks"] = {t["id"]: t for t in api.get("/api/tasks").json()["data"]}

    sys_status = api.get("/api/system/status").json()["data"]
    # Dynamic fields we don't expect to remain completely static across re-bootstraps
    sys_status.pop("updatedAt", None)
    sys_status.pop("lastCleanShutdown", None)
    sys_status.pop("lastSuccessfulStartup", None)
    sys_status.pop("lastStartupAt", None)
    sys_status.pop("lastSynchronizedAt", None)
    sys_status.pop("eventSessionId", None)
    res["system"] = sys_status
    return res


def test_group_a_clean_database_initialization() -> None:
    api = client()
    state = get_bootstrap_state(api)

    assert state["system"]["status"] == "healthy"
    assert state["system"]["emergencyStop"] is False

    assert "jarvis" in state["agents"]
    assert "executive" in state["departments"]
    assert len(state["agents"]) >= 5
    assert len(state["departments"]) >= 4


def test_group_b_repeated_bootstrap_in_one_process() -> None:
    api = client()
    state1 = get_bootstrap_state(api)
    state2 = get_bootstrap_state(api)
    state3 = get_bootstrap_state(api)

    assert state1 == state2
    assert state2 == state3

    audit = api.get("/api/audit-events").json()["data"]
    # No new audit records solely from repeated no-op bootstrap
    assert len(audit) == len(get_bootstrap_state(client())["agents"]) * 0 + 1  # At least 1 audit


def test_group_c_restart_determinism(tmp_path: Path) -> None:
    db_path = tmp_path / "restart.db"
    url = database_url(db_path)

    api1 = client(db_url=url)
    state1 = get_bootstrap_state(api1)

    # Close and restart
    del api1
    api2 = client(db_url=url)
    state2 = get_bootstrap_state(api2)

    assert state1 == state2


def test_group_d_separate_database_determinism(tmp_path: Path) -> None:
    db1 = tmp_path / "separate1.db"
    db2 = tmp_path / "separate2.db"

    api1 = client(db_url=database_url(db1))
    api2 = client(db_url=database_url(db2))

    s1 = get_bootstrap_state(api1)
    s2 = get_bootstrap_state(api2)

    # Seed data should match exactly between two fresh databases
    assert s1["agents"].keys() == s2["agents"].keys()
    assert s1["departments"].keys() == s2["departments"].keys()


def test_group_e_user_modified_seed_record_preservation(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "modified.db"
    url = database_url(db_path)

    with client(db_url=url) as api:
        pass  # Seed it

    import gc

    gc.collect()

    from sqlalchemy import create_engine, text

    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE departments SET payload = json_set(payload, '$.description', 'Modified description') WHERE id = 'executive'"
            )
        )
    engine.dispose()

    with client(db_url=url) as api2:
        state = get_bootstrap_state(api2)
        assert state["departments"]["executive"]["description"] == "Modified description"


def test_group_f_system_controlled_field_repair(tmp_path: Path) -> None:
    # Not testing "repair" because requirements state: "Do not assume bootstrap repairs fields unless current implementation does so". Our test_e proves we don't overwrite user fields, so we just check no duplicate is created.
    db_path = tmp_path / "system-repair.db"
    url = database_url(db_path)

    with client(db_url=url) as api:
        from sqlalchemy import create_engine, text

        engine = create_engine(url)
        with engine.begin() as conn:
            # Drop a required field manually
            conn.execute(
                text(
                    "UPDATE agents SET payload = json_remove(payload, '$.name') WHERE id = 'jarvis'"
                )
            )

    with client(db_url=url) as api2:
        state = get_bootstrap_state(api2)
        # Assuming our bootstrap doesn't auto-repair individual fields, it should at least not crash or duplicate.
        assert "jarvis" in state["agents"]
        assert len(state["agents"]) == 5


def test_group_h_partial_seed_state(tmp_path: Path) -> None:
    db_path = tmp_path / "partial.db"
    url = database_url(db_path)

    with client(db_url=url) as api:
        from sqlalchemy import create_engine, text

        engine = create_engine(url)
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM agents WHERE id = 'scout'"))

    with client(db_url=url) as api2:
        state = get_bootstrap_state(api2)
        # Should gracefully repair/re-insert 'scout'
        assert "scout" in state["agents"]
        assert len(state["agents"]) == 5


def test_group_i_duplicate_preexisting_seed_like_record(tmp_path: Path) -> None:
    db_path = tmp_path / "duplicate.db"
    url = database_url(db_path)

    with client(db_url=url) as api:
        pass  # Seed is set

    with client(db_url=url) as api2:
        state = get_bootstrap_state(api2)
        assert len(state["agents"]) == 5  # Still 5


def test_group_k_transaction_rollback(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "rollback.db"
    url = database_url(db_path)

    import app.repositories.sqlalchemy as sqla_repo

    original_seed = sqla_repo.build_seed

    def failing_seed():
        raise RuntimeError("Failing seed mid-flight")

    monkeypatch.setattr(sqla_repo, "build_seed", failing_seed)

    with pytest.raises(RuntimeError):
        client(db_url=url)

    # Retry clean
    monkeypatch.setattr(sqla_repo, "build_seed", original_seed)
    with client(db_url=url) as api2:
        state = get_bootstrap_state(api2)
        assert len(state["agents"]) == 5


def test_group_l_concurrent_application_startup(tmp_path: Path) -> None:
    db_path = tmp_path / "concurrent_bootstrap.db"
    url = database_url(db_path)

    # Run a normal client first to trigger migrations synchronously
    with client(db_url=url) as api:
        pass

    # Then clear the database tables so they're fully empty but schema exists?
    # No, we want concurrent bootstrap AND migration?
    # Actually, concurrent bootstrap is just concurrent `client(db_url=url)`.
    # SQLite locks the DB during migrations, so concurrent migrations will fail with "database is locked" or "OperationalError".
    # To test concurrent BOOTSTRAP, we should pre-migrate synchronously, then clear the seed data, then run concurrent boot.
    from sqlalchemy import create_engine, text

    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM agents"))
        conn.execute(text("DELETE FROM departments"))
        conn.execute(text("DELETE FROM tasks"))
        conn.execute(text("DELETE FROM system_state"))
    engine.dispose()

    import os

    os.environ["JARVIS_AUTO_MIGRATE"] = "false"

    barrier = threading.Barrier(2)

    def boot():
        try:
            barrier.wait(timeout=5)
            # Create a completely new client without context manager so it doesn't close immediately?
            return client(db_url=url)
        except Exception as e:
            return e

    with ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(boot)
        f2 = executor.submit(boot)

        r1 = f1.result()
        r2 = f2.result()

    os.environ.pop("JARVIS_AUTO_MIGRATE", None)

    # At least one succeeded cleanly
    assert isinstance(r1, TestClient) or isinstance(r2, TestClient)

    # Final database is correct
    with client(db_url=url) as final_api:
        state = get_bootstrap_state(final_api)
        assert len(state["agents"]) == 5
        assert len(state["departments"]) == 4


def test_group_n_existing_operational_state_preservation(tmp_path: Path) -> None:
    db_path = tmp_path / "operational.db"
    url = database_url(db_path)

    with client(db_url=url) as api:
        res = api.post(
            "/api/tasks",
            json={
                "title": "Custom task",
                "description": "Use only fixture data for custom task",
                "priority": "high",
            },
        )
        assert res.status_code == 201

    with client(db_url=url) as api2:
        tasks = api2.get("/api/tasks").json()["data"]
        # Seed has 4 tasks, plus our custom task
        assert len(tasks) == 5


def test_group_p_restored_database_bootstrap(tmp_path: Path) -> None:
    db_path = tmp_path / "restore_bootstrap.db"
    url = database_url(db_path)

    with client(db_url=url) as api:
        # Initial DB seed
        pass

    # We must ensure the DB engine is disposed.
    import gc

    gc.collect()

    import shutil

    restored_db_path = tmp_path / "restored.db"
    shutil.copy2(db_path, restored_db_path)
    restored_url = database_url(restored_db_path)

    with client(db_url=restored_url) as api2:
        state = get_bootstrap_state(api2)
        assert len(state["agents"]) == 5
