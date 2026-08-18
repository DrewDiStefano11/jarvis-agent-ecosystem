from __future__ import annotations

import hashlib
import ipaddress
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}


class SupervisorConfigurationError(ValueError):
    pass


def _is_loopback_host(value: str) -> bool:
    normalized = value.rstrip(".").lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _url_host(value: str) -> str:
    return f"[{value}]" if ":" in value and not value.startswith("[") else value


def _parse_env_file(path: Path) -> dict[str, str]:
    """Read simple dotenv assignments without interpolation or evaluation."""

    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for number, raw_line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not key.replace("_", "a").isalnum() or not key[0].isalpha():
            raise SupervisorConfigurationError(f"invalid environment assignment at {path}:{number}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def load_environment(repository: Path, environ: dict[str, str] | None = None) -> dict[str, str]:
    values: dict[str, str] = {}
    values.update(_parse_env_file(repository / ".env"))
    values.update(_parse_env_file(repository / "apps" / "api" / ".env"))
    values.update(_parse_env_file(repository / "apps" / "web" / ".env"))
    values.update(dict(os.environ if environ is None else environ))
    return values


def _bool(values: dict[str, str], name: str, default: bool) -> bool:
    raw = values.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    raise SupervisorConfigurationError(f"{name} must be true or false")


def _int(values: dict[str, str], name: str, default: int, minimum: int, maximum: int) -> int:
    raw = values.get(name)
    try:
        result = default if raw is None else int(raw)
    except ValueError as exc:
        raise SupervisorConfigurationError(f"{name} must be an integer") from exc
    if not minimum <= result <= maximum:
        raise SupervisorConfigurationError(f"{name} must be between {minimum} and {maximum}")
    return result


def _float(
    values: dict[str, str], name: str, default: float, minimum: float, maximum: float
) -> float:
    raw = values.get(name)
    try:
        result = default if raw is None else float(raw)
    except ValueError as exc:
        raise SupervisorConfigurationError(f"{name} must be a number") from exc
    if not minimum <= result <= maximum:
        raise SupervisorConfigurationError(f"{name} must be between {minimum} and {maximum}")
    return result


def _require_loopback_url(value: str, name: str, schemes: set[str]) -> str:
    parsed = urlsplit(value)
    normalized_host = parsed.hostname.rstrip(".").lower() if parsed.hostname else ""
    loopback = _is_loopback_host(normalized_host)
    if (
        parsed.scheme not in schemes
        or not loopback
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise SupervisorConfigurationError(f"{name} must be a credential-free loopback URL")
    return value.rstrip("/")


def _owned_service_url(value: str, name: str, host: str, port: int) -> str:
    configured = _require_loopback_url(value, name, {"http"})
    parsed = urlsplit(configured)
    configured_host = parsed.hostname.rstrip(".").lower() if parsed.hostname else ""
    try:
        configured_port = parsed.port or 80
    except ValueError as exc:
        raise SupervisorConfigurationError(f"{name} contains an invalid port") from exc
    if (
        configured_host != host.rstrip(".").lower()
        or configured_port != port
        or parsed.path not in {"", "/"}
    ):
        raise SupervisorConfigurationError(
            f"{name} must match the supervisor's launched host and port"
        )
    return f"http://{_url_host(host)}:{port}"


def _owned_frontend_url(
    value: str,
    name: str,
    scheme: str,
    host: str,
    port: int,
    path: str,
) -> str:
    configured = _require_loopback_url(value, name, {scheme})
    parsed = urlsplit(configured)
    configured_host = parsed.hostname.rstrip(".").lower() if parsed.hostname else ""
    try:
        configured_port = parsed.port or (443 if scheme in {"https", "wss"} else 80)
    except ValueError as exc:
        raise SupervisorConfigurationError(f"{name} contains an invalid port") from exc
    if (
        configured_host != host.rstrip(".").lower()
        or configured_port != port
        or parsed.path.rstrip("/") != path.rstrip("/")
    ):
        raise SupervisorConfigurationError(f"{name} must target the supervisor's owned endpoint")
    suffix = path if path.startswith("/") else f"/{path}" if path else ""
    return f"{scheme}://{_url_host(host)}:{port}{suffix}"


def _safe_runtime_home(repository: Path, values: dict[str, str]) -> Path:
    configured = values.get("JARVIS_SUPERVISOR_RUNTIME_HOME", "").strip()
    if configured:
        configured_path = Path(configured).expanduser()
        if not configured_path.is_absolute():
            raise SupervisorConfigurationError("JARVIS_SUPERVISOR_RUNTIME_HOME must be absolute")
        result = configured_path.resolve()
    else:
        base = values.get("LOCALAPPDATA") or values.get("XDG_STATE_HOME")
        if not base:
            base = str(Path.home() / ".local" / "state")
        install_id = hashlib.sha256(str(repository).lower().encode("utf-8")).hexdigest()[:12]
        result = (Path(base) / "Jarvis" / "Supervisor" / install_id).resolve()
    anchor = Path(result.anchor).resolve()
    if result == anchor or result == repository or repository in result.parents:
        raise SupervisorConfigurationError(
            "JARVIS_SUPERVISOR_RUNTIME_HOME cannot be a filesystem root or inside the repository"
        )
    return result


def _sqlite_path(repository: Path, value: str) -> Path:
    try:
        url = make_url(value)
    except ArgumentError as exc:
        raise SupervisorConfigurationError("JARVIS_DATABASE_URL is invalid") from exc
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        raise SupervisorConfigurationError(
            "the runtime supervisor requires a file-backed SQLite URL"
        )
    raw_path = unquote(url.database)
    if raw_path.startswith("/") and len(raw_path) > 3 and raw_path[2] == ":":
        raw_path = raw_path[1:]
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = repository / "apps" / "api" / candidate
    return candidate.resolve()


@dataclass(frozen=True)
class SupervisorConfig:
    repository: Path
    runtime_home: Path
    python_executable: Path
    node_executable: Path | None
    api_url: str
    web_url: str
    api_host: str
    api_port: int
    web_host: str
    web_port: int
    database_url: str
    database_path: Path
    worker_enabled: bool
    ollama_relevant: bool
    ollama_url: str
    health_interval_seconds: float
    health_failure_limit: int
    startup_timeout_seconds: float
    graceful_shutdown_seconds: float
    restart_initial_seconds: float
    restart_maximum_seconds: float
    backoff_reset_seconds: float
    log_max_bytes: int
    log_backup_count: int
    backup_retention_count: int
    backup_interval_hours: int
    disk_warning_bytes: int
    disk_critical_bytes: int
    environment: dict[str, str]

    @classmethod
    def load(cls, repository: Path, environ: dict[str, str] | None = None) -> SupervisorConfig:
        repository = repository.resolve()
        if not (repository / "apps" / "api" / "app" / "main.py").is_file():
            raise SupervisorConfigurationError("repository does not contain the Jarvis API")
        values = load_environment(repository, environ)
        api_host = values.get("API_HOST", "127.0.0.1").strip()
        web_host = values.get("JARVIS_SUPERVISOR_WEB_HOST", "127.0.0.1").strip()
        if not _is_loopback_host(api_host):
            raise SupervisorConfigurationError("API_HOST must remain loopback-only")
        if not _is_loopback_host(web_host):
            raise SupervisorConfigurationError("JARVIS_SUPERVISOR_WEB_HOST must be loopback-only")
        api_port = _int(values, "API_PORT", 8000, 1, 65535)
        web_port = _int(values, "JARVIS_SUPERVISOR_WEB_PORT", 5173, 1, 65535)
        if api_port == web_port:
            raise SupervisorConfigurationError("API and web ports must differ")
        api_url = _owned_service_url(
            values.get("JARVIS_SUPERVISOR_API_URL", f"http://{_url_host(api_host)}:{api_port}"),
            "JARVIS_SUPERVISOR_API_URL",
            api_host,
            api_port,
        )
        web_url = _owned_service_url(
            values.get("JARVIS_SUPERVISOR_WEB_URL", f"http://{_url_host(web_host)}:{web_port}"),
            "JARVIS_SUPERVISOR_WEB_URL",
            web_host,
            web_port,
        )
        worker_enabled = _bool(values, "JARVIS_AUTONOMOUS_WORKER_ENABLED", False)
        execution_mode = values.get("JARVIS_MODEL_EXECUTION_MODE", "disabled").strip()
        if worker_enabled and execution_mode != "local_only":
            raise SupervisorConfigurationError(
                "enabled autonomous worker requires JARVIS_MODEL_EXECUTION_MODE=local_only"
            )
        if worker_enabled and not values.get("JARVIS_AUTONOMOUS_WORKER_ACTOR_ID", "").strip():
            raise SupervisorConfigurationError(
                "enabled autonomous worker requires JARVIS_AUTONOMOUS_WORKER_ACTOR_ID"
            )
        if worker_enabled and not values.get("JARVIS_AUTONOMOUS_WORKER_INSTANCE_ID", "").strip():
            raise SupervisorConfigurationError(
                "enabled autonomous worker requires JARVIS_AUTONOMOUS_WORKER_INSTANCE_ID"
            )
        ollama_enabled = _bool(values, "JARVIS_MODEL_OLLAMA_ENABLED", False)
        ollama_url = _require_loopback_url(
            values.get("JARVIS_MODEL_OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
            "JARVIS_MODEL_OLLAMA_BASE_URL",
            {"http", "https"},
        )
        web_origin = _owned_frontend_url(
            values.get("WEB_ORIGIN", f"http://{_url_host(web_host)}:{web_port}"),
            "WEB_ORIGIN",
            "http",
            web_host,
            web_port,
            "",
        )
        vite_api = _owned_frontend_url(
            values.get("VITE_API_BASE_URL", f"http://{_url_host(api_host)}:{api_port}"),
            "VITE_API_BASE_URL",
            "http",
            api_host,
            api_port,
            "",
        )
        vite_ws = _owned_frontend_url(
            values.get("VITE_WS_URL", f"ws://{_url_host(api_host)}:{api_port}/ws/events"),
            "VITE_WS_URL",
            "ws",
            api_host,
            api_port,
            "/ws/events",
        )
        python_value = values.get("JARVIS_SUPERVISOR_PYTHON_EXECUTABLE", "").strip()
        if python_value:
            api_python = Path(python_value).expanduser()
            if not api_python.is_absolute():
                raise SupervisorConfigurationError(
                    "JARVIS_SUPERVISOR_PYTHON_EXECUTABLE must be an absolute path"
                )
        elif os.name == "nt":
            api_python = repository / "apps" / "api" / ".venv" / "Scripts" / "python.exe"
        else:
            api_python = repository / "apps" / "api" / ".venv" / "bin" / "python"
        node_name = "node.exe" if os.name == "nt" else "node"
        node_value = values.get("JARVIS_SUPERVISOR_NODE_EXECUTABLE", "").strip()
        node = Path(node_value).expanduser() if node_value else None
        if node is not None and not node.is_absolute():
            raise SupervisorConfigurationError(
                "JARVIS_SUPERVISOR_NODE_EXECUTABLE must be an absolute path"
            )
        if node is None:
            resolved_node = shutil.which(node_name) or shutil.which("node")
            node = Path(resolved_node) if resolved_node else None
        database_url = values.get("JARVIS_DATABASE_URL", "sqlite:///./data/jarvis.db").strip()
        environment = dict(values)
        environment["API_HOST"] = api_host
        environment["API_PORT"] = str(api_port)
        environment["WEB_ORIGIN"] = web_origin
        environment["VITE_API_BASE_URL"] = vite_api
        environment["VITE_WS_URL"] = vite_ws
        return cls(
            repository=repository,
            runtime_home=_safe_runtime_home(repository, values),
            python_executable=api_python.resolve(),
            node_executable=node.resolve() if node else None,
            api_url=api_url,
            web_url=web_url,
            api_host=api_host,
            api_port=api_port,
            web_host=web_host,
            web_port=web_port,
            database_url=database_url,
            database_path=_sqlite_path(repository, database_url),
            worker_enabled=worker_enabled,
            ollama_relevant=ollama_enabled,
            ollama_url=ollama_url,
            health_interval_seconds=_float(
                values, "JARVIS_SUPERVISOR_HEALTH_INTERVAL_SECONDS", 5, 0.1, 300
            ),
            health_failure_limit=_int(values, "JARVIS_SUPERVISOR_HEALTH_FAILURE_LIMIT", 3, 1, 100),
            startup_timeout_seconds=_float(
                values, "JARVIS_SUPERVISOR_STARTUP_TIMEOUT_SECONDS", 60, 1, 600
            ),
            graceful_shutdown_seconds=_float(
                values, "JARVIS_SUPERVISOR_GRACEFUL_SHUTDOWN_SECONDS", 20, 1, 300
            ),
            restart_initial_seconds=_float(
                values, "JARVIS_SUPERVISOR_RESTART_INITIAL_SECONDS", 1, 0.1, 300
            ),
            restart_maximum_seconds=_float(
                values, "JARVIS_SUPERVISOR_RESTART_MAXIMUM_SECONDS", 300, 1, 3600
            ),
            backoff_reset_seconds=_float(
                values, "JARVIS_SUPERVISOR_BACKOFF_RESET_SECONDS", 300, 1, 86400
            ),
            log_max_bytes=_int(
                values, "JARVIS_SUPERVISOR_LOG_MAX_BYTES", 5_242_880, 65_536, 1_073_741_824
            ),
            log_backup_count=_int(values, "JARVIS_SUPERVISOR_LOG_BACKUP_COUNT", 5, 1, 100),
            backup_retention_count=_int(
                values, "JARVIS_SUPERVISOR_BACKUP_RETENTION_COUNT", 7, 1, 365
            ),
            backup_interval_hours=_int(
                values, "JARVIS_SUPERVISOR_BACKUP_INTERVAL_HOURS", 24, 0, 8760
            ),
            disk_warning_bytes=_int(
                values,
                "JARVIS_SUPERVISOR_DISK_WARNING_BYTES",
                1_073_741_824,
                1_048_576,
                10_995_116_277_760,
            ),
            disk_critical_bytes=_int(
                values,
                "JARVIS_SUPERVISOR_DISK_CRITICAL_BYTES",
                268_435_456,
                1_048_576,
                10_995_116_277_760,
            ),
            environment=environment,
        ).validated()

    def validated(self) -> SupervisorConfig:
        if self.restart_initial_seconds > self.restart_maximum_seconds:
            raise SupervisorConfigurationError("initial restart delay cannot exceed maximum")
        if self.disk_critical_bytes > self.disk_warning_bytes:
            raise SupervisorConfigurationError(
                "critical disk threshold cannot exceed warning threshold"
            )
        return self

    @property
    def api_directory(self) -> Path:
        return self.repository / "apps" / "api"

    @property
    def web_directory(self) -> Path:
        return self.repository / "apps" / "web"

    @property
    def logs_directory(self) -> Path:
        return self.runtime_home / "logs"

    @property
    def backups_directory(self) -> Path:
        return self.runtime_home / "backups"

    @property
    def state_path(self) -> Path:
        return self.runtime_home / "state.json"

    @property
    def lock_path(self) -> Path:
        return self.runtime_home / "supervisor.lock"

    @property
    def stop_request_path(self) -> Path:
        return self.runtime_home / "stop-request.json"

    @property
    def task_name(self) -> str:
        digest = hashlib.sha256(str(self.repository).lower().encode("utf-8")).hexdigest()[:12]
        return f"JarvisSupervisor-{digest}"
