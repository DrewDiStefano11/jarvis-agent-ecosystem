from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from app.runtime_supervisor.backup import BACKUP_PREFIX, BACKUP_SUFFIX, _alembic_revision
from app.runtime_supervisor.config import SupervisorConfig
from app.runtime_supervisor.io import read_json, verified_runtime_home

STATUS_VALID = "valid"
STATUS_MISSING_BACKUP = "missing_backup"
STATUS_UNAPPROVED_PATH = "unapproved_path"
STATUS_MISSING_MANIFEST = "missing_manifest"
STATUS_INVALID_MANIFEST = "invalid_manifest"
STATUS_FILENAME_MISMATCH = "filename_mismatch"
STATUS_SIZE_MISMATCH = "size_mismatch"
STATUS_HASH_MISMATCH = "hash_mismatch"
STATUS_UNREADABLE_SQLITE = "unreadable_sqlite"
STATUS_INTEGRITY_CHECK_FAILED = "integrity_check_failed"
STATUS_REVISION_MISMATCH = "revision_mismatch"


def _compute_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_safe_path(target: Path, allowed_directory: Path) -> bool:
    try:
        resolved_directory = allowed_directory.resolve()
        resolved_target = target.resolve()
        return (
            resolved_target.parent == resolved_directory
            and not target.is_symlink()
            and not resolved_target.is_symlink()
        )
    except OSError:
        return False


def verify_backup(config: SupervisorConfig, target: str | Path) -> dict[str, Any]:
    """Verify a single SQLite backup file and its manifest in a completely read-only manner."""
    directory = config.backups_directory.resolve()

    if not verified_runtime_home(config.runtime_home, config.repository):
        return {
            "status": STATUS_UNAPPROVED_PATH,
            "valid": False,
            "detail": "Runtime home is not verified for this repository installation",
            "backupFile": str(target) if isinstance(target, str) else target.name,
        }

    if isinstance(target, str):
        target_path = Path(target)
        backup_path = target_path if target_path.is_absolute() else directory / target_path
    else:
        backup_path = target

    backup_name = backup_path.name

    result_base: dict[str, Any] = {
        "backupFile": backup_name,
        "backupPath": str(backup_path),
        "valid": False,
    }

    if not _is_safe_path(backup_path, directory):
        return {
            **result_base,
            "status": STATUS_UNAPPROVED_PATH,
            "detail": "Backup path must be a regular file directly inside the verified backup directory and not a symlink",
        }

    if not backup_path.exists():
        return {
            **result_base,
            "status": STATUS_MISSING_BACKUP,
            "detail": f"Backup file does not exist: {backup_name}",
        }

    if not backup_path.is_file():
        return {
            **result_base,
            "status": STATUS_UNAPPROVED_PATH,
            "detail": f"Backup target is not a regular file: {backup_name}",
        }

    if not (backup_name.startswith(BACKUP_PREFIX) and backup_name.endswith(BACKUP_SUFFIX)):
        return {
            **result_base,
            "status": STATUS_UNAPPROVED_PATH,
            "detail": f"Backup filename does not match expected prefix/suffix pattern: {backup_name}",
        }

    manifest_path = backup_path.with_suffix(".json")

    if not _is_safe_path(manifest_path, directory) or not manifest_path.exists():
        return {
            **result_base,
            "status": STATUS_MISSING_MANIFEST,
            "detail": f"Backup manifest file is missing: {manifest_path.name}",
        }

    manifest = read_json(manifest_path)
    if (
        not manifest
        or manifest.get("kind") != "jarvis-sqlite-backup"
        or "backupFile" not in manifest
        or "sizeBytes" not in manifest
        or "sha256" not in manifest
    ):
        return {
            **result_base,
            "status": STATUS_INVALID_MANIFEST,
            "detail": f"Backup manifest is invalid or structurally incomplete: {manifest_path.name}",
        }

    recorded_filename = manifest.get("backupFile")
    if recorded_filename != backup_name:
        return {
            **result_base,
            "status": STATUS_FILENAME_MISMATCH,
            "detail": f"Manifest recorded filename ({recorded_filename}) does not match actual file ({backup_name})",
            "recordedFilename": recorded_filename,
        }

    actual_size = backup_path.stat().st_size
    recorded_size = manifest.get("sizeBytes")
    if actual_size != recorded_size:
        return {
            **result_base,
            "status": STATUS_SIZE_MISMATCH,
            "detail": f"Actual file size ({actual_size} bytes) does not match manifest recorded size ({recorded_size} bytes)",
            "actualSizeBytes": actual_size,
            "recordedSizeBytes": recorded_size,
        }

    actual_sha256 = _compute_sha256(backup_path)
    recorded_sha256 = manifest.get("sha256")
    if actual_sha256 != recorded_sha256:
        return {
            **result_base,
            "status": STATUS_HASH_MISMATCH,
            "detail": f"Computed SHA-256 ({actual_sha256}) does not match manifest recorded digest ({recorded_sha256})",
            "actualSha256": actual_sha256,
            "recordedSha256": recorded_sha256,
        }

    # Verify SQLite opens read-only and passes PRAGMA quick_check
    uri_path = f"{backup_path.resolve().as_uri()}?mode=ro"
    try:
        with closing(sqlite3.connect(uri_path, uri=True, timeout=10)) as connection:
            try:
                check_row = connection.execute("PRAGMA quick_check").fetchone()
            except sqlite3.DatabaseError as exc:
                msg = str(exc).lower()
                if "not a database" in msg or "encrypted" in msg or "malformed" in msg:
                    return {
                        **result_base,
                        "status": STATUS_UNREADABLE_SQLITE,
                        "detail": f"SQLite file structure is unreadable or not a database: {exc}",
                    }
                return {
                    **result_base,
                    "status": STATUS_INTEGRITY_CHECK_FAILED,
                    "detail": f"SQLite PRAGMA quick_check query failed: {exc}",
                }

            if not check_row or check_row[0] != "ok":
                reason = check_row[0] if check_row else "empty result"
                return {
                    **result_base,
                    "status": STATUS_INTEGRITY_CHECK_FAILED,
                    "detail": f"SQLite quick_check failed: {reason}",
                }

            actual_revision = _alembic_revision(connection)
    except sqlite3.Error as exc:
        return {
            **result_base,
            "status": STATUS_UNREADABLE_SQLITE,
            "detail": f"Failed to open database file with SQLite read-only mode: {exc}",
        }

    recorded_revision = manifest.get("alembicRevision")
    if recorded_revision != actual_revision:
        return {
            **result_base,
            "status": STATUS_REVISION_MISMATCH,
            "detail": f"Stored Alembic revision in database ({actual_revision}) does not match manifest ({recorded_revision})",
            "actualAlembicRevision": actual_revision,
            "recordedAlembicRevision": recorded_revision,
        }

    return {
        **result_base,
        "status": STATUS_VALID,
        "valid": True,
        "detail": "Backup is internally consistent and matches recorded manifest",
        "sizeBytes": actual_size,
        "sha256": actual_sha256,
        "alembicRevision": actual_revision,
        "createdAt": manifest.get("createdAt"),
    }


