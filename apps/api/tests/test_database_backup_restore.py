from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


def database_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


@pytest.fixture(autouse=True)
def isolated_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    database = (tmp_path / f"jarvis-backup-test-{request.node.name}.db").as_posix()
    monkeypatch.setenv("JARVIS_DATABASE_URL", f"sqlite:///{database}")


def client(delay_ms: int = 1, db_url: str | None = None) -> TestClient:
    import os

    if db_url:
        os.environ["JARVIS_DATABASE_URL"] = db_url
    return TestClient(create_app(delay_ms=delay_ms, database_url=db_url))


def backup_sqlite(source_path: Path, dest_path: Path) -> None:
    """
    Test-local backup helper. There is no formal backup API currently shipped.
    We must use the SQLite backup API to ensure a consistent snapshot, including WAL.
    """
    source_db = sqlite3.connect(source_path)
    dest_db = sqlite3.connect(dest_path)
    with dest_db:
        source_db.backup(dest_db)
    dest_db.close()
    source_db.close()


def test_group_a_c_clean_shutdown_backup_and_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "original.db"
    url = database_url(db_path)

    with client(db_url=url) as api:
        res = api.post(
            "/api/tasks",
            json={"title": "Test Backup", "description": "Needs preserving", "priority": "high"},
        )
        assert res.status_code == 201

    import gc

    gc.collect()

    backup_path = tmp_path / "backup.db"
    backup_sqlite(db_path, backup_path)
    assert backup_path.exists()
    assert backup_path.stat().st_size > 0

    # Restore
    restored_path = tmp_path / "restored.db"
    shutil.copy2(backup_path, restored_path)
    restored_url = database_url(restored_path)

    with client(db_url=restored_url) as api2:
        status = api2.get("/api/system/status").json()["data"]
        assert status["status"] == "healthy"
        assert status["databaseHealthy"] is True
        assert status["schemaCurrent"] is True

        # Verify schema version
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        config_path = Path(__file__).resolve().parents[2] / "alembic.ini"
        if not config_path.exists():
            config_path = Path("alembic.ini").resolve()
        config = Config(str(config_path))
        config.set_main_option("script_location", str(config_path.parent / "migrations"))
        script = ScriptDirectory.from_config(config)
        head_rev = script.get_current_head()
        assert status["databaseRevision"] == head_rev


def test_group_d_l_state_preservation(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    url = database_url(db_path)

    headers = {"Idempotency-Key": "my-idemp-key"}

    with client(db_url=url) as api:
        api.post(
            "/api/tasks",
            json={"title": "Custom task", "description": "Preserve me", "priority": "high"},
            headers=headers,
        )

    import gc

    gc.collect()

    backup_path = tmp_path / "state_backup.db"
    backup_sqlite(db_path, backup_path)

    restored_path = tmp_path / "state_restored.db"
    shutil.copy2(backup_path, restored_path)
    restored_url = database_url(restored_path)

    with client(db_url=restored_url) as api2:
        tasks = api2.get("/api/tasks").json()["data"]
        assert len(tasks) == 5

        # Idempotency check: replay the request
        replay = api2.post(
            "/api/tasks",
            json={"title": "Custom task", "description": "Preserve me", "priority": "high"},
            headers=headers,
        )
        assert replay.status_code == 201  # We replay, so it returns 201 with the SAME object

        tasks_after_replay = api2.get("/api/tasks").json()["data"]
        assert len(tasks_after_replay) == 5  # Still 5, no duplicate created


def test_group_m_o_emergency_stop_and_isolation(tmp_path: Path) -> None:
    db_path = tmp_path / "iso.db"
    url = database_url(db_path)

    with client(db_url=url) as api:
        api.post("/api/system/emergency-stop")

    import gc

    gc.collect()

    backup_path = tmp_path / "iso_backup.db"
    backup_sqlite(db_path, backup_path)
    restored_path = tmp_path / "iso_restored.db"
    shutil.copy2(backup_path, restored_path)

    # Mutate original
    with client(db_url=url) as api1:
        api1.post("/api/system/resume")

    # Check restored is isolated
    with client(db_url=database_url(restored_path)) as api2:
        status = api2.get("/api/system/status").json()["data"]
        assert status["emergencyStop"] is True


def test_group_z_ad_corruption_partial_copy(tmp_path: Path) -> None:
    # We create an invalid DB file and ensure the app doesn't silently accept it
    db_path = tmp_path / "corrupt.db"
    with open(db_path, "wb") as f:
        f.write(b"this is not a sqlite database")

    url = database_url(db_path)

    with pytest.raises((sqlite3.DatabaseError, sqlite3.OperationalError, Exception)):
        client(db_url=url)
