"""Individual runtime-doctor checks.

Every check is a pure function of the ``DiagnosticsContext`` plus facts the
runner has already remembered on it. Checks never mutate application state,
never start or stop processes, never clear emergency stop, never run
migrations, and never widen permissions. Remediation strings always describe
explicit operator actions.
"""

from __future__ import annotations

import asyncio
from importlib import metadata
from typing import Any
from urllib.parse import urlsplit

from app.core.errors import DomainError
from app.diagnostics.context import DiagnosticsContext
from app.diagnostics.models import CheckResult, CheckStatus
from app.diagnostics.probes import (
    HealthResult,
    alembic_heads,
    inspect_sqlite,
    tcp_connect,
)
from app.model_providers.contracts import HealthStatus, ProviderHealth
from app.model_providers.errors import ErrorCategory
from app.model_providers.factory import build_provider_registry
from app.model_providers.registry import ProviderRegistry

HEALTH_ENDPOINT = "/api/health"
SYSTEM_STATUS_ENDPOINT = "/api/system/status"
OFFICE_ENDPOINT = "/api/office"
APP_PACKAGE = "jarvis-simulator-api"
EXPECTED_SERVICE = "jarvis-simulator-api"
MAX_REASONS = 8
MAX_FAILURE_DETAIL = 200

START_RUNTIME_REMEDIATION = (
    "start the supported runtime: .\\scripts\\jarvis.ps1 start (supervised) "
    "or the documented local uvicorn/pnpm dev servers"
)
MIGRATE_REMEDIATION = (
    "run the supported migration workflow from apps/api: python -m alembic upgrade head "
    "(the doctor never migrates automatically)"
)
RESUME_REMEDIATION = (
    "investigate the cause, then use the supported resume action "
    "(System -> Resume in the web UI, or POST /api/system/resume); "
    "health tooling never clears emergency stop"
)
PORT_COLLISION_REMEDIATION = (
    "another process appears to be using the port; stop it via its own tooling or change "
    "the configured port - the doctor never kills processes"
)


def _reasons(items: list[str]) -> str:
    kept = [item for item in items if item]
    if len(kept) <= 1:
        return kept[0] if kept else "ok"
    return "; ".join(kept[:MAX_REASONS])


def _health_payload(result: Any) -> dict[str, Any] | None:
    if isinstance(result, HealthResult) and isinstance(result.payload, dict):
        return result.payload
    return None


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _as_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _truncate(value: str, limit: int = MAX_FAILURE_DETAIL) -> str:
    return value if len(value) <= limit else value[: limit - 3] + "..."


# ---------------------------------------------------------------------------
# 1. Repository / runtime identity
# ---------------------------------------------------------------------------


def check_identity(ctx: DiagnosticsContext) -> CheckResult:
    try:
        app_version: str | None = metadata.version(APP_PACKAGE)
    except metadata.PackageNotFoundError:
        app_version = None
    app_env = ctx.settings.app_env if ctx.settings is not None else "unknown"
    if not ctx.git_available or ctx.git_sha is None:
        return CheckResult(
            name="repository_identity",
            group="identity",
            status=CheckStatus.DEGRADED,
            reason="repository Git identity unavailable (not a Git checkout or git missing)",
            remediation="run the doctor inside the cloned repository for exact SHA reporting",
            identifiers={
                "appVersion": app_version,
                "appEnv": app_env,
                "apiEndpoint": ctx.api_url,
                "webEndpoint": ctx.web_url,
            },
        )
    return CheckResult(
        name="repository_identity",
        group="identity",
        status=CheckStatus.HEALTHY,
        reason="clean worktree" if not ctx.git_dirty else "local changes present in the worktree",
        identifiers={
            "gitSha": ctx.git_sha,
            "gitDirty": ctx.git_dirty,
            "appVersion": app_version,
            "appEnv": app_env,
            "apiEndpoint": ctx.api_url,
            "webEndpoint": ctx.web_url,
        },
    )


# ---------------------------------------------------------------------------
# 2. Configuration validity and feature flags
# ---------------------------------------------------------------------------


def check_configuration(ctx: DiagnosticsContext) -> CheckResult:
    problems: list[str] = []
    if ctx.settings_error:
        problems.append(f"application settings invalid: {ctx.settings_error}")
    if ctx.supervisor_error:
        problems.append(f"supervisor configuration invalid: {ctx.supervisor_error}")
    settings = ctx.settings
    identifiers: dict[str, Any] = {
        "autonomousWorkerEnabled": ctx.worker_configured,
        "modelExecutionMode": settings.model_execution_mode if settings else "unknown",
        "toolExecutionEnabled": settings.tool_execution_enabled if settings else None,
        "ollamaEnabled": settings.model_ollama_enabled if settings else None,
    }
    if problems:
        return CheckResult(
            name="configuration",
            group="configuration",
            status=CheckStatus.BLOCKED,
            reason=_reasons(problems),
            remediation=(
                "fix the reported configuration in the repository/apps .env files or process "
                "environment; values are redacted by the doctor"
            ),
            identifiers=identifiers,
        )
    return CheckResult(
        name="configuration",
        group="configuration",
        status=CheckStatus.HEALTHY,
        reason="application and supervisor configuration validate",
        identifiers=identifiers,
    )


# ---------------------------------------------------------------------------
# 3. Database health
# ---------------------------------------------------------------------------


