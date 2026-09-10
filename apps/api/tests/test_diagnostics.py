"""Deterministic coverage for the Jarvis runtime doctor checks.

Every scenario is driven by a scripted HTTP probe, a scripted provider-health
probe, real migrated SQLite databases, and real supervisor coordination state
files, so the tests run identically on Linux CI and Windows developer
machines. No test starts an uncontrolled process or performs model inference.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.diagnostics import checks as doctor_checks
from app.diagnostics.models import CheckStatus
from app.diagnostics.probes import HealthResult
from app.diagnostics.runner import run_diagnostics
from app.main import DATABASE_REVISION
from app.model_providers.contracts import HealthStatus, ProviderHealth
from app.model_providers.errors import ErrorCategory
from tests.diagnostics_fixtures import (
    API_ROOT,
    ScriptedProbe,
    api_health_payload,
    base_environment,
    build_repository,
    build_seeded_database,
    marked_workspace,
    migrate_database,
    office_payload,
    process_payload,
    system_status_payload,
    write_supervisor_state,
)


@pytest.fixture()
def repository(tmp_path: Path) -> Path:
    return build_repository(tmp_path)


@pytest.fixture()
def seeded_database(tmp_path: Path) -> Path:
    return build_seeded_database(tmp_path)


API_URL = "http://127.0.0.1:8000"
WEB_URL = "http://127.0.0.1:5173"
HEALTH_URL = f"{API_URL}/api/health"
STATUS_URL = f"{API_URL}/api/system/status"
OFFICE_URL = f"{API_URL}/api/office"
WEB_URL_ROOT = f"{WEB_URL}/"

WORKER_ENV = {
    "JARVIS_AUTONOMOUS_WORKER_ENABLED": "true",
    "JARVIS_AUTONOMOUS_WORKER_ACTOR_ID": "actor-1",
    "JARVIS_AUTONOMOUS_WORKER_INSTANCE_ID": "instance-1",
    "JARVIS_MODEL_EXECUTION_MODE": "local_only",
    "JARVIS_MODEL_OLLAMA_ENABLED": "true",
    "JARVIS_MODEL_OLLAMA_MODEL": "llama-test",
}

DISABLED_WORKER_PAYLOAD = api_health_payload()


def healthy_worker_payload(*, component: dict | None = None, **payload_overrides: object) -> dict:
    worker_component = {
        "enabled": True,
        "modelExecutionMode": "local_only",
        "workerActorId": "actor-1",
        "activeExecutionCount": 0,
        "queuedEligibleRuntimeCount": 0,
        "providerReady": True,
        "status": "healthy",
        "reasonCode": None,
    }
    worker_component.update(component or {})
    return api_health_payload(autonomousWorker=worker_component, **payload_overrides)


def provider_health(**overrides: object) -> ProviderHealth:
    values = {
        "provider": "ollama",
        "healthy": True,
        "status": HealthStatus.HEALTHY,
        "latency_ms": 3.0,
        "model_available": True,
    }
    values.update(overrides)
    return ProviderHealth(**values)


def status_of(report, name: str) -> CheckStatus:
    result = report.check(name)
    assert result is not None, f"check {name!r} missing from report"
    return result.status


def run(
    repository: Path,
    environment: dict[str, str],
    probe: ScriptedProbe | None = None,
    *,
    deep: bool = False,
    provider_health=None,
):
    return run_diagnostics(
        repository,
        environ=environment,
        deep=deep,
        http_probe=probe or ScriptedProbe(),
        provider_health=provider_health,
    )


def offline_probe() -> ScriptedProbe:
    return ScriptedProbe()


def no_tcp(monkeypatch: pytest.MonkeyPatch, connected: bool, error: str | None = None) -> None:
    monkeypatch.setattr(doctor_checks, "tcp_connect", lambda url, timeout=1.5: (connected, error))


# ---------------------------------------------------------------------------
# Healthy state
# ---------------------------------------------------------------------------


def test_healthy_state(
    tmp_path: Path, repository: Path, seeded_database: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    no_tcp(monkeypatch, connected=False)
    workspace = marked_workspace(tmp_path)
    environment = base_environment(tmp_path, repository, seeded_database)
    environment.update(WORKER_ENV)
    environment["JARVIS_TOOL_EXECUTION_ENABLED"] = "true"
    environment["JARVIS_TOOL_WORKSPACES_JSON"] = f'{{"reports": "{workspace.as_posix()}"}}'
    write_supervisor_state(
        repository,
        environment,
        processes={
            "api": process_payload("api"),
            "web": process_payload("web"),
            "autonomous_worker": process_payload("autonomous_worker", enabled=True),
        },
    )
    probe = ScriptedProbe(
        {
            HEALTH_URL: HealthResult(True, "healthy", payload=healthy_worker_payload()),
            WEB_URL_ROOT: HealthResult(True, "healthy"),
            OFFICE_URL: HealthResult(True, "healthy", payload=office_payload()),
            STATUS_URL: HealthResult(
                True, "healthy", payload=system_status_payload(emergencyStop=False)
            ),
        }
    )
    run_ = run(
        repository,
        environment,
        probe,
        deep=True,
        provider_health=lambda settings: {"ollama": provider_health()},
    )
    report = run_.report
    assert report.overall == CheckStatus.HEALTHY, [
        (item.name, item.status.value, item.reason) for item in report.checks
    ]
    assert report.ready_to_run is True
    assert report.blocked_reasons == ()
    identity = status_of(report, "repository_identity") and report.check("repository_identity")
    assert identity.identifiers["gitSha"]
    database = report.check("database")
    assert database.identifiers["databaseRevision"] == DATABASE_REVISION
    worker = report.check("autonomous_worker")
    assert worker.identifiers["workerState"] == "idle"
    readiness = report.check("planning_readiness")
    assert readiness.identifiers["autonomousOperation"] == "ready"
    workspace_check = report.check("workspace_tools")
    assert workspace_check.identifiers["configuredAliases"] == ["reports"]


# ---------------------------------------------------------------------------
# API availability and identity
# ---------------------------------------------------------------------------


def test_api_unavailable_reports_not_running(
    repository: Path, seeded_database: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    no_tcp(monkeypatch, connected=False, error="ConnectionRefusedError")
    environment = base_environment(Path("/tmp"), repository, seeded_database)
    report = run(repository, environment, offline_probe()).report
    api = report.check("api")
    assert api.status == CheckStatus.BLOCKED
    assert "not running" in api.reason
    assert "127.0.0.1:8000" in api.reason
    assert api.identifiers["expectedPort"] == 8000
    assert report.overall == CheckStatus.BLOCKED
    assert any(item.startswith("api:") for item in report.blocked_reasons)


def test_api_port_in_use_is_distinguished_from_not_running(
    repository: Path, seeded_database: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    no_tcp(monkeypatch, connected=True)
    environment = base_environment(Path("/tmp"), repository, seeded_database)
    report = run(repository, environment, offline_probe()).report
    api = report.check("api")
    assert api.status == CheckStatus.BLOCKED
    assert "accepts connections" in api.reason
    assert "never kills processes" in (api.remediation or "")


def test_api_reachable_but_malformed_response(repository: Path, seeded_database: Path) -> None:
    environment = base_environment(Path("/tmp"), repository, seeded_database)
    probe = ScriptedProbe({HEALTH_URL: HealthResult(True, "degraded", "invalid JSON response")})
    report = run(repository, environment, probe).report
    api = report.check("api")
    assert api.status == CheckStatus.DEGRADED
    assert "health response is invalid" in api.reason


def test_api_reports_wrong_service_identity(repository: Path, seeded_database: Path) -> None:
    environment = base_environment(Path("/tmp"), repository, seeded_database)
    probe = ScriptedProbe(
        {HEALTH_URL: HealthResult(True, "healthy", payload=api_health_payload(service="other"))}
    )
    report = run(repository, environment, probe).report
    api = report.check("api")
    assert api.status == CheckStatus.DEGRADED
    assert "service identity" in api.reason


def test_api_application_degraded_reports_the_reasons(
    repository: Path, seeded_database: Path
) -> None:
    environment = base_environment(Path("/tmp"), repository, seeded_database)
    payload = api_health_payload(
        status="degraded",
        databaseReachable=False,
        schemaCurrent=False,
        recoveryRequired=True,
        outboxExhaustedCount=2,
        expiredLeaseCount=1,
    )
    probe = ScriptedProbe({HEALTH_URL: HealthResult(True, "degraded", payload=payload)})
    report = run(repository, environment, probe).report
    api = report.check("api")
    assert api.status == CheckStatus.DEGRADED
    for fragment in (
        "database unreachable",
        "schema not current",
        "recovery required",
        "exhausted outbox",
        "expired task leases",
    ):
        assert fragment in api.reason


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


def test_database_missing_file_is_blocked(repository: Path, tmp_path: Path) -> None:
    environment = base_environment(tmp_path, repository, tmp_path / "does-not-exist.db")
    report = run(repository, environment, offline_probe()).report
    database = report.check("database")
    assert database.status == CheckStatus.BLOCKED
    assert "does not exist" in database.reason
    assert "alembic upgrade head" in (database.remediation or "")
    assert "never migrates automatically" in (database.remediation or "")


def test_database_migration_mismatch_reports_revisions(repository: Path, tmp_path: Path) -> None:
    database = migrate_database(tmp_path / "behind.db", "20260729_04")
    environment = base_environment(tmp_path, repository, database)
    report = run(repository, environment, offline_probe()).report
    database_check = report.check("database")
    assert database_check.status == CheckStatus.BLOCKED
    assert "20260729_04" in database_check.reason
    assert DATABASE_REVISION in database_check.reason
    assert database_check.identifiers["databaseRevision"] == "20260729_04"
    assert database_check.identifiers["expectedRevision"] == DATABASE_REVISION
    assert report.database_revision == "20260729_04"


def test_database_foreign_revision_is_reported(repository: Path, tmp_path: Path) -> None:
    database = migrate_database(tmp_path / "foreign.db", "head")
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE alembic_version SET version_num = '99999999_unknown'")
        connection.commit()
    environment = base_environment(tmp_path, repository, database)
    report = run(repository, environment, offline_probe()).report
    database_check = report.check("database")
    assert database_check.status == CheckStatus.BLOCKED
    assert "behind or foreign" in database_check.reason


def test_database_missing_core_tables_is_blocked(repository: Path, tmp_path: Path) -> None:
    database = tmp_path / "empty-schema.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        connection.execute("INSERT INTO alembic_version VALUES (?)", (DATABASE_REVISION,))
        connection.commit()
    environment = base_environment(tmp_path, repository, database)
    report = run(repository, environment, offline_probe()).report
    database_check = report.check("database")
    assert database_check.status == CheckStatus.BLOCKED
    assert "missing core tables" in database_check.reason


def test_database_deep_integrity_failure(repository: Path, tmp_path: Path) -> None:
    database = migrate_database(tmp_path / "corrupt.db", "head")
    raw = database.read_bytes()
    database.write_bytes(raw[: max(1, len(raw) // 3)] + raw[len(raw) // 3 + 7 :])
    environment = base_environment(tmp_path, repository, database)
    report = run(repository, environment, offline_probe(), deep=True).report
    database_check = report.check("database")
    assert database_check.status == CheckStatus.BLOCKED
    assert (
        "integrity quick_check failed" in database_check.reason
        or "cannot be opened" in database_check.reason
        or "revision" in database_check.reason
    )


def test_database_path_with_spaces_is_read_correctly(repository: Path, tmp_path: Path) -> None:
    # Windows installations routinely live under paths with spaces; the
    # read-only URI must stay correct there (mirrors supervisor behavior).
    database = migrate_database(tmp_path / "Repository Data" / "runtime db.db", "head")
    environment = base_environment(tmp_path, repository, database)
    report = run(repository, environment, offline_probe()).report
    database_check = report.check("database")
    assert database_check.status == CheckStatus.HEALTHY, database_check.reason
    assert database_check.identifiers["databaseRevision"] == DATABASE_REVISION


def test_database_supports_only_sqlite_urls(repository: Path, tmp_path: Path) -> None:
    environment = base_environment(tmp_path, repository, tmp_path / "ignored.db")
    environment["JARVIS_DATABASE_URL"] = "postgresql://localhost/jarvis"
    report = run(repository, environment, offline_probe()).report
    database_check = report.check("database")
    assert database_check.status == CheckStatus.BLOCKED
    assert "SQLite" in database_check.reason


# ---------------------------------------------------------------------------
# Emergency stop
# ---------------------------------------------------------------------------


def test_emergency_stop_active_blocks_readiness(repository: Path, seeded_database: Path) -> None:
    with sqlite3.connect(seeded_database) as connection:
        connection.execute("UPDATE system_state SET emergency_stop = 1 WHERE id = 1")
        connection.commit()
    environment = base_environment(Path("/tmp"), repository, seeded_database)
    probe = ScriptedProbe({HEALTH_URL: HealthResult(True, "healthy", payload=api_health_payload())})
    report = run(repository, environment, probe).report
    emergency = report.check("emergency_stop")
    assert emergency.status == CheckStatus.BLOCKED
    assert emergency.identifiers["active"] is True
    assert "never clears emergency stop" in (emergency.remediation or "")
    readiness = report.check("planning_readiness")
    assert readiness.status == CheckStatus.DISABLED  # worker disabled in this scenario
    assert report.overall == CheckStatus.BLOCKED


def test_emergency_stop_not_active_when_durable_state_clear(
    repository: Path, seeded_database: Path
) -> None:
    environment = base_environment(Path("/tmp"), repository, seeded_database)
    probe = ScriptedProbe({HEALTH_URL: HealthResult(True, "healthy", payload=api_health_payload())})
    report = run(repository, environment, probe).report
    emergency = report.check("emergency_stop")
    assert emergency.status == CheckStatus.HEALTHY
    assert emergency.identifiers["active"] is False
    assert emergency.identifiers["source"] == "durable system state"


def test_emergency_stop_unknown_without_any_source(repository: Path, tmp_path: Path) -> None:
    environment = base_environment(tmp_path, repository, tmp_path / "missing.db")
    report = run(repository, environment, offline_probe()).report
    emergency = report.check("emergency_stop")
    assert emergency.status == CheckStatus.UNKNOWN


# ---------------------------------------------------------------------------
# Supervisor
# ---------------------------------------------------------------------------


def test_supervisor_stale_state_is_blocked(repository: Path, seeded_database: Path) -> None:
    environment = base_environment(Path("/tmp"), repository, seeded_database)
    write_supervisor_state(repository, environment, pid=0x7FFFFFF0)
    probe = ScriptedProbe({HEALTH_URL: HealthResult(True, "healthy", payload=api_health_payload())})
    report = run(repository, environment, probe).report
    supervisor = report.check("supervisor")
    assert supervisor.status == CheckStatus.BLOCKED
    assert "stale supervisor state" in supervisor.reason
    assert "stop" in (supervisor.remediation or "")


def test_supervisor_not_running_is_degraded_not_blocked(
    repository: Path, seeded_database: Path
) -> None:
    environment = base_environment(Path("/tmp"), repository, seeded_database)
    probe = ScriptedProbe({HEALTH_URL: HealthResult(True, "healthy", payload=api_health_payload())})
    report = run(repository, environment, probe).report
    supervisor = report.check("supervisor")
    assert supervisor.status == CheckStatus.DEGRADED
    assert "not running" in supervisor.reason
    assert report.overall == CheckStatus.DEGRADED


def test_supervisor_required_child_failed(repository: Path, seeded_database: Path) -> None:
    environment = base_environment(Path("/tmp"), repository, seeded_database)
    write_supervisor_state(
        repository,
        environment,
        processes={
            "api": process_payload("api", state="failed", health="failed"),
            "web": process_payload("web"),
            "autonomous_worker": process_payload(
                "autonomous_worker", enabled=False, state="disabled", health="disabled"
            ),
        },
    )
    report = run(repository, environment, offline_probe()).report
    supervisor = report.check("supervisor")
    assert supervisor.status == CheckStatus.BLOCKED
    assert "api process is failed" in supervisor.reason


def test_supervisor_restart_history_is_degraded(repository: Path, seeded_database: Path) -> None:
    environment = base_environment(Path("/tmp"), repository, seeded_database)
    write_supervisor_state(
        repository,
        environment,
        processes={
            "api": process_payload(
                "api",
                restartCount=2,
                lastFailure="2026-09-08 health endpoint unavailable",
                failureHistory=["2026-09-08 health endpoint unavailable"],
            ),
            "web": process_payload("web"),
            "autonomous_worker": process_payload(
                "autonomous_worker", enabled=False, state="disabled", health="disabled"
            ),
        },
    )
    report = run(repository, environment, offline_probe()).report
    supervisor = report.check("supervisor")
    assert supervisor.status == CheckStatus.DEGRADED
    assert "restarted 2" in supervisor.reason
    failures = report.check("recent_failures")
    assert failures.status == CheckStatus.DEGRADED
    assert "health endpoint unavailable" in failures.reason


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


def test_provider_unavailable(repository: Path, seeded_database: Path) -> None:
    environment = base_environment(Path("/tmp"), repository, seeded_database)
    environment.update(WORKER_ENV)
    health = provider_health(
        healthy=False,
        status=HealthStatus.UNAVAILABLE,
        model_available=None,
        error_category=ErrorCategory.PROVIDER_UNAVAILABLE.value,
        detail="connection refused",
    )
    report = run(
        repository,
        environment,
        offline_probe(),
        provider_health=lambda settings: {"ollama": health},
    ).report
    provider = report.check("model_provider")
    assert provider.status == CheckStatus.BLOCKED
    assert "unreachable" in provider.reason


def test_provider_configured_model_missing(repository: Path, seeded_database: Path) -> None:
    environment = base_environment(Path("/tmp"), repository, seeded_database)
    environment.update(WORKER_ENV)
    health = provider_health(model_available=False)
    report = run(
        repository,
        environment,
        offline_probe(),
        provider_health=lambda settings: {"ollama": health},
    ).report
    provider = report.check("model_provider")
    assert provider.status == CheckStatus.BLOCKED
    assert "configured model is missing" in provider.reason
    assert "never downloads models" in (provider.remediation or "")


def test_provider_malformed_response(repository: Path, seeded_database: Path) -> None:
    environment = base_environment(Path("/tmp"), repository, seeded_database)
    environment.update(WORKER_ENV)
    health = provider_health(
        healthy=False,
        status=HealthStatus.UNAVAILABLE,
        model_available=None,
        error_category=ErrorCategory.MALFORMED_PROVIDER_RESPONSE.value,
        detail="Ollama model-list response is malformed",
    )
    report = run(
        repository,
        environment,
        offline_probe(),
        provider_health=lambda settings: {"ollama": health},
    ).report
    provider = report.check("model_provider")
    assert provider.status == CheckStatus.BLOCKED
    assert "malformed response" in provider.reason


def test_provider_none_configured_while_local_only(repository: Path, seeded_database: Path) -> None:
    environment = base_environment(Path("/tmp"), repository, seeded_database)
    environment.update(
        {key: value for key, value in WORKER_ENV.items() if key != "JARVIS_MODEL_OLLAMA_ENABLED"}
    )
    report = run(
        repository, environment, offline_probe(), provider_health=lambda settings: {}
    ).report
    provider = report.check("model_provider")
    assert provider.status == CheckStatus.BLOCKED
    assert "no model provider is enabled" in provider.reason


def test_provider_configuration_only_is_degraded(repository: Path, seeded_database: Path) -> None:
    environment = base_environment(Path("/tmp"), repository, seeded_database)
    environment.update(WORKER_ENV)
    health = provider_health(
        status=HealthStatus.CONFIGURATION_ONLY, model_available=None, healthy=True
    )
    report = run(
        repository,
        environment,
        offline_probe(),
        provider_health=lambda settings: {"ollama": health},
    ).report
    provider = report.check("model_provider")
    assert provider.status == CheckStatus.DEGRADED


def test_provider_disabled_when_model_execution_disabled(
    repository: Path, seeded_database: Path
) -> None:
    environment = base_environment(Path("/tmp"), repository, seeded_database)
    report = run(repository, environment, offline_probe()).report
    provider = report.check("model_provider")
    assert provider.status == CheckStatus.DISABLED


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


def test_worker_disabled_is_not_a_failure(repository: Path, seeded_database: Path) -> None:
    environment = base_environment(Path("/tmp"), repository, seeded_database)
    probe = ScriptedProbe({HEALTH_URL: HealthResult(True, "healthy", payload=api_health_payload())})
    report = run(repository, environment, probe).report
    worker = report.check("autonomous_worker")
    assert worker.status == CheckStatus.DISABLED
    assert worker.identifiers["enabled"] is False
    readiness = report.check("planning_readiness")
    assert readiness.status == CheckStatus.DISABLED


def test_worker_unavailable_is_blocked(repository: Path, seeded_database: Path) -> None:
    environment = base_environment(Path("/tmp"), repository, seeded_database)
    environment.update(WORKER_ENV)
    payload = healthy_worker_payload(
        component={"status": "degraded", "reasonCode": "autonomous_worker_unavailable"}
    )
    probe = ScriptedProbe({HEALTH_URL: HealthResult(True, "healthy", payload=payload)})
    report = run(
        repository,
        environment,
        probe,
        provider_health=lambda settings: {"ollama": provider_health()},
    ).report
    worker = report.check("autonomous_worker")
    assert worker.status == CheckStatus.BLOCKED
    assert worker.identifiers["workerState"] == "absent"
    assert worker.identifiers["reasonCode"] == "autonomous_worker_unavailable"
    readiness = report.check("planning_readiness")
    assert readiness.status == CheckStatus.BLOCKED
    assert "worker process not running" in readiness.reason


def test_worker_idle_and_active_states(repository: Path, seeded_database: Path) -> None:
    environment = base_environment(Path("/tmp"), repository, seeded_database)
    environment.update(WORKER_ENV)
    idle = healthy_worker_payload(
        component={"activeExecutionCount": 1, "queuedEligibleRuntimeCount": 2}
    )
    probe = ScriptedProbe({HEALTH_URL: HealthResult(True, "healthy", payload=idle)})
    report = run(
        repository,
        environment,
        probe,
        provider_health=lambda settings: {"ollama": provider_health()},
    ).report
    worker = report.check("autonomous_worker")
    assert worker.status == CheckStatus.HEALTHY
    assert worker.identifiers["workerState"] == "active"
    assert "executing" in worker.reason


def test_worker_stale_by_lease_authority(repository: Path, seeded_database: Path) -> None:
    environment = base_environment(Path("/tmp"), repository, seeded_database)
    environment.update(WORKER_ENV)
    payload = healthy_worker_payload(staleWorkerCount=1)
    probe = ScriptedProbe({HEALTH_URL: HealthResult(True, "healthy", payload=payload)})
    report = run(
        repository,
        environment,
        probe,
        provider_health=lambda settings: {"ollama": provider_health()},
    ).report
    worker = report.check("autonomous_worker")
    assert worker.status == CheckStatus.DEGRADED
    assert worker.identifiers["workerState"] == "stale"
    assert "stale worker" in worker.reason


def test_worker_process_failed_in_supervisor(repository: Path, seeded_database: Path) -> None:
    environment = base_environment(Path("/tmp"), repository, seeded_database)
    environment.update(WORKER_ENV)
    write_supervisor_state(
        repository,
        environment,
        processes={
            "api": process_payload("api"),
            "web": process_payload("web"),
            "autonomous_worker": process_payload(
                "autonomous_worker",
                enabled=True,
                state="failed",
                health="failed",
                lastFailure="2026-09-08 process exited with code 1",
            ),
        },
    )
    report = run(repository, environment, offline_probe()).report
    worker = report.check("autonomous_worker")
    assert worker.status == CheckStatus.BLOCKED
    assert worker.identifiers["workerState"] == "failed"
    assert "process exited with code 1" in worker.reason


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_configuration_invalid_settings_is_blocked(repository: Path, tmp_path: Path) -> None:
    environment = base_environment(tmp_path, repository, tmp_path / "missing.db")
    environment["WEB_ORIGIN"] = "http://example.invalid"
    report = run(repository, environment, offline_probe()).report
    configuration = report.check("configuration")
    assert configuration.status == CheckStatus.BLOCKED
    assert "settings invalid" in configuration.reason


def test_configuration_worker_enabled_without_actor(repository: Path, tmp_path: Path) -> None:
    environment = base_environment(tmp_path, repository, tmp_path / "missing.db")
    environment.update(
        {
            "JARVIS_AUTONOMOUS_WORKER_ENABLED": "true",
            "JARVIS_MODEL_EXECUTION_MODE": "local_only",
            "JARVIS_MODEL_OLLAMA_ENABLED": "true",
        }
    )
    report = run(repository, environment, offline_probe()).report
    configuration = report.check("configuration")
    assert configuration.status == CheckStatus.BLOCKED
    assert "settings invalid" in configuration.reason or "supervisor" in configuration.reason


# ---------------------------------------------------------------------------
# Workspace tools
# ---------------------------------------------------------------------------


def test_workspace_tools_disabled_is_not_a_failure(repository: Path, seeded_database: Path) -> None:
    environment = base_environment(Path("/tmp"), repository, seeded_database)
    report = run(repository, environment, offline_probe()).report
    workspace = report.check("workspace_tools")
    assert workspace.status == CheckStatus.DISABLED
    assert workspace.identifiers["enabled"] is False


def test_workspace_configuration_invalid(repository: Path, seeded_database: Path) -> None:
    environment = base_environment(Path("/tmp"), repository, seeded_database)
    environment["JARVIS_TOOL_EXECUTION_ENABLED"] = "true"
    environment["JARVIS_TOOL_WORKSPACES_JSON"] = "{not json"
    report = run(repository, environment, offline_probe()).report
    workspace = report.check("workspace_tools")
    assert workspace.status == CheckStatus.BLOCKED
    assert "TOOL_CONFIG_INVALID" in workspace.reason


def test_workspace_marker_missing(repository: Path, seeded_database: Path, tmp_path: Path) -> None:
    unmarked = tmp_path / "unmarked-workspace"
    unmarked.mkdir()
    environment = base_environment(Path("/tmp"), repository, seeded_database)
    environment["JARVIS_TOOL_EXECUTION_ENABLED"] = "true"
    environment["JARVIS_TOOL_WORKSPACES_JSON"] = f'{{"reports": "{unmarked.as_posix()}"}}'
    report = run(repository, environment, offline_probe()).report
    workspace = report.check("workspace_tools")
    assert workspace.status == CheckStatus.BLOCKED
    assert workspace.identifiers["unreadyWorkspaces"] == {"reports": "TOOL_WORKSPACE_UNMARKED"}


def test_workspace_enabled_without_workspaces_is_degraded(
    repository: Path, seeded_database: Path
) -> None:
    environment = base_environment(Path("/tmp"), repository, seeded_database)
    environment["JARVIS_TOOL_EXECUTION_ENABLED"] = "true"
    environment["JARVIS_TOOL_WORKSPACES_JSON"] = "{}"
    report = run(repository, environment, offline_probe()).report
    workspace = report.check("workspace_tools")
    assert workspace.status == CheckStatus.DEGRADED
    assert "no workspaces are configured" in workspace.reason


# ---------------------------------------------------------------------------
# Office / runtime state path
# ---------------------------------------------------------------------------


def test_office_contract_valid(repository: Path, seeded_database: Path) -> None:
    environment = base_environment(Path("/tmp"), repository, seeded_database)
    probe = ScriptedProbe(
        {
            HEALTH_URL: HealthResult(True, "healthy", payload=api_health_payload()),
            OFFICE_URL: HealthResult(True, "healthy", payload=office_payload()),
        }
    )
    report = run(repository, environment, probe).report
    office = report.check("office_runtime_path")
    assert office.status == CheckStatus.HEALTHY


def test_office_contract_invalid(repository: Path, seeded_database: Path) -> None:
    environment = base_environment(Path("/tmp"), repository, seeded_database)
    probe = ScriptedProbe(
        {
            HEALTH_URL: HealthResult(True, "healthy", payload=api_health_payload()),
            OFFICE_URL: HealthResult(True, "healthy", payload={"unexpected": True}),
        }
    )
    report = run(repository, environment, probe).report
    office = report.check("office_runtime_path")
    assert office.status == CheckStatus.DEGRADED
    assert "missing fields" in office.reason


def test_office_emergency_state_mismatch_in_deep_mode(
    repository: Path, seeded_database: Path
) -> None:
    environment = base_environment(Path("/tmp"), repository, seeded_database)
    probe = ScriptedProbe(
        {
            HEALTH_URL: HealthResult(True, "healthy", payload=api_health_payload()),
            OFFICE_URL: HealthResult(True, "healthy", payload=office_payload(emergencyStop=True)),
        }
    )
    report = run(repository, environment, probe, deep=True).report
    office = report.check("office_runtime_path")
    assert office.status == CheckStatus.DEGRADED
    assert "does not match durable system state" in office.reason


# ---------------------------------------------------------------------------
# Recent failures
# ---------------------------------------------------------------------------


def test_recent_failures_from_api_health(repository: Path, seeded_database: Path) -> None:
    environment = base_environment(Path("/tmp"), repository, seeded_database)
    payload = api_health_payload(
        status="degraded", recoveryRequired=True, expiredLeaseCount=2, staleWorkerCount=1
    )
    probe = ScriptedProbe({HEALTH_URL: HealthResult(True, "degraded", payload=payload)})
    report = run(repository, environment, probe).report
    failures = report.check("recent_failures")
    assert failures.status == CheckStatus.DEGRADED
    assert "recovery required" in failures.reason
    assert "expired task lease" in failures.reason
    assert "stale worker" in failures.reason


def test_recent_failures_unknown_without_sources(repository: Path, tmp_path: Path) -> None:
    environment = base_environment(tmp_path, repository, tmp_path / "missing.db")
    report = run(repository, environment, offline_probe()).report
    failures = report.check("recent_failures")
    assert failures.status == CheckStatus.UNKNOWN


# ---------------------------------------------------------------------------
# Migration-script expectation sanity (guards the doctor's own oracle)
# ---------------------------------------------------------------------------


def test_doctor_expects_the_real_repository_migration_head() -> None:
    from app.diagnostics.probes import alembic_heads

    heads = alembic_heads(API_ROOT / "migrations")
    # The doctor's expected-revision oracle must always track the application's
    # declared DATABASE_REVISION, including after future migrations merge.
    assert heads == {DATABASE_REVISION}
