from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.runtime_supervisor.backup import create_backup
from app.runtime_supervisor.backup_verification import (
    STATUS_FILENAME_MISMATCH,
    STATUS_HASH_MISMATCH,
    STATUS_INTEGRITY_CHECK_FAILED,
    STATUS_INVALID_MANIFEST,
    STATUS_MISSING_BACKUP,
    STATUS_MISSING_MANIFEST,
    STATUS_REVISION_MISMATCH,
    STATUS_SIZE_MISMATCH,
    STATUS_UNAPPROVED_PATH,
    STATUS_UNREADABLE_SQLITE,
    STATUS_VALID,
    verify_all_backups,
    verify_backup,
    verify_latest_backup,
)
from app.runtime_supervisor.cli import main as cli_main
from app.runtime_supervisor.io import atomic_write_json, ensure_runtime_home
from tests.test_runtime_supervisor import create_database, make_config


def test_verify_backup_valid(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    ensure_runtime_home(config.runtime_home, config.repository)
    create_database(config)
    manifest = create_backup(config)

    backup_file = str(manifest["backupFile"])
    res = verify_backup(config, backup_file)

    assert res["valid"] is True
    assert res["status"] == STATUS_VALID
    assert res["backupFile"] == backup_file
    assert res["alembicRevision"] == "revision-test"
    assert res["sizeBytes"] == manifest["sizeBytes"]
    assert res["sha256"] == manifest["sha256"]


def test_verify_latest_and_all_backups(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    ensure_runtime_home(config.runtime_home, config.repository)
    create_database(config)
    manifest1 = create_backup(config)

    # Latest verification
    latest_res = verify_latest_backup(config)
    assert latest_res["valid"] is True
    assert latest_res["status"] == STATUS_VALID
    assert latest_res["latest"]["backupFile"] == manifest1["backupFile"]

    # All verification
    all_res = verify_all_backups(config)
    assert all_res["valid"] is True
    assert all_res["totalCount"] == 1
    assert all_res["validCount"] == 1
    assert all_res["invalidCount"] == 0


def test_verify_backup_missing_file(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    ensure_runtime_home(config.runtime_home, config.repository)

    res = verify_backup(config, "jarvis-20260101T000000.000000Z.sqlite3")
    assert res["valid"] is False
    assert res["status"] == STATUS_MISSING_BACKUP


def test_verify_backup_missing_manifest(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    ensure_runtime_home(config.runtime_home, config.repository)
    create_database(config)
    manifest = create_backup(config)

    backup_path = config.backups_directory / str(manifest["backupFile"])
    manifest_path = backup_path.with_suffix(".json")
    manifest_path.unlink()

    res = verify_backup(config, backup_path.name)
    assert res["valid"] is False
    assert res["status"] == STATUS_MISSING_MANIFEST


def test_verify_backup_invalid_manifest_structure(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    ensure_runtime_home(config.runtime_home, config.repository)
    create_database(config)
    manifest = create_backup(config)

    backup_path = config.backups_directory / str(manifest["backupFile"])
    manifest_path = backup_path.with_suffix(".json")
    atomic_write_json(manifest_path, {"kind": "wrong-kind"})

    res = verify_backup(config, backup_path.name)
    assert res["valid"] is False
    assert res["status"] == STATUS_INVALID_MANIFEST


def test_verify_backup_filename_mismatch(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    ensure_runtime_home(config.runtime_home, config.repository)
    create_database(config)
    manifest = create_backup(config)

    backup_path = config.backups_directory / str(manifest["backupFile"])
    manifest_path = backup_path.with_suffix(".json")
    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_data["backupFile"] = "jarvis-different.sqlite3"
    atomic_write_json(manifest_path, manifest_data)

    res = verify_backup(config, backup_path.name)
    assert res["valid"] is False
    assert res["status"] == STATUS_FILENAME_MISMATCH


def test_verify_backup_size_mismatch_and_truncated(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    ensure_runtime_home(config.runtime_home, config.repository)
    create_database(config)
    manifest = create_backup(config)

    backup_path = config.backups_directory / str(manifest["backupFile"])
    # Truncate the backup file
    with backup_path.open("rb+") as stream:
        stream.truncate(10)

    res = verify_backup(config, backup_path.name)
    assert res["valid"] is False
    assert res["status"] == STATUS_SIZE_MISMATCH


def test_verify_backup_hash_mismatch_tampered_content(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    ensure_runtime_home(config.runtime_home, config.repository)
    create_database(config)
    manifest = create_backup(config)

    backup_path = config.backups_directory / str(manifest["backupFile"])

    # Modify single byte in file while preserving exact length
    content = bytearray(backup_path.read_bytes())
    content[0] ^= 0xFF
    backup_path.write_bytes(content)

    res = verify_backup(config, backup_path.name)
    assert res["valid"] is False
    assert res["status"] == STATUS_HASH_MISMATCH


def test_verify_backup_unreadable_sqlite(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    ensure_runtime_home(config.runtime_home, config.repository)
    config.backups_directory.mkdir(parents=True, exist_ok=True)

    backup_path = config.backups_directory / "jarvis-20260101T000000.000000Z.sqlite3"
    content = b"Not a sqlite database content" + b"\x00" * 100
    backup_path.write_bytes(content)

    manifest = {
        "kind": "jarvis-sqlite-backup",
        "createdAt": "2026-01-01T00:00:00Z",
        "backupFile": backup_path.name,
        "sizeBytes": len(content),
        "sha256": "3e9b0e2632b7bc65ec43bd4df821f7e34bfb49e1f5787687ebff3b3e02024b0c",  # arbitrary 64-hex
    }
    import hashlib

    manifest["sha256"] = hashlib.sha256(content).hexdigest()
    atomic_write_json(backup_path.with_suffix(".json"), manifest)

    res = verify_backup(config, backup_path.name)
    assert res["valid"] is False
    assert res["status"] == STATUS_UNREADABLE_SQLITE


def test_verify_backup_integrity_check_failed(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    ensure_runtime_home(config.runtime_home, config.repository)
    create_database(config)
    manifest = create_backup(config)

    backup_path = config.backups_directory / str(manifest["backupFile"])
    manifest_path = backup_path.with_suffix(".json")

    # Corrupt sqlite page header bytes to pass header signature check but fail quick_check
    content = bytearray(backup_path.read_bytes())
    if len(content) > 200:
        content[100] ^= 0xFF
        backup_path.write_bytes(content)

        # Update manifest hash & size so hash check passes and reaches sqlite quick_check
        import hashlib

        manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_data["sha256"] = hashlib.sha256(content).hexdigest()
        manifest_data["sizeBytes"] = len(content)
        atomic_write_json(manifest_path, manifest_data)

        res = verify_backup(config, backup_path.name)
        assert res["valid"] is False
        assert res["status"] in (STATUS_INTEGRITY_CHECK_FAILED, STATUS_UNREADABLE_SQLITE)


def test_verify_backup_alembic_revision_mismatch(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    ensure_runtime_home(config.runtime_home, config.repository)
    create_database(config)
    manifest = create_backup(config)

    backup_path = config.backups_directory / str(manifest["backupFile"])
    manifest_path = backup_path.with_suffix(".json")

    # Update database alembic revision inside the backup
    with sqlite3.connect(backup_path) as conn:
        conn.execute("UPDATE alembic_version SET version_num = 'modified-rev'")

    # Recompute SHA-256 in manifest so hash check passes
    import hashlib

    new_content = backup_path.read_bytes()
    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_data["sha256"] = hashlib.sha256(new_content).hexdigest()
    manifest_data["sizeBytes"] = len(new_content)
    atomic_write_json(manifest_path, manifest_data)

    res = verify_backup(config, backup_path.name)
    assert res["valid"] is False
    assert res["status"] == STATUS_REVISION_MISMATCH
    assert res["actualAlembicRevision"] == "modified-rev"
    assert res["recordedAlembicRevision"] == "revision-test"


def test_verify_backup_path_security_symlink_and_directory_escape(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    ensure_runtime_home(config.runtime_home, config.repository)
    config.backups_directory.mkdir(parents=True, exist_ok=True)

    # Outside backup file
    outside = tmp_path / "outside.sqlite3"
    outside.write_bytes(b"data")

    # Attempt directory traversal or relative path outside
    res = verify_backup(config, "../../outside.sqlite3")
    assert res["valid"] is False
    assert res["status"] == STATUS_UNAPPROVED_PATH

    # Symlink inside backup dir pointing outside
    symlink_path = config.backups_directory / "jarvis-symlink.sqlite3"
    try:
        symlink_path.symlink_to(outside)
        res = verify_backup(config, symlink_path.name)
        assert res["valid"] is False
        assert res["status"] == STATUS_UNAPPROVED_PATH
    except OSError:
        pass  # On Windows without symlink privileges, skip symlink creation part


def test_cli_verify_backup_commands(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    config = make_config(tmp_path)
    monkeypatch.setenv("JARVIS_SUPERVISOR_RUNTIME_HOME", str(config.runtime_home))
    ensure_runtime_home(config.runtime_home, config.repository)
    create_database(config)
    manifest = create_backup(config)

    # CLI test single latest backup
    exit_code = cli_main(["--repository", str(config.repository), "--json", "verify-backup"])
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["valid"] is True
    assert payload["status"] == STATUS_VALID

    # CLI test all backups
    exit_code = cli_main(
        ["--repository", str(config.repository), "--json", "verify-backup", "--all"]
    )
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["valid"] is True
    assert payload["totalCount"] == 1

    # CLI test targeted file
    exit_code = cli_main(
        [
            "--repository",
            str(config.repository),
            "--json",
            "verify-backup",
            "--target",
            str(manifest["backupFile"]),
        ]
    )
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["valid"] is True

    # CLI test human readable text output
    exit_code = cli_main(["--repository", str(config.repository), "verify-backup", "--all"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Backup Verification Summary: valid=True" in out