def check_database(ctx: DiagnosticsContext) -> CheckResult:
    if ctx.database_path is None:
        return CheckResult(
            name="database",
            group="database",
            status=CheckStatus.BLOCKED,
            reason=(
                "configured JARVIS_DATABASE_URL is not a file-backed SQLite URL "
                "supported by this deployment"
            ),
            remediation="use the documented SQLite deployment (sqlite:///./data/jarvis.db)",
            identifiers={"databaseUrl": "unsupported"},
        )
    facts = inspect_sqlite(ctx.database_path, deep=ctx.deep)
    ctx.remember("database_facts", facts)
    identifiers: dict[str, Any] = {"databasePath": str(ctx.database_path)}
    if not facts.exists:
        return CheckResult(
            name="database",
            group="database",
            status=CheckStatus.BLOCKED,
            reason="database file does not exist yet",
            remediation=MIGRATE_REMEDIATION,
            identifiers=identifiers,
        )
    if not facts.openable:
        return CheckResult(
            name="database",
            group="database",
            status=CheckStatus.BLOCKED,
            reason=f"database cannot be opened read-only ({facts.open_error or 'unknown error'})",
            remediation="check file permissions and that the path is a SQLite database",
            identifiers=identifiers,
        )
    identifiers["databaseRevision"] = facts.revision
    heads = alembic_heads(ctx.repository / "apps" / "api" / "migrations")
    identifiers["expectedRevision"] = ", ".join(sorted(heads)) if heads else None
    if len(heads) > 1:
        return CheckResult(
            name="database",
            group="database",
            status=CheckStatus.DEGRADED,
            reason=f"migration scripts declare multiple heads: {', '.join(sorted(heads))}",
            remediation="resolve the migration fork before relying on the schema",
            identifiers=identifiers,
        )
    if facts.revision_error:
        return CheckResult(
            name="database",
            group="database",
            status=CheckStatus.BLOCKED,
            reason=f"schema revision cannot be determined ({facts.revision_error})",
            remediation=MIGRATE_REMEDIATION,
            identifiers=identifiers,
        )
    if not heads:
        return CheckResult(
            name="database",
            group="database",
            status=CheckStatus.DEGRADED,
            reason="migration scripts unavailable; expected revision cannot be determined",
            remediation="run the doctor from a complete repository checkout",
            identifiers=identifiers,
        )
    if facts.revision not in heads:
        return CheckResult(
            name="database",
            group="database",
            status=CheckStatus.BLOCKED,
            reason=(
                f"database schema is behind or foreign: current revision "
                f"{facts.revision!r} does not match expected head {sorted(heads)[0]!r}"
            ),
            remediation=MIGRATE_REMEDIATION,
            identifiers=identifiers,
        )
    if not facts.core_tables_present:
        return CheckResult(
            name="database",
            group="database",
            status=CheckStatus.BLOCKED,
            reason=f"schema is malformed: missing core tables {facts.missing_core_tables}",
            remediation="restore from a supervisor backup or re-run the migration workflow",
            identifiers=identifiers,
        )
    if ctx.deep and facts.integrity is not None and facts.integrity != "ok":
        return CheckResult(
            name="database",
            group="database",
            status=CheckStatus.BLOCKED,
            reason=f"integrity quick_check failed: {_truncate(str(facts.integrity))}",
            remediation=(
                "preserve the database file, review docs/persistence.md, and restore from the "
                "most recent supervisor backup"
            ),
            identifiers=identifiers,
        )
    api_revision = ctx.recalled("api_database_revision")
    if isinstance(api_revision, str) and api_revision != facts.revision:
        return CheckResult(
            name="database",
            group="database",
            status=CheckStatus.DEGRADED,
            reason=(
                f"running API reports revision {api_revision!r} while the configured database "
                f"is at {facts.revision!r}; the doctor and API may be using different databases"
            ),
            remediation="confirm JARVIS_DATABASE_URL matches the running API configuration",
            identifiers=identifiers,
        )
    return CheckResult(
        name="database",
        group="database",
        status=CheckStatus.HEALTHY,
        reason=f"reachable, schema current at {facts.revision}",
        remediation=None,
        identifiers=identifiers,
    )


# ---------------------------------------------------------------------------
# 4. API health
# ---------------------------------------------------------------------------


def _api_unreachable_result(ctx: DiagnosticsContext, result: HealthResult) -> CheckResult:
    host = urlsplit(ctx.api_url).hostname or "127.0.0.1"
    port = urlsplit(ctx.api_url).port or 80
    connected, error = tcp_connect(ctx.api_url)
    identifiers = {"apiEndpoint": ctx.api_url, "expectedPort": port}
    if connected:
        return CheckResult(
            name="api",
            group="api",
            status=CheckStatus.BLOCKED,
            reason=(
                f"something accepts connections on {host}:{port} but does not answer the "
                f"Jarvis health endpoint ({result.status})"
            ),
            remediation=PORT_COLLISION_REMEDIATION,
            identifiers=identifiers,
        )
    return CheckResult(
        name="api",
        group="api",
        status=CheckStatus.BLOCKED,
        reason=(
            f"API process is not running: nothing is listening on {host}:{port} "
            f"({error or result.status})"
        ),
        remediation=START_RUNTIME_REMEDIATION,
        identifiers=identifiers,
    )


