# SQLite Backup Integrity Verification

The Jarvis runtime supervisor provides read-only verification of local SQLite backups to confirm that existing database backups are internally consistent, uncorrupted, and match their recorded JSON manifests.

## Overview

A backup file existing on disk does not guarantee that it is readable, valid, or free from corruption or tampering. The backup verification module performs read-only inspection and integrity validation without altering application state, modifying the live database, or restoring data.

Verification is exposed via the supervisor CLI (`app.runtime_supervisor`) and PowerShell wrapper (`scripts/jarvis.ps1`).

## Verification Checks

When a backup is verified, the runtime supervisor performs the following sequential checks:

1. **Runtime Home Verification**: Ensures the supervisor runtime home is marker-verified for the current repository installation (`.jarvis-supervisor-runtime.json`).
2. **Path Security & Boundary**: Confirms the backup file and manifest path reside directly inside the verified `backups/` directory, are regular files, and are not symbolic links or junction points.
3. **File Existence**: Verifies that the backup file exists on disk.
4. **Filename Pattern**: Confirms the filename follows the approved pattern (`jarvis-<timestamp>.sqlite3`).
5. **Manifest Presence & Structural Validity**: Confirms the matching JSON manifest (`jarvis-<timestamp>.json`) exists, contains required schema fields (`kind`, `createdAt`, `backupFile`, `sizeBytes`, `sha256`), and identifies as `jarvis-sqlite-backup`.
6. **Filename Consistency**: Validates that `backupFile` in the manifest matches the actual filename.
7. **Size Matching**: Confirms the file size on disk matches `sizeBytes` recorded in the manifest.
8. **SHA-256 Digest**: Computes a streaming SHA-256 hash of the backup file and validates it against the recorded `sha256` digest.
9. **SQLite Read-Only Open**: Opens the backup database in read-only mode (`?mode=ro`).
10. **SQLite Integrity Check**: Executes `PRAGMA quick_check` against the SQLite database connection to verify database page structure and index integrity.
11. **Alembic Revision Consistency**: Reads `alembic_version` from the backup database and compares it with `alembicRevision` stored in the manifest.

## Operator CLI Usage

### PowerShell Wrapper (`scripts/jarvis.ps1`)

Verify the latest backup:

```powershell
.\scripts\jarvis.ps1 verify-backup
```

Verify all backups in the runtime home:

```powershell
.\scripts\jarvis.ps1 verify-backup --all
```

Verify a specific backup file:

```powershell
.\scripts\jarvis.ps1 verify-backup --target jarvis-20260101T000000.000000Z.sqlite3
```

Output machine-readable JSON:

```powershell
.\scripts\jarvis.ps1 verify-backup --json
.\scripts\jarvis.ps1 verify-backup --all --json
```

### Python Module (`app.runtime_supervisor`)

```bash
python3 -m app.runtime_supervisor verify-backup
python3 -m app.runtime_supervisor verify-backup --all
python3 -m app.runtime_supervisor verify-backup --target jarvis-20260101T000000.000000Z.sqlite3 --json
```

## Status & Error Categories

Verification returns structured status categories indicating success or specific failure reasons:

| Status Code | Description |
| :--- | :--- |
| `valid` | Backup is valid, uncorrupted, and matches its recorded manifest. |
| `missing_backup` | Specified backup file or backup directory does not exist. |
| `unapproved_path` | Target path is outside the backup directory, is a symlink, or fails runtime-home ownership checks. |
| `missing_manifest` | Corresponding JSON manifest file is missing. |
| `invalid_manifest` | Manifest file is unparseable or lacks required metadata fields. |
| `filename_mismatch` | Actual filename does not match `backupFile` in the manifest. |
| `size_mismatch` | Backup file size does not match `sizeBytes` recorded in the manifest. |
| `hash_mismatch` | Computed SHA-256 digest does not match `sha256` recorded in the manifest. |
| `unreadable_sqlite` | SQLite driver cannot open the file or the file is not a valid SQLite database. |
| `integrity_check_failed` | SQLite `PRAGMA quick_check` returned corruption or integrity error messages. |
| `revision_mismatch` | Database Alembic revision does not match `alembicRevision` in the manifest. |

## JSON Output Schema

Single backup verification result:

```json
{
  "alembicRevision": "revision-id",
  "backupFile": "jarvis-20260101T000000.000000Z.sqlite3",
  "backupPath": "/path/to/backups/jarvis-20260101T000000.000000Z.sqlite3",
  "createdAt": "2026-01-01T00:00:00.000000Z",
  "detail": "Backup is internally consistent and matches recorded manifest",
  "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "sizeBytes": 16384,
  "status": "valid",
  "valid": true
}
```

Multi-backup verification summary (`--all`):

```json
{
  "invalidCount": 0,
  "results": [
    {
      "alembicRevision": "revision-id",
      "backupFile": "jarvis-20260101T000000.000000Z.sqlite3",
      "backupPath": "/path/to/backups/jarvis-20260101T000000.000000Z.sqlite3",
      "createdAt": "2026-01-01T00:00:00.000000Z",
      "detail": "Backup is internally consistent and matches recorded manifest",
      "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
      "sizeBytes": 16384,
      "status": "valid",
      "valid": true
    }
  ],
  "status": "valid",
  "totalCount": 1,
  "validCount": 1,
  "valid": true
}
```

## Security & Safety Guarantee

- **Read-Only Operation**: Verification opens database connections exclusively with `mode=ro` (read-only SQLite URI). It never performs writes, updates, deletes, or schema migrations.
- **Path Security**: All targets must reside strictly within `config.backups_directory`. Path traversal attempts (e.g., `../..`) and symlinks are rejected with `unapproved_path`.
- **Runtime Home Ownership**: Refuses execution if `.jarvis-supervisor-runtime.json` does not match the active repository installation.

## Limitations

- `PRAGMA quick_check` verifies internal SQLite page structures and cell pointer offsets. It does not perform domain-level data validation or application logic verification.
- Verification checks backups existing on disk. It does not automatically repair corrupted backups or create new backups if missing.
- Database restore remains a manual operator procedure (see `docs/runtime-supervisor.md`).
