"""Build the doctor's operating context from repository configuration.

The doctor must stay useful exactly when configuration is broken: an invalid
``.env`` is a diagnostic finding, not a crash. ``build_context`` therefore
always returns a context; configuration failures are recorded on it and
reported by the configuration check.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.core.config import Settings
from app.diagnostics.probes import (
    DEFAULT_HTTP_TIMEOUT,
    HttpProbe,
    default_http_probe,
    inspect_git,
    sqlite_path_from_url,
)
from app.diagnostics.redaction import collect_secret_values
from app.runtime_supervisor.config import (
    SupervisorConfig,
    SupervisorConfigurationError,
    SupervisorCoordination,
    load_environment,
)
from app.runtime_supervisor.status import load_recorded_status

Clock = Callable[[], datetime]

DEFAULT_API_URL = "http://127.0.0.1:8000"
DEFAULT_WEB_URL = "http://127.0.0.1:5173"
DEFAULT_DATABASE_URL = "sqlite:///./data/jarvis.db"


def _truncate_error(value: str) -> str:
    return value[:500]


@dataclass
class DiagnosticsContext:
    """Everything a doctor check may rely on, with injectable probes."""

    repository: Path
    environment: Mapping[str, str]
    api_url: str
    web_url: str
    database_url: str
    database_path: Path | None
    deep: bool
    http_timeout: float
    settings: Settings | None
    settings_error: str | None = None
    supervisor_error: str | None = None
    supervisor_status: dict[str, Any] = field(default_factory=dict)
    git_sha: str | None = None
    git_dirty: bool | None = None
    git_available: bool = False
    http_probe: HttpProbe = default_http_probe
    clock: Clock = lambda: datetime.now(UTC)
    secret_values: tuple[str, ...] = ()
    _state: dict[str, Any] = field(default_factory=dict)

    def probe_json(self, url: str) -> Any:
        """Probe a JSON endpoint and remember the result for later checks."""

        result = self.http_probe(url, True, self.http_timeout)
        self._state.setdefault("http_results", {})[url] = result
        return result

    def probe_plain(self, url: str) -> Any:
        result = self.http_probe(url, False, self.http_timeout)
        self._state.setdefault("http_results", {})[url] = result
        return result

    def remember(self, key: str, value: Any) -> None:
        self._state[key] = value

    def recalled(self, key: str) -> Any:
        return self._state.get(key)

    def utc_now(self) -> str:
        return self.clock().isoformat().replace("+00:00", "Z")

    @property
    def worker_configured(self) -> bool:
        if self.settings is None:
            raw = self.environment.get("JARVIS_AUTONOMOUS_WORKER_ENABLED", "false")
            return raw.strip().lower() in {"1", "true", "yes", "on"}
        return self.settings.autonomous_worker_enabled


def build_context(
    repository: Path,
    *,
    environ: Mapping[str, str] | None = None,
    deep: bool = False,
    http_probe: HttpProbe | None = None,
    clock: Clock | None = None,
    http_timeout: float = DEFAULT_HTTP_TIMEOUT,
) -> DiagnosticsContext:
    repository = repository.resolve()
    environment = load_environment(repository, None if environ is None else dict(environ))

    settings: Settings | None = None
    settings_error: str | None = None
    try:
        settings = Settings(_env_file=None, **environment)
    except ValidationError as exc:
        settings_error = "; ".join(
            str(item["msg"]) for item in exc.errors(include_input=False, include_url=False)
        )
    except (TypeError, ValueError) as exc:
        settings_error = _truncate_error(str(exc))

    supervisor_error: str | None = None
    api_url = DEFAULT_API_URL
    web_url = DEFAULT_WEB_URL
    database_url = environment.get("JARVIS_DATABASE_URL", DEFAULT_DATABASE_URL).strip()
    database_path: Path | None = None
    try:
        config = SupervisorConfig.load(repository, dict(environment))
        api_url = config.api_url
        web_url = config.web_url
        database_url = config.database_url
        database_path = config.database_path
    except SupervisorConfigurationError as exc:
        supervisor_error = _truncate_error(str(exc))
        # Fall back to raw endpoint configuration so later checks can still
        # distinguish "not running" from "wrong URL".
        api_url = environment.get("JARVIS_SUPERVISOR_API_URL", api_url).rstrip("/")
        web_url = environment.get("JARVIS_SUPERVISOR_WEB_URL", web_url).rstrip("/")

    supervisor_status: dict[str, Any] = {}
    try:
        coordination = SupervisorCoordination.load(repository, dict(environment))
        supervisor_status = dict(load_recorded_status(coordination))
    except (SupervisorConfigurationError, OSError):
        supervisor_status = {}

    if database_path is None:
        database_path = sqlite_path_from_url(repository, database_url)
    git = inspect_git(repository)

    return DiagnosticsContext(
        repository=repository,
        environment=environment,
        api_url=api_url,
        web_url=web_url,
        database_url=database_url,
        database_path=database_path,
        deep=deep,
        http_timeout=http_timeout,
        settings=settings,
        settings_error=settings_error,
        supervisor_error=supervisor_error,
        supervisor_status=supervisor_status,
        git_sha=git.sha,
        git_dirty=git.dirty,
        git_available=git.available,
        http_probe=http_probe or default_http_probe,
        clock=clock or (lambda: datetime.now(UTC)),
        secret_values=collect_secret_values(environment),
    )