def check_api(ctx: DiagnosticsContext) -> CheckResult:
    result = ctx.probe_json(f"{ctx.api_url}{HEALTH_ENDPOINT}")
    ctx.remember("api_health", result)
    if not isinstance(result, HealthResult) or not result.available:
        return _api_unreachable_result(ctx, result)
    identifiers: dict[str, Any] = {"apiEndpoint": ctx.api_url}
    payload = _health_payload(result)
    if payload is None:
        return CheckResult(
            name="api",
            group="api",
            status=CheckStatus.DEGRADED,
            reason=f"API process is reachable but the health response is invalid: {result.detail}",
            remediation="check the API logs; the response did not match the health contract",
            identifiers=identifiers,
        )
    service = payload.get("service")
    identifiers["service"] = service
    identifiers["applicationStatus"] = payload.get("status")
    identifiers["databaseReachable"] = _as_bool(payload.get("databaseReachable"))
    identifiers["schemaCurrent"] = _as_bool(payload.get("schemaCurrent"))
    if service != EXPECTED_SERVICE:
        return CheckResult(
            name="api",
            group="api",
            status=CheckStatus.DEGRADED,
            reason=f"unexpected service identity {service!r} on the health endpoint",
            remediation="confirm the endpoint points at the Jarvis API (API_HOST/API_PORT)",
            identifiers=identifiers,
        )
    ctx.remember("api_health_payload", payload)
    if result.status == "healthy" and payload.get("status") == "healthy":
        return CheckResult(
            name="api",
            group="api",
            status=CheckStatus.HEALTHY,
            reason="health endpoint reports a healthy, ready application",
            identifiers=identifiers,
        )
    degraded_reasons: list[str] = []
    if payload.get("databaseReachable") is False:
        degraded_reasons.append("database unreachable")
    if payload.get("schemaCurrent") is False:
        degraded_reasons.append("schema not current")
    if payload.get("recoveryRequired"):
        degraded_reasons.append("workflow recovery required")
    exhausted = _as_int(payload.get("outboxExhaustedCount"))
    if exhausted:
        degraded_reasons.append(f"{exhausted} exhausted outbox events")
    expired = _as_int(payload.get("expiredLeaseCount"))
    if expired:
        degraded_reasons.append(f"{expired} expired task leases")
    stale = _as_int(payload.get("staleWorkerCount"))
    if stale:
        degraded_reasons.append(f"{stale} stale workers")
    return CheckResult(
        name="api",
        group="api",
        status=CheckStatus.DEGRADED,
        reason=_reasons(degraded_reasons or ["application reports degraded state"]),
        remediation="review the reported conditions; run the doctor with --deep for contracts",
        identifiers=identifiers,
    )


def check_system_status_contract(ctx: DiagnosticsContext) -> CheckResult:
    """Deep-mode check: the system status endpoint answers a valid contract."""

    result = ctx.probe_json(f"{ctx.api_url}{SYSTEM_STATUS_ENDPOINT}")
    if not isinstance(result, HealthResult) or not result.available:
        return CheckResult(
            name="api_system_status_contract",
            group="api",
            status=CheckStatus.BLOCKED,
            reason=f"/api/system/status is not answering ({result.status if isinstance(result, HealthResult) else 'probe error'})",
            remediation=START_RUNTIME_REMEDIATION,
            identifiers={"apiEndpoint": ctx.api_url},
        )
    payload = _health_payload(result)
    identifiers: dict[str, Any] = {"apiEndpoint": ctx.api_url}
    if payload is None:
        return CheckResult(
            name="api_system_status_contract",
            group="api",
            status=CheckStatus.DEGRADED,
            reason=f"system status response is not valid Jarvis JSON: {result.detail}",
            remediation="check the API logs for the system status handler",
            identifiers=identifiers,
        )
    required = {"status", "environment", "emergencyStop", "databaseRevision", "schemaCurrent"}
    missing = sorted(required - set(payload))
    identifiers.update(
        {
            "status": payload.get("status"),
            "environment": payload.get("environment"),
            "emergencyStop": payload.get("emergencyStop"),
            "databaseRevision": payload.get("databaseRevision"),
        }
    )
    ctx.remember("api_database_revision", payload.get("databaseRevision"))
    ctx.remember("api_emergency_stop", payload.get("emergencyStop"))
    if missing:
        return CheckResult(
            name="api_system_status_contract",
            group="api",
            status=CheckStatus.DEGRADED,
            reason=f"system status contract is missing fields: {', '.join(missing)}",
            remediation="the API build may not match this checkout; redeploy the API",
            identifiers=identifiers,
        )
    return CheckResult(
        name="api_system_status_contract",
        group="api",
        status=CheckStatus.HEALTHY,
        reason="system status endpoint answers the expected contract",
        identifiers=identifiers,
    )


# ---------------------------------------------------------------------------
# 5. Frontend availability
# ---------------------------------------------------------------------------


def check_frontend(ctx: DiagnosticsContext) -> CheckResult:
    url = f"{ctx.web_url}/"
    result = ctx.probe_plain(url)
    ctx.remember("web_health", result)
    identifiers = {"webEndpoint": ctx.web_url}
    if not isinstance(result, HealthResult) or not result.available:
        supervisor = ctx.supervisor_status
        processes = supervisor.get("processes") if isinstance(supervisor, dict) else None
        web_process = processes.get("web") if isinstance(processes, dict) else None
        if (
            isinstance(web_process, dict)
            and web_process.get("enabled")
            and supervisor.get("ownership") == "running"
            and web_process.get("processState") == "running"
        ):
            return CheckResult(
                name="frontend",
                group="frontend",
                status=CheckStatus.DEGRADED,
                reason=(
                    f"supervisor reports the web preview running but {url} did not answer "
                    f"({result.status if isinstance(result, HealthResult) else 'probe error'})"
                ),
                remediation="inspect supervisor logs (logs/web.log); the preview server may be overloaded",
                identifiers=identifiers,
            )
        connected, _error = tcp_connect(ctx.web_url)
        if connected:
            return CheckResult(
                name="frontend",
                group="frontend",
                status=CheckStatus.BLOCKED,
                reason=(
                    f"the configured web port is occupied but does not serve the web UI "
                    f"({url} returned {result.status if isinstance(result, HealthResult) else 'error'})"
                ),
                remediation=PORT_COLLISION_REMEDIATION,
                identifiers=identifiers,
            )
        return CheckResult(
            name="frontend",
            group="frontend",
            status=CheckStatus.DEGRADED,
            reason=f"web UI is not being served at {url}",
            remediation=START_RUNTIME_REMEDIATION,
            identifiers=identifiers,
        )
    return CheckResult(
        name="frontend",
        group="frontend",
        status=CheckStatus.HEALTHY,
        reason="web endpoint responds",
        identifiers=identifiers,
    )


# ---------------------------------------------------------------------------
# 6. Runtime supervisor
# ---------------------------------------------------------------------------

_REQUIRED_PROCESSES = ("api", "web")


