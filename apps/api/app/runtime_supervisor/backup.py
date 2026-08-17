from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import subprocess
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.runtime_supervisor.config import SupervisorConfig
from app.runtime_supervisor.io import atomic_write_json, read_json, utc_now, verified_runtime_home
from app.runtime_supervisor.ownership import SingletonLock

BACKUP_PREFIX = "jarvis-"
BACKUP_SUFFIX = ".sqlite3"


class BackupError(RuntimeError):
    pass


def _git_sha(repository: Path) -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _alembic_revision(connection: sqlite3.Connection) -> str | None:
    try:
        row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    except sqlite3.DatabaseError:
        return None
    return str(row[0]) if row else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _owned_backup(path: Path, directory: Path) -> bool:
    try:
        return (
            path.parent.resolve() == directory.resolve()
            and path.name.startswith(BACKUP_PREFIX)
            and path.name.endswith(BACKUP_SUFFIX)
            and path.is_file()
        )
    except OSError:
        return False


def prune_backups(config: SupervisorConfig) -> list[Path]:
    if not verified_runtime_home(config.runtime_home, config.repository):
        raise BackupError("refusing cleanup outside the verified supervisor runtime home")
    directory = config.backups_directory
    if not directory.exists():
        return []
    candidates = sorted(
        (item for item in directory.iterdir() if _owned_backup(item, directory)),
        key=lambda item: item.name,
        reverse=True,
    )
    removed: list[Path] = []
    for path in candidates[config.backup_retention_count :]:
        path.unlink()
        manifest = path.with_suffix(".json")
        if manifest.is_file() and manifest.parent.resolve() == directory.resolve():
            manifest.unlink()
        removed.append(path)
    for partial in directory.glob(f"{BACKUP_PREFIX}*.partial"):
        if partial.parent.resolve() == directory.resolve():
            partial.unlink()
    backup_names = {item.with_suffix(".json").name for item in candidates}
    for manifest in directory.glob(f"{BACKUP_PREFIX}*.json"):
        if manifest.parent.resolve() == directory.resolve() and manifest.name not in backup_names:
            manifest.unlink()
    return removed


def create_backup(config: SupervisorConfig) -> dict[str, Any]:
    if not verified_runtime_home(config.runtime_home, config.repository):
        raise BackupError("refusing backup outside the verified supervisor runtime home")
    lock = SingletonLock(config.runtime_home / "backup.lock")
    if not lock.acquire():
        raise BackupError("another SQLite backup is already running")
    try:
        return _create_backup(config)
    finally:
        lock.release()


def _create_backup(config: SupervisorConfig) -> dict[str, Any]:
    if not config.database_path.is_file():
        raise BackupError(f"database does not exist: {config.database_path}")
    free = shutil.disk_usage(config.runtime_home).free
    source_size = config.database_path.stat().st_size
    required = max(config.disk_critical_bytes, source_size * 2 + 16 * 1024 * 1024)
    if free < required:
        raise BackupError(f"insufficient disk space for backup ({free} bytes free)")
    config.backups_directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    final_path = config.backups_directory / f"{BACKUP_PREFIX}{stamp}{BACKUP_SUFFIX}"
    partial_path = final_path.with_suffix(".partial")
    revision: str | None = None
    try:
        with closing(sqlite3.connect(config.database_path, timeout=30)) as source:
            revision = _alembic_revision(source)
            with closing(sqlite3.connect(partial_path)) as destination:
                source.backup(destination)
        with closing(sqlite3.connect(f"file:{partial_path.as_posix()}?mode=ro", uri=True)) as check:
            result = check.execute("PRAGMA quick_check").fetchone()
            if not result or result[0] != "ok":
                raise BackupError("SQLite quick_check failed")
        manifest = {
            "kind": "jarvis-sqlite-backup",
            "createdAt": utc_now(),
            "sourceDatabase": str(config.database_path),
            "repository": str(config.repository),
            "gitSha": _git_sha(config.repository),
            "alembicRevision": revision,
            "backupFile": final_path.name,
            "sizeBytes": partial_path.stat().st_size,
            "sha256": _sha256(partial_path),
        }
        atomic_write_json(final_path.with_suffix(".json"), manifest)
        os.replace(partial_path, final_path)
        removed = prune_backups(config)
        manifest["retentionRemoved"] = [item.name for item in removed]
        atomic_write_json(config.runtime_home / "last-backup.json", manifest)
        return manifest
    except BaseException:
        try:
            partial_path.unlink()
        except FileNotFoundError:
            pass
        final_path.unlink(missing_ok=True)
        final_path.with_suffix(".json").unlink(missing_ok=True)
        raise


def last_backup(config: SupervisorConfig) -> dict[str, Any] | None:
    return read_json(config.runtime_home / "last-backup.json")
