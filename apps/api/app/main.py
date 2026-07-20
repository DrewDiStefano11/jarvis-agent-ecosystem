from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.errors import DomainError
from app.core.transitions import InvalidTransitionError, validate_transition
from app.models.domain import (
    Agent,
    ApiResponse,
    Approval,
    CreateTaskRequest,
    CreateTemporaryAgentRequest,
    DecisionRequest,
    EditApprovalRequest,
    FailureRequest,
    Notification,
    Office,
    Performance,
    ResourceStatus,
    SystemStatus,
    Task,
)
from app.services.events import EventBroker
from app.services.repository import InMemoryRepository
from app.simulator.engine import SimulatorEngine


def create_app(delay_ms: int | None = None) -> FastAPI:
    app = FastAPI(
        title="Jarvis Agent Ecosystem Simulator",
        version="0.1.0",
        description="Phase 1 deterministic simulator. No real agents or external actions.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[os.getenv("WEB_ORIGIN", "http://localhost:5173")],
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    repository = InMemoryRepository()
    broker = EventBroker()
    simulator = SimulatorEngine(
        repository,
        broker,
        delay_ms if delay_ms is not None else int(os.getenv("SIMULATOR_DELAY_MS", "800")),
    )
    app.state.repository = repository
    app.state.broker = broker
    app.state.simulator = simulator

    @app.exception_handler(DomainError)
    async def domain_error(_: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message, "details": {}}},
        )

    @app.get("/api/health", response_model=ApiResponse)
    async def health() -> ApiResponse:
        return ApiResponse(
            data={"status": "healthy", "service": "jarvis-simulator-api", "simulated": True}
        )

    def system_status() -> SystemStatus:
        return SystemStatus(
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
        )

    @app.get("/api/system/status", response_model=ApiResponse)
    async def get_system_status() -> ApiResponse:
        return ApiResponse(data=system_status())

    @app.post("/api/system/emergency-stop", response_model=ApiResponse)
    async def emergency_stop() -> ApiResponse:
        await simulator.emergency_stop()
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
    async def create_temporary(body: CreateTemporaryAgentRequest) -> ApiResponse:
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
            "temporary_agent.created", {"agent": item.model_dump(mode="json")}, agent_id=item.id
        )
        return ApiResponse(data=item)

    @app.get("/api/tasks", response_model=ApiResponse)
    async def tasks() -> ApiResponse:
        return ApiResponse(data=list(repository.tasks.values()))

    @app.get("/api/tasks/{task_id}", response_model=ApiResponse)
    async def task(task_id: str) -> ApiResponse:
        return ApiResponse(data=repository.require(repository.tasks, task_id, "task"))

    @app.post("/api/tasks", response_model=ApiResponse, status_code=201)
    async def create_task(body: CreateTaskRequest) -> ApiResponse:
        now = datetime.now(UTC)
        item = Task(
            id=f"task-{uuid4().hex[:10]}",
            title=body.title,
            description=body.description,
            request=body.description,
            createdBy="local-user",
            assignedManagerId="jarvis",
            priority=body.priority,
            createdAt=now,
            updatedAt=now,
        )
        repository.tasks[item.id] = item
        event = await broker.emit("task.created", {"task": item.model_dump(mode="json")}, item.id)
        repository.add_audit(
            "task.created", f"Created task: {item.title}", event.sequenceNumber, item.id
        )
        return ApiResponse(data=item)

    async def task_action(task_id: str, action: str) -> Task:
        item = repository.require(repository.tasks, task_id, "task")
        assert isinstance(item, Task)
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
        elif action == "cancel":
            if item.status in {"completed", "cancelled"}:
                raise DomainError(
                    "TASK_NOT_CANCELLABLE", f"Task in {item.status} cannot be cancelled.", 409
                )
            item.status, item.statusMessage = "cancelled", "Cancelled by user"
        item.updatedAt = datetime.now(UTC)
        event = await broker.emit(f"task.{action}", {"task": item.model_dump(mode="json")}, item.id)
        repository.add_audit(f"task.{action}", item.statusMessage, event.sequenceNumber, item.id)
        return item

    @app.post("/api/tasks/{task_id}/pause", response_model=ApiResponse)
    async def pause_task(task_id: str) -> ApiResponse:
        return ApiResponse(data=await task_action(task_id, "pause"))

    @app.post("/api/tasks/{task_id}/resume", response_model=ApiResponse)
    async def resume_task(task_id: str) -> ApiResponse:
        return ApiResponse(data=await task_action(task_id, "resume"))

    @app.post("/api/tasks/{task_id}/retry", response_model=ApiResponse)
    async def retry_task(task_id: str) -> ApiResponse:
        return ApiResponse(data=await task_action(task_id, "retry"))

    @app.post("/api/tasks/{task_id}/cancel", response_model=ApiResponse)
    async def cancel_task(task_id: str) -> ApiResponse:
        return ApiResponse(data=await task_action(task_id, "cancel"))

    @app.get("/api/approvals", response_model=ApiResponse)
    async def approvals() -> ApiResponse:
        return ApiResponse(data=list(repository.approvals.values()))

    @app.get("/api/approvals/{approval_id}", response_model=ApiResponse)
    async def approval(approval_id: str) -> ApiResponse:
        return ApiResponse(data=repository.require(repository.approvals, approval_id, "approval"))

    async def decide(approval_id: str, decision: str, body: DecisionRequest) -> Approval:
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
            item.status = "expired"
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
        event = await broker.emit(
            f"approval.{decision}",
            {"approval": item.model_dump(mode="json")},
            item.taskId,
            item.requestedByAgentId,
        )
        repository.add_audit(
            f"approval.{decision}",
            f"Approval {decision}",
            event.sequenceNumber,
            item.taskId,
            item.requestedByAgentId,
            payload={"approvalId": item.id, "status": decision},
        )
        return item

    @app.post("/api/approvals/{approval_id}/approve", response_model=ApiResponse)
    async def approve(approval_id: str, body: DecisionRequest) -> ApiResponse:
        return ApiResponse(data=await decide(approval_id, "approved", body))

    @app.post("/api/approvals/{approval_id}/reject", response_model=ApiResponse)
    async def reject(approval_id: str, body: DecisionRequest) -> ApiResponse:
        return ApiResponse(data=await decide(approval_id, "rejected", body))

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
        return ApiResponse(data=item)

    @app.get("/api/audit-events", response_model=ApiResponse)
    async def audit() -> ApiResponse:
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
        return ApiResponse(data=item)

    @app.post("/api/simulator/start", response_model=ApiResponse)
    async def start_simulator() -> ApiResponse:
        return ApiResponse(data=await simulator.start())

    @app.post("/api/simulator/pause", response_model=ApiResponse)
    async def pause_simulator() -> ApiResponse:
        return ApiResponse(data=await simulator.pause())

    @app.post("/api/simulator/resume", response_model=ApiResponse)
    async def resume_simulator() -> ApiResponse:
        return ApiResponse(data=await simulator.resume())

    @app.post("/api/simulator/reset", response_model=ApiResponse)
    async def reset_simulator() -> ApiResponse:
        return ApiResponse(data=await simulator.reset())

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
        task_item = repository.tasks["task-demo"]
        task_item.status = "failed"
        task_item.statusMessage = body.scenario.replace("_", " ").title()
        task_item.error = {
            "code": body.scenario.upper(),
            "message": "Controlled simulated failure.",
        }
        simulator.control.state = "failed"
        await broker.emit("error.simulated", {"scenario": body.scenario}, task_item.id)
        return ApiResponse(data=task_item)

    @app.websocket("/ws/events")
    async def websocket_events(websocket: WebSocket) -> None:
        await broker.connect(websocket)
        try:
            await broker.emit(
                "system.snapshot",
                {
                    "snapshot": _json_snapshot(repository.snapshot()),
                    "system": system_status().model_dump(mode="json"),
                },
            )
            while True:
                message = await websocket.receive_text()
                if message == "resync":
                    await broker.emit(
                        "system.snapshot",
                        {
                            "snapshot": _json_snapshot(repository.snapshot()),
                            "system": system_status().model_dump(mode="json"),
                        },
                    )
        except WebSocketDisconnect:
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


app = create_app()