def check_supervisor(ctx: DiagnosticsContext) -> CheckResult:
    supervisor = ctx.supervisor_status
    identifiers: dict[str, Any] = {
        "ownership": supervisor.get("ownership"),
        "supervisorState": supervisor.get("supervisorState"),
    }
    if not isinstance(supervisor, dict) or not supervisor:
        return CheckResult(
            name="supervisor",
            group="supervisor",
            status=CheckStatus.UNKNOWN,
            reason="supervisor coordination state could not be read",
            remediation="confirm LOCALAPPDATA (or XDG_STATE_HOME) is available to the doctor",
            identifiers=identifiers,
        )
    ownership = supervisor.get("ownership")
    processes = supervisor.get("processes") if isinstance(supervisor.get("processes"), dict) else {}
    identifiers["processStates"] = {
        name: (process or {}).get("processState") for name, process in sorted(processes.items())
    }
    if ownership == "stale":
        return CheckResult(
            name="supervisor",
            group="supervisor",
            status=CheckStatus.BLOCKED,
            reason=(
                "stale supervisor state: the recorded supervisor PID no longer matches a live "
                "process (crash or PID reuse)"
            ),
            remediation=(
                "run .\\scripts\\jarvis.ps1 stop followed by start; stale state never causes "
                "the doctor to kill any process"
            ),
            identifiers=identifiers,
        )
    api_health = ctx.recalled("api_health")
    api_available = isinstance(api_health, HealthResult) and api_health.available
    if ownership != "running":
        reason = (
            "supervisor is not running while the API answers directly (unsupervised developer mode)"
            if api_available
            else "supervisor is not running"
        )
        return CheckResult(
            name="supervisor",
            group="supervisor",
            status=CheckStatus.DEGRADED,
            reason=reason,
            remediation=(
                "for 24/7 supervised operation run .\\scripts\\jarvis.ps1 start; "
                "developer servers remain supported"
            ),
            identifiers=identifiers,
        )
    problems: list[str] = []
    degraded: list[str] = []
    if not processes:
        degraded.append("state record lacks per-process detail")
    for name in _REQUIRED_PROCESSES:
        process = processes.get(name)
        if not isinstance(process, dict):
            problems.append(f"{name} process state missing")
            continue
        if not process.get("enabled", True):
            continue
        state = process.get("processState")
        if state != "running":
            problems.append(f"{name} process is {state}")
        elif process.get("healthState") not in {"healthy", "degraded", "starting"}:
            problems.append(f"{name} health is {process.get('healthState')}")
        elif process.get("healthState") != "healthy":
            degraded.append(f"{name} health is {process.get('healthState')}")
        restarts = _as_int(process.get("restartCount"))
        if restarts:
            degraded.append(f"{name} restarted {restarts} time(s)")
        last_failure = process.get("lastFailure")
        if isinstance(last_failure, str) and last_failure:
            degraded.append(f"{name} last failure: {_truncate(last_failure, 120)}")
    if problems:
        return CheckResult(
            name="supervisor",
            group="supervisor",
            status=CheckStatus.BLOCKED,
            reason=_reasons(problems),
            remediation="inspect .\\scripts\\jarvis.ps1 status and the supervisor logs directory",
            identifiers=identifiers,
        )
    if degraded:
        return CheckResult(
            name="supervisor",
            group="supervisor",
            status=CheckStatus.DEGRADED,
            reason=_reasons(degraded),
            remediation="inspect the supervisor logs directory for the reported children",
            identifiers=identifiers,
        )
    return CheckResult(
        name="supervisor",
        group="supervisor",
        status=CheckStatus.HEALTHY,
        reason="supervisor is running and required children are healthy",
        identifiers=identifiers,
    )


# ---------------------------------------------------------------------------
# 7. Local model provider
# ---------------------------------------------------------------------------


def default_provider_health(settings: Any) -> dict[str, ProviderHealth]:
    registry: ProviderRegistry = build_provider_registry(settings)
    return asyncio.run(registry.health())


def check_provider(
    ctx: DiagnosticsContext,
    provider_health: Any = default_provider_health,
) -> CheckResult:
    settings = ctx.settings
    if settings is None:
        return CheckResult(
            name="model_provider",
            group="provider",
            status=CheckStatus.UNKNOWN,
            reason="application settings are invalid; provider state cannot be determined",
            remediation="fix the reported configuration problem first",
            identifiers={},
        )
    identifiers: dict[str, Any] = {
        "modelExecutionMode": settings.model_execution_mode,
        "ollamaBaseUrl": str(settings.model_ollama_base_url),
        "ollamaModel": settings.model_ollama_model,
    }
    if settings.model_execution_mode == "disabled":
        return CheckResult(
            name="model_provider",
            group="provider",
            status=CheckStatus.DISABLED,
            reason="model execution is disabled by configuration (JARVIS_MODEL_EXECUTION_MODE)",
            remediation=(
                "to enable local planning, set JARVIS_MODEL_EXECUTION_MODE=local_only and enable "
                "a supported loopback provider"
            ),
            identifiers=identifiers,
        )
    try:
        health = provider_health(settings)
    except Exception as exc:  # noqa: BLE001 - provider probe failures are data, not crashes
        return CheckResult(
            name="model_provider",
            group="provider",
            status=CheckStatus.BLOCKED,
            reason=f"provider health could not be evaluated ({exc.__class__.__name__})",
            remediation="check provider configuration; the doctor does not launch providers",
            identifiers=identifiers,
        )
    ctx.remember("provider_health", health)
    if not health:
        return CheckResult(
            name="model_provider",
            group="provider",
            status=CheckStatus.BLOCKED,
            reason="no model provider is enabled although model execution is local_only",
            remediation="enable a supported local provider (for example JARVIS_MODEL_OLLAMA_ENABLED=true)",
            identifiers=identifiers,
        )
    worst = CheckStatus.HEALTHY
    reasons: list[str] = []
    provider_details: dict[str, Any] = {}
    for name, result in sorted(health.items()):
        status, reason = _classify_provider_health(result)
        provider_details[name] = {
            "status": result.status.value,
            "modelAvailable": result.model_available,
            "errorCategory": result.error_category,
        }
        if status.value in {"blocked", "degraded"}:
            reasons.append(f"{name}: {reason}")
        if status == CheckStatus.BLOCKED:
            worst = CheckStatus.BLOCKED
        elif status == CheckStatus.DEGRADED and worst != CheckStatus.BLOCKED:
            worst = CheckStatus.DEGRADED
    identifiers["providers"] = provider_details
    if worst == CheckStatus.HEALTHY:
        names = ", ".join(sorted(health))
        return CheckResult(
            name="model_provider",
            group="provider",
            status=CheckStatus.HEALTHY,
            reason=f"provider(s) reachable with the configured model available: {names}",
            identifiers=identifiers,
        )
    return CheckResult(
        name="model_provider",
        group="provider",
        status=worst,
        reason=_reasons(reasons),
        remediation=(
            "start/configure the supported local provider and install or select the configured "
            "model with the provider's own tooling; the doctor never downloads models"
        ),
        identifiers=identifiers,
    )


