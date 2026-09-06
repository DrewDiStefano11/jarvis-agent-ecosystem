from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import sys
import tempfile
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi import FastAPI, Header, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.agent_runtime.authorization import IdentityRuntimeAuthorizer
from app.agent_runtime.errors import AgentRuntimeError
from app.agent_runtime.router import router as agent_runtime_router
from app.agent_runtime.service import AgentRuntimeService
from app.agent_runtime.sqlalchemy_repository import SqlAlchemyAgentRuntimeRepository
from app.autonomous_worker.repository import ModelExecutionRepository
from app.autonomous_worker.router import router as autonomous_worker_router
from app.autonomous_worker.service import AutonomousWorkerService
from app.autonomous_worker.setup_router import router as local_planning_setup_router
from app.catalog.router import router as catalog_router
from app.catalog.service import CatalogService
from app.context import ContextAssembler
from app.context.enrichment import ContextEnricher
from app.core.config import Settings
from app.core.errors import DomainError
from app.core.transitions import InvalidTransitionError, validate_transition
from app.db.session import create_database_engine, create_session_factory
from app.identity.router import router as identity_router
from app.identity.service import IdentityService
from app.model_providers.factory import build_model_router
from app.models.autonomous_worker import AutonomousWorkerStatus
from app.models.context import (
    ContextAssembly,
    ContextAssemblyEventPayload,
    ContextAssemblyListResponse,
    ContextAssemblyResponse,
    ContextSourceType,
    CreateContextAssemblyRequest,
    TrustLevel,
)
from app.models.domain import (
    AcquireTaskLeaseRequest,
    Agent,
    ApiResponse,
    Approval,
    CompleteTaskLeaseRequest,
    CreateTaskRequest,
    CreateTemporaryAgentRequest,
    DecisionRequest,
    EditApprovalRequest,
    FailTaskLeaseRequest,
    FailureRequest,
    LeaseCommandRequest,
    Notification,
    Office,
    Performance,
    RegisterWorkerRequest,
    RenewTaskLeaseRequest,
    ResourceStatus,
    SimulatorControl,
    SystemStatus,
    Task,
    TypedApiResponse,
)
from app.office.router import router as office_router
from app.office.service import OfficeService
from app.repositories.sqlalchemy import IdempotencyResult, SqlAlchemyRepository
from app.repositories.task_leases import TaskLeaseRepository
from app.services.events import EventBroker
from app.services.task_creation import prepare_task_creation
from app.simulator.engine import SimulatorEngine
from app.tool_execution.router import router as tool_execution_router
from app.tool_execution.service import ToolExecutionService

DATABASE_REVISION = "20260906_09"
IdempotencyKeyHeader = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=200,
        pattern=r"^[^\x00-\x1f\x7f]+$",
    ),
]


def _is_loopback_peer(host: str | None) -> bool:
    if host == "testclient":
        # Starlette's in-process test transport has no network peer. This literal
        # cannot be produced by a direct TCP connection.
        return True
    if not host:
        return False
    try:
        return ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def _upgrade_database(settings: Settings) -> None:
    config_path = Path(__file__).resolve().parents[1] / "alembic.ini"
    config = Config(str(config_path))
    config.set_main_option("script_location", str(config_path.parent / "migrations"))
    config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
    command.upgrade(config, "head")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    repository = app.state.repository
    broker = app.state.broker
    simulator = app.state.simulator
    settings = app.state.settings
    task_leases = app.state.task_leases
    restored_workflow_state = app.state.restored_workflow_state
    lease_recovery_task = None
    office_recovery_task = None
    startup_completed = False

    async def recover_expired_task_leases() -> None:
        while True:
            await asyncio.sleep(settings.task_lease_recovery_interval_ms / 1000)
            database_reachable, schema_current = repository.health_probe(DATABASE_REVISION)
            if not database_reachable or not schema_current:
                continue
            recovered = task_leases.recover_expired_leases()
            if recovered:
                await broker.dispatch_pending()

    async def reconcile_office() -> None:
        while True:
            await asyncio.sleep(0.5)
            try:
                database_reachable, schema_current = repository.health_probe(DATABASE_REVISION)
                if not database_reachable or not schema_current:
                    continue
                if app.state.office_service.reconcile():
                    repository.refresh_event_cursor()
                    await broker.dispatch_pending()
            except SQLAlchemyError:
                # A transient lock or unavailable database must not permanently
                # stop durable arrival/lifecycle recovery for this API process.
                logging.getLogger(__name__).warning(
                    "Office reconciliation will retry after a database error."
                )

    try:
        repository.record_process_lifecycle(starting=True)
        await broker.dispatch_pending()
        await broker.start_dispatcher(settings.outbox_poll_interval_ms)
        database_reachable, schema_current = repository.health_probe(DATABASE_REVISION)
        if database_reachable and schema_current:
            task_leases.recover_expired_leases()
            await broker.dispatch_pending()
        lease_recovery_task = asyncio.create_task(recover_expired_task_leases())
        if database_reachable and schema_current:
            app.state.office_service.reconcile()
            repository.refresh_event_cursor()
        office_recovery_task = asyncio.create_task(reconcile_office())
        app.state.lease_recovery_task = lease_recovery_task
        app.state.office_recovery_task = office_recovery_task
        if restored_workflow_state == "recovery_required" and settings.simulator_auto_resume:
            await simulator.resume()
        startup_completed = True
        yield
    finally:
        if office_recovery_task:
            office_recovery_task.cancel()
            try:
                await office_recovery_task
            except asyncio.CancelledError:
                pass
        if lease_recovery_task:
            lease_recovery_task.cancel()
            try:
                await lease_recovery_task
            except asyncio.CancelledError:
                pass
        if simulator._runner and not simulator._runner.done():
            simulator._stopped = True
            simulator._runner.cancel()
            try:
                await simulator._runner
            except asyncio.CancelledError:
                pass
        await broker.stop_dispatcher()
        if startup_completed:
            repository.record_process_lifecycle(starting=False)
        app.state.engine.dispose()


