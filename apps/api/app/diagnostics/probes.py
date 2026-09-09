"""Read-only probes used by the runtime doctor.

Every probe is bounded (short timeouts), read-only, and never mutates
application state, starts processes, or performs model inference.
"""

from __future__ import annotations

import socket
import sqlite3
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from alembic.script import ScriptDirectory
from sqlalchemy.engine import make_url

from app.runtime_supervisor.health import HealthResult, probe_http

HttpProbe = Callable[[str, bool, float], HealthResult]
"""probe(url, expect_json, timeout) -> HealthResult"""

DEFAULT_HTTP_TIMEOUT = 2.0
DEFAULT_CONNECT_TIMEOUT = 1.5


def default_http_probe(url: str, expect_json: bool, timeout: float) -> HealthResult:
    return probe_http(url, expect_json=expect_json, timeout=timeout)


def tcp_connect(url: str, timeout: float = DEFAULT_CONNECT_TIMEOUT) -> tuple[bool, str | None]:
    """Return whether anything accepts TCP connections on the URL's port.

    This distinguishes "nothing is listening" from "the port is occupied by a
    process that does not answer the expected service" without identifying or
    touching the owning process.
    """

    try:
        parsed = urlsplit(url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        return False, f"invalid endpoint: {exc.__class__.__name__}"
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect((host, port))
    except OSError as exc:
        return False, exc.__class__.__name__
    return True, None


@dataclass(frozen=True)
class GitFacts:
    sha: str | None
    dirty: bool | None
    available: bool


def inspect_git(repository: Path) -> GitFacts:
    def run(*args: str) -> str | None:
        try:
            return subprocess.run(
                ["git", *args],
                cwd=repository,
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None

    sha = run("rev-parse", "HEAD")
    status = run("status", "--porcelain")
    if sha is None and status is None:
        return GitFacts(sha=None, dirty=None, available=False)
    return GitFacts(sha=sha or None, dirty=bool(status), available=True)


def alembic_heads(migrations_directory: Path) -> set[str]:
    """Return the revision heads declared by the checked-out migration scripts."""

    if not migrations_directory.is_dir():
        return set()
    try:
        script = ScriptDirectory(str(migrations_directory))
    except Exception:
        return set()
    try:
        return set(script.get_heads())
    except Exception:
        return set()


def sqlite_path_from_url(repository: Path, database_url: str) -> Path | None:
    """Resolve the configured SQLite database file, or None when unsupported."""

    try:
        url = make_url(database_url)
    except Exception:
        return None
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        return None
    raw = url.database
    if raw.startswith("file:"):
        from urllib.parse import unquote

        parsed = urlsplit(raw)
        raw = unquote(parsed.path)
    if raw.startswith("/") and len(raw) > 3 and raw[2] == ":":
        raw = raw[1:]
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = repository / "apps" / "api" / candidate
    return candidate.resolve()


@dataclass
class SqliteFacts:
    """Read-only observations about the configured SQLite database."""

    path: Path
    exists: bool = False
    openable: bool = False
    open_error: str | None = None
    revision: str | None = None
    revision_error: str | None = None
    core_tables_present: bool = False
    missing_core_tables: list[str] = field(default_factory=list)
    integrity: str | None = None
    integrity_error: str | None = None
    system_state_readable: bool = False
    emergency_stop: bool | None = None
    lease_counts: dict[str, int] | None = None
    worker_hints: dict[str, int] | None = None

    #: Core tables required for the durable control plane on a current schema.
    CORE_TABLES: tuple[str, ...] = (
        "alembic_version",
        "system_state",
        "agents",
        "tasks",
        "task_leases",
        "model_executions",
    )


def inspect_sqlite(path: Path, *, deep: bool, core_tables: tuple[str, ...] = ()) -> SqliteFacts:
    """Open the database read-only and gather bounded, non-mutating facts.

    The connection string always uses ``mode=ro`` so the doctor cannot create
    or modify a database merely by inspecting it. WAL sidecars are respected by
    SQLite automatically for readers.
    """

    tables = core_tables or SqliteFacts.CORE_TABLES
    facts = SqliteFacts(path=path)
    facts.exists = path.is_file()
    if not facts.exists:
        facts.open_error = "database file does not exist"
        return facts
    try:
        uri = f"file:{path.as_posix()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=5)
    except sqlite3.Error as exc:
        facts.open_error = exc.__class__.__name__
        return facts
    try:
        connection.row_factory = sqlite3.Row
        facts.openable = True
        _read_schema_facts(connection, facts, tables, deep=deep)
        _read_runtime_facts(connection, facts)
    finally:
        connection.close()
    return facts


def _read_schema_facts(
    connection: sqlite3.Connection,
    facts: SqliteFacts,
    core_tables: tuple[str, ...],
    *,
    deep: bool,
) -> None:
    try:
        names = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    except sqlite3.Error as exc:
        facts.revision_error = exc.__class__.__name__
        return
    facts.missing_core_tables = [name for name in core_tables if name not in names]
    facts.core_tables_present = not facts.missing_core_tables
    if "alembic_version" not in names:
        facts.revision_error = "alembic_version table is missing"
        return
    try:
        row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        facts.revision = row[0] if row is not None else None
    except sqlite3.Error as exc:
        facts.revision_error = exc.__class__.__name__
        return
    if deep:
        try:
            row = connection.execute("PRAGMA quick_check").fetchone()
            facts.integrity = row[0] if row is not None else None
        except sqlite3.Error as exc:
            facts.integrity_error = exc.__class__.__name__


def _read_runtime_facts(connection: sqlite3.Connection, facts: SqliteFacts) -> None:
    try:
        row = connection.execute("SELECT emergency_stop FROM system_state WHERE id = 1").fetchone()
    except sqlite3.Error:
        return
    if row is None:
        return
    facts.system_state_readable = True
    facts.emergency_stop = bool(row[0])
    facts.lease_counts = _lease_counts(connection)
    facts.worker_hints = _worker_hints(connection)


def _lease_counts(connection: sqlite3.Connection) -> dict[str, int] | None:
    if "task_leases" not in _table_names(connection):
        return None
    try:
        leases = connection.execute("SELECT COUNT(*) FROM task_leases").fetchone()[0]
    except sqlite3.Error:
        return None
    return {"activeLeaseCount": int(leases)}


def _worker_hints(connection: sqlite3.Connection) -> dict[str, int] | None:
    names = _table_names(connection)
    if "workers" not in names:
        return None
    try:
        active = connection.execute(
            "SELECT COUNT(*) FROM workers WHERE status = 'active'"
        ).fetchone()[0]
    except sqlite3.Error:
        return None
    return {"activeWorkerCount": int(active)}


def _table_names(connection: sqlite3.Connection) -> set[str]:
    try:
        return {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    except sqlite3.Error:
        return set()