def _classify_provider_health(result: ProviderHealth) -> tuple[CheckStatus, str]:
    if not result.healthy:
        if result.error_category == ErrorCategory.MALFORMED_PROVIDER_RESPONSE.value:
            return CheckStatus.BLOCKED, "provider returned a malformed response"
        if result.error_category == ErrorCategory.PROVIDER_UNAVAILABLE.value:
            return CheckStatus.BLOCKED, "provider service is unreachable or stopped"
        if result.error_category == ErrorCategory.MODEL_UNAVAILABLE.value:
            return CheckStatus.BLOCKED, "the configured model is unavailable on this provider"
        if result.error_category == ErrorCategory.AUTHENTICATION_FAILURE.value:
            return CheckStatus.BLOCKED, "provider rejected the configured credentials"
        if result.error_category:
            return CheckStatus.BLOCKED, f"provider error ({result.error_category})"
        return CheckStatus.BLOCKED, "provider is not healthy"
    if result.status == HealthStatus.CONFIGURATION_ONLY:
        return (
            CheckStatus.DEGRADED,
            "provider health is configuration-only (network probing disabled)",
        )
    if result.model_available is False:
        return CheckStatus.BLOCKED, "configured model is missing on this provider"
    if result.model_available is None:
        return CheckStatus.DEGRADED, "model availability could not be determined"
    return CheckStatus.HEALTHY, "reachable and model available"


# ---------------------------------------------------------------------------
# 8. Autonomous worker
# ---------------------------------------------------------------------------


def check_worker(ctx: DiagnosticsContext) -> CheckResult:
    identifiers: dict[str, Any] = {"enabled": ctx.worker_configured}
    if not ctx.worker_configured:
        return CheckResult(
            name="autonomous_worker",
            group="worker",
            status=CheckStatus.DISABLED,
            reason="autonomous worker is disabled by configuration (JARVIS_AUTONOMOUS_WORKER_ENABLED)",
            remediation=(
                "complete docs/autonomous-worker.md setup and set "
                "JARVIS_AUTONOMOUS_WORKER_ENABLED=true to enable supervised autonomy"
            ),
            identifiers=identifiers,
        )
    settings = ctx.settings
    identifiers["workerActorId"] = settings.autonomous_worker_actor_id if settings else None
    identifiers["instanceIdConfigured"] = bool(
        settings and settings.autonomous_worker_instance_id.strip()
    )
    supervisor = ctx.supervisor_status if isinstance(ctx.supervisor_status, dict) else {}
    processes = supervisor.get("processes") if isinstance(supervisor.get("processes"), dict) else {}
    worker_process = processes.get("autonomous_worker")
    if isinstance(worker_process, dict):
        identifiers["processState"] = worker_process.get("processState")
        identifiers["healthState"] = worker_process.get("healthState")
        identifiers["restartCount"] = worker_process.get("restartCount")
    payload = ctx.recalled("api_health_payload")
    component = payload.get("autonomousWorker") if isinstance(payload, dict) else None
    component = component if isinstance(component, dict) else None
    if component:
        identifiers["applicationStatus"] = component.get("status")
        identifiers["reasonCode"] = component.get("reasonCode")
        identifiers["activeExecutions"] = component.get("activeExecutionCount")
        identifiers["queuedEligible"] = component.get("queuedEligibleRuntimeCount")
        identifiers["lastWorkerHeartbeat"] = component.get("lastWorkerHeartbeat")

    stale_workers = _as_int(payload.get("staleWorkerCount")) if isinstance(payload, dict) else None
    expired_leases = (
        _as_int(payload.get("expiredLeaseCount")) if isinstance(payload, dict) else None
    )

    if stale_workers or expired_leases:
        reasons = []
        if stale_workers:
            reasons.append(f"{stale_workers} stale worker registration(s) by lease authority")
        if expired_leases:
            reasons.append(f"{expired_leases} expired task lease(s) awaiting recovery")
        identifiers["workerState"] = "stale"
        return CheckResult(
            name="autonomous_worker",
            group="worker",
            status=CheckStatus.DEGRADED,
            reason=_reasons(reasons),
            remediation=(
                "expired leases recover automatically while the API runs; verify the worker "
                "process is alive and check its logs"
            ),
            identifiers=identifiers,
        )

    if component:
        status_value = component.get("status")
        reason_code = component.get("reasonCode")
        if status_value == "healthy":
            active = _as_int(component.get("activeExecutionCount")) or 0
            queued = _as_int(component.get("queuedEligibleRuntimeCount")) or 0
            identifiers["workerState"] = "active" if (active or queued) else "idle"
            return CheckResult(
                name="autonomous_worker",
                group="worker",
                status=CheckStatus.HEALTHY,
                reason=(
                    f"worker is healthy and {'executing' if active else 'idle'}"
                    + (f" with {queued} eligible queued run(s)" if queued else "")
                ),
                identifiers=identifiers,
            )
        mapping = _WORKER_REASON_CODES.get(reason_code)
        if mapping:
            state, status, reason = mapping
            identifiers["workerState"] = state
            return CheckResult(
                name="autonomous_worker",
                group="worker",
                status=status,
                reason=reason,
                remediation=_worker_remediation(reason_code),
                identifiers=identifiers,
            )
        identifiers["workerState"] = "degraded"
        return CheckResult(
            name="autonomous_worker",
            group="worker",
            status=CheckStatus.DEGRADED,
            reason=f"application reports worker status {status_value!r} ({reason_code or 'no reason code'})",
            remediation="check the worker logs (supervisor logs/autonomous_worker.log)",
            identifiers=identifiers,
        )

    # No authoritative application status: use the supervisor's process record.
    if isinstance(worker_process, dict):
        state = worker_process.get("processState")
        if state == "running":
            identifiers["workerState"] = "starting"
            return CheckResult(
                name="autonomous_worker",
                group="worker",
                status=CheckStatus.DEGRADED,
                reason="worker process is running but the application has not published worker status",
                remediation="the API may still be starting or unreachable; re-run the doctor",
                identifiers=identifiers,
            )
        identifiers["workerState"] = "absent" if state in {"not_running", "disabled"} else "failed"
        return CheckResult(
            name="autonomous_worker",
            group="worker",
            status=CheckStatus.BLOCKED,
            reason=(
                f"worker process is {state}"
                + (
                    f" (last failure: {_truncate(str(worker_process.get('lastFailure')), 120)})"
                    if worker_process.get("lastFailure")
                    else ""
                )
            ),
            remediation=(
                "start the worker through the supervisor (.\\scripts\\jarvis.ps1 start) or the "
                "documented manual worker command"
            ),
            identifiers=identifiers,
        )
    identifiers["workerState"] = "absent"
    return CheckResult(
        name="autonomous_worker",
        group="worker",
        status=CheckStatus.BLOCKED,
        reason="worker is enabled but no worker process or status is available",
        remediation=(
            "start the worker through the supervisor (.\\scripts\\jarvis.ps1 start) or the "
            "documented manual worker command"
        ),
        identifiers=identifiers,
    )