def create_app(
    delay_ms: int | None = None,
    database_url: str | None = None,
    *,
    recover_interrupted_workflow: bool = True,
) -> FastAPI:
    settings = Settings(JARVIS_DATABASE_URL=database_url) if database_url else Settings()
    settings.ensure_runtime_directory()
    if settings.auto_migrate:
        _upgrade_database(settings)
    engine = create_database_engine(settings.database_url, settings.sql_echo)
    session_factory = create_session_factory(engine)
    repository = SqlAlchemyRepository(
        session_factory,
        settings.idempotency_lease_seconds,
        settings.outbox_max_attempts,
    )
    task_leases = TaskLeaseRepository(repository, session_factory, settings.task_lease_seconds)
    restored_workflow_state = (
        repository.mark_interrupted_workflow() if recover_interrupted_workflow else None
    )
    app = FastAPI(
        title="Jarvis Agent Ecosystem Simulator",
        version="0.1.0",
        description=(
            "Durable local Jarvis control plane with one explicitly queued, "
            "disabled-by-default local planning/review worker."
        ),
        lifespan=_lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.web_origin],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["Content-Type", "Idempotency-Key", "X-Jarvis-Actor-Id"],
    )
    broker = EventBroker(repository)
    approval_decision_locks: dict[str, asyncio.Lock] = {}
    simulator = SimulatorEngine(
        repository,
        broker,
        delay_ms if delay_ms is not None else int(os.getenv("SIMULATOR_DELAY_MS", "800")),
    )
    context_assembler = ContextAssembler(
        maximum_sources=settings.context_maximum_sources,
        maximum_tokens=settings.context_maximum_tokens,
        maximum_total_characters=settings.context_maximum_total_characters,
        cross_project_context_allowed=settings.context_cross_project_allowed,
    )
    if restored_workflow_state:
        active = repository.active_workflow()
        simulator.control.state = restored_workflow_state
        simulator.control.currentStep = active.current_step_index if active else 0
    elif repository._system.simulator_status in {"completed", "failed"}:
        simulator.control.state = repository._system.simulator_status
        if repository._system.last_checkpoint_id:
            checkpoint = repository.load_checkpoint(repository._system.last_checkpoint_id)
            simulator.control.currentStep = int(checkpoint["stepIndex"])
    app.state.repository = repository
    app.state.broker = broker
    app.state.simulator = simulator
    app.state.context_assembler = context_assembler
    app.state.settings = settings
    app.state.engine = engine
    app.state.task_leases = task_leases
    app.state.identity_service = IdentityService(session_factory)
    app.state.catalog_service = CatalogService(session_factory)
    app.state.office_service = OfficeService(session_factory)
    app.state.agent_runtime_repository = SqlAlchemyAgentRuntimeRepository(
        session_factory, outbox_max_attempts=repository.outbox_max_attempts
    )
    app.state.agent_runtime_service = AgentRuntimeService(
        app.state.agent_runtime_repository,
        authorizer=IdentityRuntimeAuthorizer(app.state.identity_service),
    )
    app.state.model_router = build_model_router(settings)
    app.state.model_execution_repository = ModelExecutionRepository(
        session_factory,
        outbox_max_attempts=repository.outbox_max_attempts,
    )
    app.state.autonomous_worker_service = AutonomousWorkerService(
        settings=settings,
        executions=app.state.model_execution_repository,
        task_leases=task_leases,
        runtime=app.state.agent_runtime_service,
        router=app.state.model_router,
    )
    app.state.tool_execution_service = ToolExecutionService(app)
    app.state.autonomous_worker_service.tool_executor = app.state.tool_execution_service
    app.include_router(tool_execution_router)
    app.include_router(agent_runtime_router)
    app.include_router(autonomous_worker_router)
    app.include_router(local_planning_setup_router)
    app.include_router(identity_router)
    app.include_router(catalog_router)
    app.include_router(office_router)
    app.state.lease_recovery_task = None
    app.state.office_recovery_task = None
    app.state.restored_workflow_state = restored_workflow_state
    app.state.recovery_required = restored_workflow_state == "recovery_required"

    def replay_idempotent(
        request: Request, key: str | None, command_type: str, payload: object
    ) -> object | None:
        if not key:
            return None
        claim = repository.idempotency_claim(key, command_type, payload)
        if claim.owned:
            assert claim.lease_expires_at is not None
            request.state.idempotency_claim = (
                key,
                command_type,
                claim.lease_expires_at,
            )
        return claim.response[1]["data"] if claim.response else None

    @app.middleware("http")
    async def cleanup_owned_idempotency_claim(request: Request, call_next):
        try:
            response = await call_next(request)
        except (Exception, asyncio.CancelledError):
            if claim := getattr(request.state, "idempotency_claim", None):
                repository.idempotency_abandon(*claim)
            raise
        if response.status_code >= 400:
            if claim := getattr(request.state, "idempotency_claim", None):
                repository.idempotency_abandon(*claim)
        return response

    @app.middleware("http")
    async def enforce_local_control_plane(request: Request, call_next):
        if not _is_loopback_peer(request.client.host if request.client else None):
            return JSONResponse(
                status_code=403,
                content={
                    "error": {
                        "code": "LOCAL_CONTROL_PLANE_ONLY",
                        "message": "This phase accepts loopback clients only.",
                        "details": {},
                    }
                },
            )
        response = await call_next(request)
        if (
            request.method in {"POST", "PATCH"}
            and request.url.path.startswith("/api/identity/agents/")
            and response.status_code < 400
        ):
            if app.state.office_service.reconcile():
                repository.refresh_event_cursor()
                await broker.dispatch_pending()
        return response

    def idempotency_result(
        request: Request,
        key: str | None,
        command_type: str,
        payload: object,
        data: object,
        status: int = 200,
        resource_id: str | None = None,
    ) -> IdempotencyResult | None:
        if not key:
            return None
        claim = getattr(request.state, "idempotency_claim", None)
        if not claim or claim[0] != key or claim[1] != command_type:
            raise RuntimeError("The request does not own the idempotency claim.")
        encoded = data.model_dump(mode="json") if hasattr(data, "model_dump") else data
        return IdempotencyResult(
            key=key,
            command=command_type,
            payload=payload,
            status=status,
            body={"data": encoded},
            lease_expires_at=claim[2],
            resource_id=resource_id,
        )

    @app.exception_handler(AgentRuntimeError)
    async def runtime_error(_: Request, exc: AgentRuntimeError) -> JSONResponse:
        missing_codes = {
            "run_not_found",
            "attempt_not_found",
            "runtime_actor_not_found",
            "runtime_parent_unavailable",
        }
        input_codes = {
            "invalid_runtime_metadata",
            "invalid_runtime_identifier",
            "runtime_actor_mismatch",
        }
        internal_codes = {"ledger_replay_error", "runtime_persistence_error"}
        auth_required_codes = {"runtime_authentication_required"}
        permission_codes = {
            "runtime_actor_inactive",
            "runtime_permission_denied",
            "runtime_replay_actor_mismatch",
        }
        status = (
            401
            if exc.code in auth_required_codes
            else 404
            if exc.code in missing_codes
            else 403
            if exc.code in permission_codes
            else 400
            if exc.code in input_codes
            else 500
            if exc.code in internal_codes
            else 409
        )
        return JSONResponse(
            status_code=status,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": {
                        "runId": exc.run_id,
                        "attemptId": exc.attempt_id,
                        "commandId": exc.command_id,
                        "metadata": exc.metadata,
                    },
                }
            },
        )

    @app.exception_handler(DomainError)
    async def domain_error(_: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message, "details": {}}},
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI's default response includes the rejected input. That can reflect a
        # credential, oversized payload, or other sensitive value supplied at a
        # boundary. Return only bounded structural diagnostics.
        issues = []
        for error in exc.errors()[:32]:
            location = [
                str(part)[:80] for part in error.get("loc", ()) if isinstance(part, str | int)
            ][:12]
            issues.append(
                {
                    "location": location,
                    "type": str(error.get("type", "value_error"))[:80],
                }
            )
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "REQUEST_VALIDATION_ERROR",
                    "message": "The request did not satisfy the API contract.",
                    "details": {
                        "issueCount": min(len(exc.errors()), 32),
                        "issues": issues,
                        "truncated": len(exc.errors()) > 32,
                    },
                }
            },
        )

    def _bounded_runtime_health(reason_code: str) -> dict:
        """Bounded runtime component result; it never carries internal detail."""
        return {
            "configured": False,
            "nonterminalRunCount": 0,
            "status": "unavailable",
            "reasonCode": reason_code,
        }

    def _normalize_runtime_health(result: object) -> dict:
        """Normalize a runtime health result into a bounded, comparable component."""
        if not isinstance(result, dict):
            return _bounded_runtime_health("runtime_health_invalid_response")
        configured = result.get("configured")
        nonterminal = result.get("nonterminalRunCount", 0)
        if not isinstance(configured, bool) or isinstance(nonterminal, bool):
            return _bounded_runtime_health("runtime_health_invalid_response")
        if not isinstance(nonterminal, int) or nonterminal < 0:
            return _bounded_runtime_health("runtime_health_invalid_response")
        status = result.get("status")
        if status is not None and not isinstance(status, str):
            return _bounded_runtime_health("runtime_health_invalid_response")
        reason_code = result.get("reasonCode")
        if reason_code is not None and not isinstance(reason_code, str):
            return _bounded_runtime_health("runtime_health_invalid_response")
        exhausted = result.get("outboxExhaustedCount", 0)
        if isinstance(exhausted, bool) or not isinstance(exhausted, int) or exhausted < 0:
            return _bounded_runtime_health("runtime_health_invalid_response")
        component = dict(result)
        if not configured:
            component["status"] = status or "unavailable"
            component.setdefault("reasonCode", "runtime_persistence_unavailable")
        elif exhausted > 0:
            component["status"] = status or "degraded"
            component.setdefault("reasonCode", "runtime_outbox_exhausted")
        elif status is None:
            component["status"] = "healthy" if reason_code is None else "degraded"
        return component

    def _safe_runtime_health(health_fn: object) -> dict:
        try:
            result = health_fn()
        except Exception:
            # Unexpected runtime repository failures stay bounded: no exception
            # text, SQL, database path, or traceback ever reaches the response.
            return _bounded_runtime_health("runtime_health_query_failed")
        return _normalize_runtime_health(result)

    def _runtime_health_component(database_reachable: bool, schema_current: bool) -> dict:
        """Runtime tables are only queried on a reachable, current schema."""
        if not database_reachable:
            return _bounded_runtime_health("database_unreachable")
        if not schema_current:
            return _bounded_runtime_health("schema_stale")
        return _safe_runtime_health(app.state.agent_runtime_repository.health_status)

    def _autonomous_status_component(
        database_reachable: bool,
        schema_current: bool,
        provider_ready: bool,
    ) -> AutonomousWorkerStatus:
        if not database_reachable or not schema_current:
            return AutonomousWorkerStatus(
                enabled=settings.autonomous_worker_enabled,
                modelExecutionMode=settings.model_execution_mode,
                providerReady=provider_ready,
                status="degraded" if settings.autonomous_worker_enabled else "disabled",
                reasonCode="schema_unavailable" if settings.autonomous_worker_enabled else None,
            )
        try:
            return app.state.model_execution_repository.status(
                enabled=settings.autonomous_worker_enabled,
                execution_mode=settings.model_execution_mode,
                provider_ready=provider_ready,
            )
        except Exception:
            # Persisted-state or query failures remain visible but never expose SQL,
            # malformed payloads, paths, or exception text through health.
            return AutonomousWorkerStatus(
                enabled=settings.autonomous_worker_enabled,
                modelExecutionMode=settings.model_execution_mode,
                providerReady=provider_ready,
                status="degraded",
                reasonCode="autonomous_worker_status_unavailable",
            )

    def _status_snapshot() -> dict:
        database_reachable, schema_current = repository.health_probe(DATABASE_REVISION)
        outbox_pending_count = repository.outbox_pending_count() if database_reachable else 0
        outbox_exhausted_count = repository.outbox_exhausted_count() if database_reachable else 0
        context_status = repository.context_assembler_status()
        if not database_reachable or not schema_current:
            context_status = context_status.model_copy(update={"state": "unavailable"})
        lease_counts = (
            task_leases.health_counts()
            if database_reachable and schema_current
            else {
                "activeWorkerCount": 0,
                "activeLeaseCount": 0,
                "expiredLeaseCount": 0,
                "staleWorkerCount": 0,
            }
        )
        runtime_persistence = _runtime_health_component(database_reachable, schema_current)
        provider_ready = bool(
            [provider for provider in app.state.model_router.registry.list() if provider.is_local]
        )
        autonomous_status = _autonomous_status_component(
            database_reachable,
            schema_current,
            provider_ready,
        ).model_copy(update={"workerActorId": settings.autonomous_worker_actor_id.strip() or None})
        runtime_degraded = runtime_persistence.get("status", "healthy") != "healthy"
        degraded = (
            repository._system.recovery_status == "required"
            or not database_reachable
            or not schema_current
            or outbox_exhausted_count > 0
            or lease_counts["expiredLeaseCount"] > 0
            or lease_counts["staleWorkerCount"] > 0
            or runtime_degraded
            or (autonomous_status.status == "degraded")
        )
        return {
            "database_reachable": database_reachable,
            "schema_current": schema_current,
            "outbox_pending_count": outbox_pending_count,
            "outbox_exhausted_count": outbox_exhausted_count,
            "context_status": context_status,
            "lease_counts": lease_counts,
            "runtime_persistence": runtime_persistence,
            "runtime_degraded": runtime_degraded,
            "autonomous_status": autonomous_status,
            "degraded": degraded,
        }

    @app.get("/api/health", response_model=ApiResponse)
    async def health() -> ApiResponse:
        snapshot = _status_snapshot()
        database_reachable = snapshot["database_reachable"]
        schema_current = snapshot["schema_current"]
        context_status = snapshot["context_status"]
        return ApiResponse(
            data={
                "status": "degraded" if snapshot["degraded"] else "healthy",
                "service": "jarvis-simulator-api",
                "processAlive": True,
                "databaseReachable": database_reachable,
                "schemaCurrent": schema_current,
                "outboxDispatcherRunning": broker.dispatcher_running,
                "outboxExhaustedCount": snapshot["outbox_exhausted_count"],
                "recoveryRequired": repository._system.recovery_status == "required",
                "contextAssemblerReady": database_reachable and schema_current,
                "contextAssemblyCount": context_status.totalAssemblies if database_reachable else 0,
                **snapshot["lease_counts"],
                "runtimePersistence": snapshot["runtime_persistence"],
                "autonomousWorker": snapshot["autonomous_status"],
                "simulated": True,
            }
        )

    def system_status() -> SystemStatus:
        snapshot = _status_snapshot()
        database_reachable = snapshot["database_reachable"]
        schema_current = snapshot["schema_current"]
        context_status = snapshot["context_status"]
        return SystemStatus(
            status="degraded" if snapshot["degraded"] else "healthy",
            environment=settings.app_env,
            seedDataVersion=repository._system.seed_data_version,
            emergencyStop=repository.emergency_stop,
            simulator=simulator.control,
            resources=[
                ResourceStatus(name=name, value=value)
                for name, value in [
                    ("CPU", "fixture 18%"),
                    ("Memory", "fixture 42%"),
                    ("Model queue", "0 simulated"),
                    ("Heavy-reasoning slots", "0 / 1 simulated"),
                    ("Browser workers", "unavailable"),
                    ("File workers", "1 simulated"),
                ]
            ],
            lastSynchronizedAt=datetime.now(UTC),
            storageBackend="sqlite",
            databaseHealthy=database_reachable,
            databaseRevision=DATABASE_REVISION,
            schemaCurrent=schema_current,
            eventSessionId=repository.event_session_id,
            outboxPendingCount=snapshot["outbox_pending_count"],
            outboxExhaustedCount=snapshot["outbox_exhausted_count"],
            recoveryRequired=repository._system.recovery_status == "required",
            activeWorkflowRunId=repository._system.last_workflow_run_id,
            lastCheckpointId=repository._system.last_checkpoint_id,
            lastStartupAt=repository._system.last_successful_startup,
            lastCleanShutdown=repository._system.last_clean_shutdown,
            contextAssembler=context_status,
            autonomousWorker=snapshot["autonomous_status"],
            **snapshot["lease_counts"],
        )

    @app.get("/api/system/status", response_model=TypedApiResponse[SystemStatus])
    async def get_system_status() -> ApiResponse:
        return ApiResponse(data=system_status())

    @app.post("/api/system/emergency-stop", response_model=ApiResponse)
    async def emergency_stop() -> ApiResponse:
        await simulator.emergency_stop()
        app.state.office_service.reconcile(stop_all=True)
        repository.refresh_event_cursor()
        await broker.dispatch_pending()
        return ApiResponse(data=system_status())

    @app.post("/api/system/resume", response_model=ApiResponse)
    async def resume_system() -> ApiResponse:
        await simulator.system_resume()
        return ApiResponse(data=system_status())

    @app.get("/api/departments", response_model=ApiResponse)
    async def departments() -> ApiResponse:
        return ApiResponse(data=list(repository.departments.values()))

    @app.get("/api/departments/{department_id}", response_model=ApiResponse)
    async def department(department_id: str) -> ApiResponse:
        return ApiResponse(
            data=repository.require(repository.departments, department_id, "department")
        )

    @app.get("/api/agents", response_model=ApiResponse)
    async def agents() -> ApiResponse:
        return ApiResponse(data=list(repository.agents.values()))

    @app.get("/api/agents/{agent_id}", response_model=ApiResponse)
    async def agent(agent_id: str) -> ApiResponse:
        return ApiResponse(data=repository.require(repository.agents, agent_id, "agent"))

    @app.post("/api/agents/temporary", response_model=ApiResponse, status_code=201)
    async def create_temporary(
        request: Request,
        body: CreateTemporaryAgentRequest,
        idempotency_key: IdempotencyKeyHeader = None,
    ) -> ApiResponse:
        payload = body.model_dump(mode="json")
        if replay := replay_idempotent(request, idempotency_key, "temporary-agent.create", payload):
            return ApiResponse(data=Agent.model_validate(replay))
        repository.require(repository.departments, body.departmentId, "department")
        item_id = f"temp-{uuid4().hex[:8]}"
        now = datetime.now(UTC)
        item = Agent(
            id=item_id,
            name=body.name,
            role=body.role,
            description="Temporary restricted agent created for simulation only.",
            goals=["Complete one bounded simulated assignment"],
            departmentId=body.departmentId,
            managerId="jarvis",
            capabilities=["simulated_task"],
            allowedTools=["simulated_workspace"],
            deniedTools=["shell", "network", "email", "financial_accounts"],
            approvalPolicy={"allActionsRequireApproval": True},
            memoryAccess={"scope": "none"},
            performance=Performance(
                completionRate=0,
                accuracyScore=0,
                averageCompletionTime=0,
                failedTaskCount=0,
                userCorrectionCount=0,
                reviewerScore=0,
                reliabilityScore=0,
            ),
            resourceProfile={"heavyTasks": 0, "lightweightTasks": 1, "simulated": True},
            office=Office(
                zone="Agent Builder laboratory",
                deskId=f"LAB-{len(repository.agents)}",
                spriteIdentifier="sprite-temporary",
                displayPosition={"x": 48, "y": 78},
                currentAnimationState="idle",
            ),
            createdAt=now,
            updatedAt=now,
            deploymentStatus="temporary-simulated",
            isTemporary=True,
        )
        repository.agents[item.id] = item
        await broker.emit(
            "temporary_agent.created",
            {"agent": item.model_dump(mode="json")},
            agent_id=item.id,
            idempotency=idempotency_result(
                request,
                idempotency_key,
                "temporary-agent.create",
                payload,
                item,
                201,
                item.id,
            ),
        )
        return ApiResponse(data=item)

    @app.get("/api/tasks", response_model=ApiResponse)
    async def tasks() -> ApiResponse:
        return ApiResponse(data=repository.list_tasks_durable())

    @app.get("/api/tasks/{task_id}", response_model=ApiResponse)
    async def task(task_id: str) -> ApiResponse:
        return ApiResponse(data=repository.get_task_durable(task_id))

    @app.post("/api/tasks", response_model=ApiResponse, status_code=201)
    async def create_task(
        request: Request,
        body: CreateTaskRequest,
        idempotency_key: IdempotencyKeyHeader = None,
    ) -> ApiResponse:
        payload = body.model_dump(mode="json")
        if replay := replay_idempotent(request, idempotency_key, "task.create", payload):
            return ApiResponse(data=Task.model_validate(replay))
        source = (
            repository.get_task_durable(body.correctionOfTaskId)
            if body.correctionOfTaskId is not None
            else None
        )
        item = prepare_task_creation(body, source)
        audit: dict[str, object] = {"summary": f"Created task: {item.title}"}
        if source is not None:
            audit = {
                "summary": f"Created correction of {source.id}: {item.title}",
                "new": "queued",
                "payload": {
                    "correctionOfTaskId": source.id,
                    "projectId": source.projectId,
                },
            }
        await broker.emit(
            "task.created",
            {"task": item.model_dump(mode="json")},
            item.id,
            audit=audit,
            created_task=item,
            idempotency=idempotency_result(
                request, idempotency_key, "task.create", payload, item, 201, item.id
            ),
        )
        return ApiResponse(data=item)

    async def task_action(
        task_id: str,
        action: str,
        idempotency_key: str | None = None,
        idempotency_command: str | None = None,
        idempotency_payload: object | None = None,
        request: Request | None = None,
    ) -> Task:
        if action == "cancel":
            item = task_leases.cancel_task(task_id)
            await broker.dispatch_pending()
            return item
        item = repository.get_task_durable(task_id)
        repository.tasks[item.id] = item
        if action == "pause":
            if item.status in {"completed", "cancelled", "paused"}:
                raise DomainError(
                    "TASK_NOT_PAUSABLE", f"Task in {item.status} cannot be paused.", 409
                )
            item.status, item.statusMessage = "paused", "Paused by user"
        elif action == "resume":
            if item.status != "paused":
                raise DomainError("TASK_NOT_PAUSED", "Only a paused task can be resumed.", 409)
            item.status, item.statusMessage = "in_progress", "Resumed"
        elif action == "retry":
            if item.status != "failed":
                raise DomainError("TASK_NOT_FAILED", "Only a failed task can be retried.", 409)
            if item.retryCount >= item.maxRetries:
                raise DomainError("RETRY_LIMIT_REACHED", "The task retry limit was reached.", 409)
            item.retryCount += 1
            item.status, item.statusMessage, item.error = (
                "retrying",
                "Retry queued from deterministic checkpoint",
                None,
            )
        item.updatedAt = datetime.now(UTC)
        await broker.emit(
            f"task.{action}",
            {"task": item.model_dump(mode="json")},
            item.id,
            audit={"summary": item.statusMessage},
            idempotency=idempotency_result(
                request,
                idempotency_key,
                idempotency_command,
                idempotency_payload,
                item,
            )
            if request and idempotency_command and idempotency_payload is not None
            else None,
        )
        return item

    @app.post("/api/tasks/{task_id}/pause", response_model=ApiResponse)
    async def pause_task(task_id: str) -> ApiResponse:
        return ApiResponse(data=await task_action(task_id, "pause"))

    @app.post("/api/tasks/{task_id}/resume", response_model=ApiResponse)
    async def resume_task(task_id: str) -> ApiResponse:
        return ApiResponse(data=await task_action(task_id, "resume"))

    @app.post("/api/tasks/{task_id}/retry", response_model=ApiResponse)
    async def retry_task(
        request: Request,
        task_id: str,
        idempotency_key: IdempotencyKeyHeader = None,
    ) -> ApiResponse:
        payload = {"taskId": task_id}
        if replay := replay_idempotent(request, idempotency_key, "task.retry", payload):
            return ApiResponse(data=Task.model_validate(replay))
        item = await task_action(task_id, "retry", idempotency_key, "task.retry", payload, request)
        return ApiResponse(data=item)

    @app.post("/api/tasks/{task_id}/cancel", response_model=ApiResponse)
    async def cancel_task(task_id: str) -> ApiResponse:
        return ApiResponse(data=await task_action(task_id, "cancel"))

    @app.get("/api/context/assemblies", response_model=ContextAssemblyListResponse)
    async def context_assemblies(taskId: str | None = None) -> ApiResponse:
        items = list(repository.context_assemblies.values())
        if taskId is not None:
            items = [item for item in items if item.taskId == taskId]
        return ApiResponse(data=items)

    @app.get(
        "/api/context/assemblies/{assembly_id}",
        response_model=ContextAssemblyResponse,
    )
    async def context_assembly(assembly_id: str) -> ApiResponse:
        return ApiResponse(
            data=repository.require(
                repository.context_assemblies,
                assembly_id,
                "context_assembly",
            )
        )

    @app.post(
        "/api/context/assemblies",
        response_model=ContextAssemblyResponse,
        status_code=201,
    )
    async def create_context_assembly(
        request: Request,
        body: CreateContextAssemblyRequest,
        idempotency_key: IdempotencyKeyHeader = None,
        x_jarvis_actor_id: Annotated[str | None, Header(alias="X-Jarvis-Actor-Id")] = None,
    ) -> ApiResponse | JSONResponse:
        from fastapi import HTTPException

        for source in body.sources:
            if source.trustLevel in {
                TrustLevel.SYSTEM_POLICY,
                TrustLevel.TRUSTED_CONFIGURATION,
                TrustLevel.TASK_REQUEST,
                TrustLevel.TRUSTED_VALIDATOR,
                TrustLevel.TRUSTED_TOOL_RESULT,
                TrustLevel.APPROVED_ARTIFACT,
                TrustLevel.PRIOR_MODEL_OUTPUT,
            }:
                raise HTTPException(
                    status_code=403, detail="Client cannot forge trusted system context sources"
                )

            if source.sourceType in {
                ContextSourceType.SYSTEM_POLICY,
                ContextSourceType.TASK_REQUEST,
                ContextSourceType.TOOL_RESULT,
                ContextSourceType.VALIDATOR_RESULT,
                ContextSourceType.PRIOR_MODEL_OUTPUT,
            }:
                raise HTTPException(
                    status_code=403,
                    detail=f"Client cannot forge backend-owned source types ({source.sourceType})",
                )

        payload = body.model_dump(mode="json")
        replay = replay_idempotent(
            request,
            idempotency_key,
            "context-assembly.create",
            payload,
        )
        if replay is not None:
            return ApiResponse(data=ContextAssembly.model_validate(replay))

        actor_id = None
        if x_jarvis_actor_id is not None:
            actor_context = app.state.agent_runtime_service.authenticate_actor(x_jarvis_actor_id)
            actor_id = actor_context.actor_id

        task = repository.get_task_durable(body.taskId)

        enricher = ContextEnricher(
            identity_service=app.state.identity_service,
            settings=settings,
            repository=repository,
            tool_registry=getattr(app.state, "tool_execution_service", None),
        )
        system_sources = enricher.enrich(body.taskId, actor_id=actor_id)
        body.sources.extend(system_sources)

        item = context_assembler.assemble(task, body)
        existing = repository.context_assemblies.get(item.id)
        if existing is not None:
            completion = idempotency_result(
                request,
                idempotency_key,
                "context-assembly.create",
                payload,
                existing,
                200,
                existing.id,
            )
            if completion is not None:
                repository.complete_idempotency(completion)
            response = ApiResponse(data=existing)
            return JSONResponse(
                status_code=200,
                content=response.model_dump(mode="json"),
            )

        event_payload = ContextAssemblyEventPayload(
            assemblyId=item.id,
            status=item.status,
            requestHash=item.requestHash,
            includedSourceCount=item.report.includedSourceCount,
            excludedSourceCount=item.report.excludedSourceCount,
            redactionCount=item.report.redactionCount,
            injectionFindingCount=item.report.injectionFindingCount,
            conflictCount=item.report.conflictCount,
        ).model_dump(mode="json")
        await broker.emit(
            "context.assembly.created",
            event_payload,
            created_context=(item, task),
            task_id=item.taskId,
            correlation_id=item.id,
            source="context-assembler",
            audit={
                "summary": f"Context assembly {item.status}: {item.id}",
                "new": item.status,
                "payload": event_payload,
            },
            idempotency=idempotency_result(
                request,
                idempotency_key,
                "context-assembly.create",
                payload,
                item,
                201,
                item.id,
            ),
        )
        return ApiResponse(data=item)

    @app.get("/api/workers", response_model=ApiResponse)
    async def workers() -> ApiResponse:
        return ApiResponse(data=task_leases.list_workers())

    @app.post("/api/workers", response_model=ApiResponse, status_code=201)
    async def register_worker(body: RegisterWorkerRequest) -> ApiResponse:
        worker = task_leases.register_worker(
            body.name, body.instanceId, body.leaseSeconds, body.metadata
        )
        await broker.dispatch_pending()
        return ApiResponse(data=worker)

    @app.post("/api/workers/{worker_id}/heartbeat", response_model=ApiResponse)
    async def heartbeat_worker(worker_id: str) -> ApiResponse:
        return ApiResponse(data=task_leases.heartbeat_worker(worker_id))

    @app.post("/api/workers/{worker_id}/drain", response_model=ApiResponse)
    async def drain_worker(worker_id: str) -> ApiResponse:
        worker = task_leases.drain_worker(worker_id)
        await broker.dispatch_pending()
        return ApiResponse(data=worker)

    @app.post("/api/workers/{worker_id}/stop", response_model=ApiResponse)
    async def stop_worker(worker_id: str) -> ApiResponse:
        worker = task_leases.stop_worker(worker_id)
        await broker.dispatch_pending()
        return ApiResponse(data=worker)

    @app.post("/api/workers/{worker_id}/tasks/acquire", response_model=ApiResponse)
    async def acquire_task(worker_id: str, body: AcquireTaskLeaseRequest) -> ApiResponse:
        acquired = task_leases.acquire_task(worker_id, body.leaseSeconds, body.taskId)
        if acquired is None:
            return ApiResponse(data=None)
        task_item, lease = acquired
        await broker.dispatch_pending()
        return ApiResponse(data={"task": task_item, "lease": lease})

    @app.post("/api/tasks/{task_id}/lease/renew", response_model=ApiResponse)
    async def renew_task_lease(task_id: str, body: RenewTaskLeaseRequest) -> ApiResponse:
        lease = task_leases.renew_lease(
            task_id,
            body.workerId,
            body.leaseToken,
            body.leaseSeconds,
            body.checkpointId,
        )
        await broker.dispatch_pending()
        return ApiResponse(data=lease)

    @app.post("/api/tasks/{task_id}/lease/release", response_model=ApiResponse)
    async def release_task_lease(task_id: str, body: LeaseCommandRequest) -> ApiResponse:
        item = task_leases.release_lease(task_id, body.workerId, body.leaseToken)
        await broker.dispatch_pending()
        return ApiResponse(data=item)

    @app.post("/api/tasks/{task_id}/lease/complete", response_model=ApiResponse)
    async def complete_task_lease(task_id: str, body: CompleteTaskLeaseRequest) -> ApiResponse:
        item = task_leases.complete_task(task_id, body.workerId, body.leaseToken, body.result)
        await broker.dispatch_pending()
        return ApiResponse(data=item)

    @app.post("/api/tasks/{task_id}/lease/fail", response_model=ApiResponse)
    async def fail_task_lease(task_id: str, body: FailTaskLeaseRequest) -> ApiResponse:
        item = task_leases.fail_task(
            task_id,
            body.workerId,
            body.leaseToken,
            body.error,
            body.retryable,
        )
        await broker.dispatch_pending()
        return ApiResponse(data=item)

    @app.get("/api/approvals", response_model=ApiResponse)
    async def approvals() -> ApiResponse:
        return ApiResponse(data=list(repository.approvals.values()))

    @app.get("/api/approvals/{approval_id}", response_model=ApiResponse)
    async def approval(approval_id: str) -> ApiResponse:
        return ApiResponse(data=repository.require(repository.approvals, approval_id, "approval"))

    async def decide(
        approval_id: str,
        decision: str,
        body: DecisionRequest,
        idempotency_key: str | None = None,
        idempotency_command: str | None = None,
        idempotency_payload: object | None = None,
        request: Request | None = None,
    ) -> Approval:
        lock = approval_decision_locks.setdefault(approval_id, asyncio.Lock())
        async with lock:
            item = repository.require(repository.approvals, approval_id, "approval")
            assert isinstance(item, Approval)
            if repository.emergency_stop:
                raise DomainError(
                    "EMERGENCY_STOP_ACTIVE",
                    "Approval actions are blocked while emergency stop is active.",
                    423,
                )
            if item.status != "pending":
                raise DomainError(
                    "APPROVAL_ALREADY_PROCESSED", f"Approval is already {item.status}.", 409
                )
            if item.expiresAt <= datetime.now(UTC):
                expired_at = datetime.now(UTC)
                item.status = "expired"
                item.reviewedBy = "system"
                item.reviewedAt = expired_at
                item.decisionNote = "Expired before review."
                await broker.emit(
                    "approval.expired",
                    {"approval": item.model_dump(mode="json")},
                    item.taskId,
                    item.requestedByAgentId,
                    audit={
                        "summary": "Approval expired before a decision",
                        "previous": "pending",
                        "new": "expired",
                        "payload": {
                            "approvalId": item.id,
                            "status": "expired",
                            "requestedByAgentId": item.requestedByAgentId,
                            "expiresAt": item.expiresAt.isoformat(),
                        },
                    },
                )
                raise DomainError("APPROVAL_EXPIRED", "Expired approvals cannot be processed.", 409)
            if decision == "approved" and item.riskLevel == "black":
                raise DomainError(
                    "BLACK_RISK_PROHIBITED", "Black-risk actions cannot be approved.", 403
                )
            item.status = decision
            item.reviewedBy, item.reviewedAt, item.decisionNote = (
                body.reviewedBy,
                datetime.now(UTC),
                body.decisionNote,
            )
            await broker.emit(
                f"approval.{decision}",
                {"approval": item.model_dump(mode="json")},
                item.taskId,
                item.requestedByAgentId,
                audit={
                    "summary": f"Approval {decision}",
                    "payload": {"approvalId": item.id, "status": decision},
                },
                idempotency=idempotency_result(
                    request,
                    idempotency_key,
                    idempotency_command,
                    idempotency_payload,
                    item,
                )
                if request and idempotency_command and idempotency_payload is not None
                else None,
            )
            return item

    @app.post("/api/approvals/{approval_id}/approve", response_model=ApiResponse)
    async def approve(
        request: Request,
        approval_id: str,
        body: DecisionRequest,
        idempotency_key: IdempotencyKeyHeader = None,
    ) -> ApiResponse:
        payload = {"approvalId": approval_id, **body.model_dump(mode="json")}
        if replay := replay_idempotent(request, idempotency_key, "approval.approve", payload):
            return ApiResponse(data=Approval.model_validate(replay))
        item = await decide(
            approval_id,
            "approved",
            body,
            idempotency_key,
            "approval.approve",
            payload,
            request,
        )
        return ApiResponse(data=item)

    @app.post("/api/approvals/{approval_id}/reject", response_model=ApiResponse)
    async def reject(
        request: Request,
        approval_id: str,
        body: DecisionRequest,
        idempotency_key: IdempotencyKeyHeader = None,
    ) -> ApiResponse:
        payload = {"approvalId": approval_id, **body.model_dump(mode="json")}
        if replay := replay_idempotent(request, idempotency_key, "approval.reject", payload):
            return ApiResponse(data=Approval.model_validate(replay))
        item = await decide(
            approval_id,
            "rejected",
            body,
            idempotency_key,
            "approval.reject",
            payload,
            request,
        )
        return ApiResponse(data=item)

    @app.post("/api/approvals/{approval_id}/edit", response_model=ApiResponse)
    async def edit_approval(approval_id: str, body: EditApprovalRequest) -> ApiResponse:
        item = repository.require(repository.approvals, approval_id, "approval")
        assert isinstance(item, Approval)
        if item.status != "pending":
            raise DomainError(
                "APPROVAL_ALREADY_PROCESSED", "Only pending approvals are editable.", 409
            )
        if body.title is not None:
            item.title = body.title
        if body.description is not None:
            item.description = body.description
        repository.persist()
        return ApiResponse(data=item)

    @app.get("/api/audit-events", response_model=ApiResponse)
    async def audit() -> ApiResponse:
        repository.reload()
        return ApiResponse(data=repository.audit)

    @app.get("/api/artifacts", response_model=ApiResponse)
    async def artifacts() -> ApiResponse:
        return ApiResponse(data=list(repository.artifacts.values()))

    @app.get("/api/notifications", response_model=ApiResponse)
    async def notifications() -> ApiResponse:
        return ApiResponse(data=list(repository.notifications.values()))

    @app.post("/api/notifications/{notification_id}/read", response_model=ApiResponse)
    async def read_notification(notification_id: str) -> ApiResponse:
        item = repository.require(repository.notifications, notification_id, "notification")
        assert isinstance(item, Notification)
        item.isRead = True
        repository.persist()
        return ApiResponse(data=item)

    @app.post("/api/simulator/start", response_model=ApiResponse)
    async def start_simulator(
        request: Request,
        idempotency_key: IdempotencyKeyHeader = None,
    ) -> ApiResponse:
        payload = {"action": "start"}
        if replay := replay_idempotent(request, idempotency_key, "simulator.start", payload):
            return ApiResponse(data=replay)
        expected = simulator.control.model_copy(deep=True, update={"state": "running"})
        result = await simulator.start(
            idempotency_result(request, idempotency_key, "simulator.start", payload, expected)
        )
        return ApiResponse(data=result)

    @app.post("/api/simulator/pause", response_model=ApiResponse)
    async def pause_simulator() -> ApiResponse:
        return ApiResponse(data=await simulator.pause())

    @app.post("/api/simulator/resume", response_model=ApiResponse)
    async def resume_simulator() -> ApiResponse:
        return ApiResponse(data=await simulator.resume())

    @app.post("/api/simulator/reset", response_model=ApiResponse)
    async def reset_simulator(
        request: Request,
        idempotency_key: IdempotencyKeyHeader = None,
    ) -> ApiResponse:
        payload = {"action": "reset"}
        if replay := replay_idempotent(request, idempotency_key, "simulator.reset", payload):
            return ApiResponse(data=replay)
        expected = SimulatorControl(accelerated=simulator.delay_ms <= 10)
        result = await simulator.reset(
            idempotency_result(request, idempotency_key, "simulator.reset", payload, expected)
        )
        return ApiResponse(data=result)

    @app.post("/api/simulator/approval", response_model=ApiResponse)
    async def trigger_approval() -> ApiResponse:
        return ApiResponse(data=repository.approvals["approval-pending"])

    @app.post("/api/simulator/failure", response_model=ApiResponse)
    async def trigger_failure(body: FailureRequest) -> ApiResponse:
        if body.scenario == "invalid_transition":
            try:
                validate_transition("idle", "reviewing")
            except InvalidTransitionError as exc:
                raise DomainError("INVALID_STATE_TRANSITION", str(exc), 409) from exc
        return ApiResponse(data=await simulator.fail(body.scenario))

    @app.websocket("/ws/events")
    async def websocket_events(websocket: WebSocket) -> None:
        if not _is_loopback_peer(websocket.client.host if websocket.client else None):
            await websocket.close(code=1008, reason="This phase accepts loopback clients only.")
            return
        await broker.connect(websocket)
        try:
            await broker.send_snapshot(
                websocket,
                lambda: {
                    "snapshot": _json_snapshot(repository.snapshot()),
                    "system": system_status().model_dump(mode="json"),
                },
            )
            while True:
                message = await websocket.receive_text()
                if len(message) > 32:
                    await websocket.close(code=1009, reason="WebSocket command is too large.")
                    return
                if message == "resync":
                    await broker.send_snapshot(
                        websocket,
                        lambda: {
                            "snapshot": _json_snapshot(repository.snapshot()),
                            "system": system_status().model_dump(mode="json"),
                        },
                    )
                else:
                    await websocket.close(code=1008, reason="Unsupported WebSocket command.")
                    return
        except WebSocketDisconnect:
            pass
        finally:
            broker.disconnect(websocket)

    return app


def _json_snapshot(snapshot: dict[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in snapshot.items():
        if isinstance(value, list):
            result[key] = [
                item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                for item in value
            ]
        else:
            result[key] = value
    return result


if "pytest" in sys.modules:
    _pytest_database = Path(tempfile.gettempdir()) / f"jarvis-import-{uuid4().hex}.db"
    app = create_app(database_url=f"sqlite:///{_pytest_database.as_posix()}")
else:
    app = create_app()