def verify_latest_backup(config: SupervisorConfig) -> dict[str, Any]:
    """Verify the latest available SQLite backup in the supervisor backup directory."""
    directory = config.backups_directory.resolve()
    if not directory.exists():
        return {
            "status": STATUS_MISSING_BACKUP,
            "valid": False,
            "detail": "Backup directory does not exist",
            "backupsChecked": 0,
        }

    candidates = sorted(
        [
            item
            for item in directory.iterdir()
            if _is_safe_path(item, directory)
            and item.name.startswith(BACKUP_PREFIX)
            and item.name.endswith(BACKUP_SUFFIX)
            and item.is_file()
        ],
        key=lambda item: item.name,
        reverse=True,
    )

    if not candidates:
        return {
            "status": STATUS_MISSING_BACKUP,
            "valid": False,
            "detail": "No backup files found in backup directory",
            "backupsChecked": 0,
        }

    latest = candidates[0]
    verification = verify_backup(config, latest)
    return {
        "status": verification["status"],
        "valid": verification["valid"],
        "latest": verification,
        "backupsChecked": 1,
    }


def verify_all_backups(config: SupervisorConfig) -> dict[str, Any]:
    """Verify all SQLite backup files found in the supervisor backup directory."""
    directory = config.backups_directory.resolve()
    if not directory.exists():
        return {
            "status": STATUS_MISSING_BACKUP,
            "valid": False,
            "detail": "Backup directory does not exist",
            "results": [],
            "totalCount": 0,
            "validCount": 0,
            "invalidCount": 0,
        }

    candidates = sorted(
        [
            item
            for item in directory.iterdir()
            if _is_safe_path(item, directory)
            and item.name.startswith(BACKUP_PREFIX)
            and item.name.endswith(BACKUP_SUFFIX)
            and item.is_file()
        ],
        key=lambda item: item.name,
        reverse=True,
    )

    results = [verify_backup(config, candidate) for candidate in candidates]
    valid_count = sum(1 for r in results if r.get("valid"))
    invalid_count = len(results) - valid_count
    overall_valid = len(results) > 0 and invalid_count == 0

    return {
        "status": STATUS_VALID
        if overall_valid
        else (STATUS_MISSING_BACKUP if not results else "verification_failed"),
        "valid": overall_valid,
        "results": results,
        "totalCount": len(results),
        "validCount": valid_count,
        "invalidCount": invalid_count,
    }