_WORKER_REASON_CODES: dict[str, tuple[str, CheckStatus, str]] = {
    "autonomous_worker_unavailable": (
        "absent",
        CheckStatus.BLOCKED,
        "no live worker heartbeat: the worker process is not running or cannot reach the API",
    ),
    "execution_lease_lost": (
        "stale",
        CheckStatus.DEGRADED,
        "an active execution lost its task lease (worker restart or lease expiry)",
    ),
    "no_local_provider_available": (
        "blocked",
        CheckStatus.BLOCKED,
        "no local model provider is available to the worker",
    ),
    "model_execution_disabled": (
        "blocked",
        CheckStatus.BLOCKED,
        "model execution mode is not local_only",
    ),
    "model_result_corrupt": (
        "blocked",
        CheckStatus.BLOCKED,
        "persisted model results failed integrity validation",
    ),
    "autonomous_runtime_state_corrupt": (
        "blocked",
        CheckStatus.BLOCKED,
        "autonomous runtime state is corrupt",
    ),
    "model_execution_outbox_exhausted": (
        "blocked",
        CheckStatus.DEGRADED,
        "model execution events exhausted outbox delivery attempts",
    ),
}


def _worker_remediation(reason_code: str | None) -> str:
    if reason_code == "autonomous_worker_unavailable":
        return (
            "start the worker through the supervisor (.\\scripts\\jarvis.ps1 start) or the "
            "documented manual worker command from apps/api"
        )
    if reason_code == "no_local_provider_available":
        return "start or configure the supported local provider (see the model provider check)"
    if reason_code == "model_execution_disabled":
        return "set JARVIS_MODEL_EXECUTION_MODE=local_only for enabled workers"
    if reason_code in {"model_result_corrupt", "autonomous_runtime_state_corrupt"}:
        return "inspect the runtime ledger and follow docs/recovery.md; do not delete the database"
    if reason_code == "model_execution_outbox_exhausted":
        return "inspect outbox failures in the API logs; delivery retries are bounded by design"
    return "check the worker and API logs for the reported reason code"


# ---------------------------------------------------------------------------
# 9. Planning/runtime readiness
# ---------------------------------------------------------------------------


def check_readiness(ctx: DiagnosticsContext, checks: dict[str, CheckResult]) -> CheckResult:
    """Explain why autonomous planning is blocked, using existing authority."""

    if not ctx.worker_configured:
        flags: list[str] = ["autonomous worker disabled (JARVIS_AUTONOMOUS_WORKER_ENABLED=false)"]
        if ctx.settings is not None:
            flags.append(f"model execution mode {ctx.settings.model_execution_mode!r}")
            if not ctx.settings.model_ollama_enabled:
                flags.append("Ollama provider disabled")
        return CheckResult(
            name="planning_readiness",
            group="readiness",
            status=CheckStatus.DISABLED,
            reason="autonomous planning is not enabled by configuration",
            remediation=(
                "follow docs/autonomous-worker.md: configure the worker actor/instance and a "
                "local provider, then enable the worker explicitly"
            ),
            identifiers={"autonomousOperation": "disabled"},
        )
    blockers: list[str] = []
    configuration = checks.get("configuration")
    if configuration is not None and configuration.status == CheckStatus.BLOCKED:
        blockers.append("configuration invalid")
    api = checks.get("api")
    if api is not None and api.status == CheckStatus.BLOCKED:
        blockers.append("API unavailable")
    database = checks.get("database")
    if database is not None and database.status == CheckStatus.BLOCKED:
        blockers.append("database not ready")
    emergency = checks.get("emergency_stop")
    if emergency is not None and emergency.status == CheckStatus.BLOCKED:
        blockers.append("emergency stop active")
    provider = checks.get("model_provider")
    if provider is not None and provider.status == CheckStatus.BLOCKED:
        blockers.append("model provider unavailable or model missing")
    worker = checks.get("autonomous_worker")
    if worker is not None and worker.status == CheckStatus.BLOCKED:
        blockers.append("worker process not running")
    payload = ctx.recalled("api_health_payload")
    component = payload.get("autonomousWorker") if isinstance(payload, dict) else None
    if isinstance(component, dict) and component.get("reasonCode"):
        blockers.append(f"application reason code {component['reasonCode']!r}")
    identifiers: dict[str, Any] = {"autonomousOperation": "blocked" if blockers else "ready"}
    if blockers:
        return CheckResult(
            name="planning_readiness",
            group="readiness",
            status=CheckStatus.BLOCKED,
            reason=_reasons(blockers),
            remediation="resolve the reported blockers; the doctor never grants permissions or roles",
            identifiers=identifiers,
        )
    return CheckResult(
        name="planning_readiness",
        group="readiness",
        status=CheckStatus.HEALTHY,
        reason="no blocker found: the enabled worker can accept supported work",
        identifiers=identifiers,
    )


# ---------------------------------------------------------------------------
# 10. Emergency stop
# ---------------------------------------------------------------------------


def check_emergency_stop(ctx: DiagnosticsContext) -> CheckResult:
    facts = ctx.recalled("database_facts")
    active: bool | None = None
    source = None
    if facts is not None and facts.system_state_readable:
        active = facts.emergency_stop
        source = "durable system state"
    else:
        api_value = ctx.recalled("api_emergency_stop")
        if api_value is None:
            api_value = (
                ctx.supervisor_status.get("emergencyStop") if ctx.supervisor_status else None
            )
        if isinstance(api_value, bool):
            active = api_value
            source = "running application state (may lag the database)"
    identifiers: dict[str, Any] = {"active": active, "source": source}
    if active is None:
        return CheckResult(
            name="emergency_stop",
            group="emergency_stop",
            status=CheckStatus.UNKNOWN,
            reason="emergency-stop state could not be determined from any authoritative source",
            remediation="the database and API were unavailable; fix those checks first",
            identifiers=identifiers,
        )
    if active:
        return CheckResult(
            name="emergency_stop",
            group="emergency_stop",
            status=CheckStatus.BLOCKED,
            reason="emergency stop is active: autonomous execution is administratively stopped",
            remediation=RESUME_REMEDIATION,
            identifiers=identifiers,
        )
    return CheckResult(
        name="emergency_stop",
        group="emergency_stop",
        status=CheckStatus.HEALTHY,
        reason="emergency stop is not active",
        identifiers=identifiers,
    )


# ---------------------------------------------------------------------------
# 11. Workspace tools
# ---------------------------------------------------------------------------


def check_workspace_tools(ctx: DiagnosticsContext) -> CheckResult:
    settings = ctx.settings
    if settings is None:
        return CheckResult(
            name="workspace_tools",
            group="workspace",
            status=CheckStatus.UNKNOWN,
            reason="application settings are invalid; workspace configuration cannot be validated",
            remediation="fix the reported configuration problem first",
            identifiers={"enabled": None},
        )
    if not settings.tool_execution_enabled:
        return CheckResult(
            name="workspace_tools",
            group="workspace",
            status=CheckStatus.DISABLED,
            reason="workspace tools are disabled by configuration (JARVIS_TOOL_EXECUTION_ENABLED)",
            remediation=(
                "to use workspace tools, set JARVIS_TOOL_EXECUTION_ENABLED=true and configure "
                "JARVIS_TOOL_WORKSPACES_JSON per docs/goal-mode/workspace-tools.md"
            ),
            identifiers={"enabled": False},
        )
    from app.tool_execution.filesystem import WorkspaceToolRegistry

    try:
        registry = WorkspaceToolRegistry(settings.tool_workspaces_json)
    except DomainError as error:
        return CheckResult(
            name="workspace_tools",
            group="workspace",
            status=CheckStatus.BLOCKED,
            reason=f"workspace configuration is invalid ({error.code})",
            remediation=(
                "JARVIS_TOOL_WORKSPACES_JSON must map aliases to marked local directories; "
                "see docs/goal-mode/workspace-tools.md"
            ),
            identifiers={"enabled": True, "configurationError": error.code},
        )
    try:
        workspaces = registry.workspaces()
    except DomainError as error:
        return CheckResult(
            name="workspace_tools",
            group="workspace",
            status=CheckStatus.BLOCKED,
            reason=f"workspace validation failed ({error.code})",
            remediation="review the workspace marker and directory configuration",
            identifiers={"enabled": True, "configurationError": error.code},
        )
    identifiers: dict[str, Any] = {
        "enabled": True,
        "configuredAliases": [item.workspaceId for item in workspaces],
    }
    if not workspaces:
        return CheckResult(
            name="workspace_tools",
            group="workspace",
            status=CheckStatus.DEGRADED,
            reason="workspace tools are enabled but no workspaces are configured",
            remediation="configure at least one marked workspace or disable the feature",
            identifiers=identifiers,
        )
    not_ready = [item for item in workspaces if not item.ready]
    if not_ready:
        identifiers["unreadyWorkspaces"] = {item.workspaceId: item.reasonCode for item in not_ready}
        return CheckResult(
            name="workspace_tools",
            group="workspace",
            status=CheckStatus.BLOCKED,
            reason=_reasons(
                [
                    f"workspace {item.workspaceId!r} not ready ({item.reasonCode})"
                    for item in not_ready
                ]
            ),
            remediation=(
                "create the operator workspace marker (.jarvis-workspace.json) and directory "
                "per docs/goal-mode/workspace-tools.md; the doctor never writes into workspaces"
            ),
            identifiers=identifiers,
        )
    return CheckResult(
        name="workspace_tools",
        group="workspace",
        status=CheckStatus.HEALTHY,
        reason=f"{len(workspaces)} configured workspace(s) are marked and ready",
        identifiers=identifiers,
    )


# ---------------------------------------------------------------------------
# 12. Office/runtime activity path
# ---------------------------------------------------------------------------

_OFFICE_REQUIRED_FIELDS = ("serverTime", "catalog", "placements", "placementVersions")


def check_office(ctx: DiagnosticsContext) -> CheckResult:
    api_health = ctx.recalled("api_health")
    identifiers: dict[str, Any] = {"officeEndpoint": f"{ctx.api_url}{OFFICE_ENDPOINT}"}
    if not (isinstance(api_health, HealthResult) and api_health.available):
        return CheckResult(
            name="office_runtime_path",
            group="office",
            status=CheckStatus.UNKNOWN,
            reason="the Office state path cannot be verified while the API is unavailable",
            remediation=START_RUNTIME_REMEDIATION,
            identifiers=identifiers,
        )
    result = ctx.probe_json(f"{ctx.api_url}{OFFICE_ENDPOINT}")
    payload = _health_payload(result) if isinstance(result, HealthResult) else None
    if payload is None:
        return CheckResult(
            name="office_runtime_path",
            group="office",
            status=CheckStatus.DEGRADED,
            reason=f"the Office endpoint did not answer a valid snapshot ({result.status if isinstance(result, HealthResult) else 'probe error'})",
            remediation="check the API logs; the office router may be failing",
            identifiers=identifiers,
        )
    missing = [field for field in _OFFICE_REQUIRED_FIELDS if field not in payload]
    placements = payload.get("placements")
    identifiers["placementCount"] = len(placements) if isinstance(placements, list) else None
    identifiers["officeEmergencyStop"] = _as_bool(payload.get("emergencyStop"))
    if missing:
        return CheckResult(
            name="office_runtime_path",
            group="office",
            status=CheckStatus.DEGRADED,
            reason=f"office snapshot contract is missing fields: {', '.join(missing)}",
            remediation="the API build may not match this checkout; redeploy the API",
            identifiers=identifiers,
        )
    emergency = ctx.recalled("database_facts")
    durable_stop = emergency.emergency_stop if emergency is not None else None
    office_stop = _as_bool(payload.get("emergencyStop"))
    if (
        ctx.deep
        and durable_stop is not None
        and office_stop is not None
        and durable_stop != office_stop
    ):
        return CheckResult(
            name="office_runtime_path",
            group="office",
            status=CheckStatus.DEGRADED,
            reason=(
                "office snapshot emergency stop does not match durable system state "
                f"(office={office_stop}, database={durable_stop})"
            ),
            remediation="the office reconcile loop may be lagging; re-run the doctor after a few seconds",
            identifiers=identifiers,
        )
    return CheckResult(
        name="office_runtime_path",
        group="office",
        status=CheckStatus.HEALTHY,
        reason="the Office endpoint answers the runtime state contract",
        identifiers=identifiers,
    )


# ---------------------------------------------------------------------------
# 13. Recent critical runtime failures
# ---------------------------------------------------------------------------


def check_recent_failures(ctx: DiagnosticsContext) -> CheckResult:
    reasons: list[str] = []
    identifiers: dict[str, Any] = {}
    payload = ctx.recalled("api_health_payload")
    if isinstance(payload, dict):
        if payload.get("recoveryRequired"):
            reasons.append("workflow recovery required")
        exhausted = _as_int(payload.get("outboxExhaustedCount"))
        if exhausted:
            reasons.append(f"{exhausted} outbox event(s) exhausted delivery attempts")
            identifiers["outboxExhaustedCount"] = exhausted
        expired = _as_int(payload.get("expiredLeaseCount"))
        if expired:
            reasons.append(f"{expired} expired task lease(s)")
            identifiers["expiredLeaseCount"] = expired
        stale = _as_int(payload.get("staleWorkerCount"))
        if stale:
            reasons.append(f"{stale} stale worker registration(s)")
            identifiers["staleWorkerCount"] = stale
    supervisor = ctx.supervisor_status if isinstance(ctx.supervisor_status, dict) else {}
    processes = supervisor.get("processes") if isinstance(supervisor.get("processes"), dict) else {}
    for name, process in sorted(processes.items()):
        if not isinstance(process, dict) or not process.get("enabled"):
            continue
        history = process.get("failureHistory")
        if isinstance(history, list) and history:
            identifiers[f"{name}FailureCount"] = len(history)
            reasons.append(f"{name}: {_truncate(str(history[-1]), 120)}")
        elif isinstance(process.get("lastFailure"), str) and process["lastFailure"]:
            reasons.append(f"{name}: {_truncate(process['lastFailure'], 120)}")
    if reasons:
        return CheckResult(
            name="recent_failures",
            group="failures",
            status=CheckStatus.DEGRADED,
            reason=_reasons(reasons),
            remediation=(
                "inspect the supervisor logs directory and API logs for the reported failures; "
                "docs/recovery.md describes the supported recovery paths"
            ),
            identifiers=identifiers,
        )
    if not isinstance(payload, dict) and not processes:
        return CheckResult(
            name="recent_failures",
            group="failures",
            status=CheckStatus.UNKNOWN,
            reason="no failure source (API health or supervisor state) is available",
            remediation="fix the API/supervisor checks first; failure history is unreadable",
            identifiers=identifiers,
        )
    return CheckResult(
        name="recent_failures",
        group="failures",
        status=CheckStatus.HEALTHY,
        reason="no recent critical runtime failures observed",
        identifiers=identifiers,
    )
