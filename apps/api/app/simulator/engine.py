from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from app.core.errors import DomainError
from app.core.transitions import ACTIVE_STATES, InvalidTransitionError, validate_transition
from app.models.domain import AgentStatus, Artifact, Notification, SimulatorControl
from app.repositories.sqlalchemy import SqlAlchemyRepository
from app.services.events import EventBroker


class SimulatorEngine:
    def __init__(
        self, repository: SqlAlchemyRepository, broker: EventBroker, delay_ms: int = 60
    ) -> None:
        self.repository = repository
        self.broker = broker
        self.delay_ms = delay_ms
        self.control = SimulatorControl(accelerated=delay_ms <= 10)
        self._runner: asyncio.Task[None] | None = None
        self._resume = asyncio.Event()
        self._resume.set()
        self._stopped = False
        self.steps = self._build_steps()
        for index, step in enumerate(self.steps):
            slug = "_".join(str(step["message"]).lower().split()[:4])
            step["id"] = f"{step['agent']}.{index + 1:02d}.{slug}"
        self.run_id: str | None = repository._system.last_workflow_run_id

    def _build_steps(self) -> list[dict[str, Any]]:
        return [
            {
                "agent": "jarvis",
                "agentStatus": "assigned",
                "taskStatus": "assigned",
                "progress": 5,
                "message": "Jarvis accepted the request",
            },
            {
                "agent": "jarvis",
                "agentStatus": "planning",
                "taskStatus": "planning",
                "progress": 10,
                "message": "Jarvis is planning the project",
            },
            {
                "agent": "atlas",
                "agentStatus": "assigned",
                "progress": 15,
                "message": "Project delegated to Atlas",
                "children": True,
            },
            {
                "agent": "atlas",
                "agentStatus": "planning",
                "progress": 20,
                "message": "Atlas is planning research",
            },
            {
                "agent": "scout",
                "agentStatus": "assigned",
                "progress": 25,
                "message": "Research assigned to Scout",
            },
            {
                "agent": "scout",
                "agentStatus": "planning",
                "progress": 28,
                "message": "Scout is preparing research",
            },
            {
                "agent": "scout",
                "agentStatus": "researching",
                "taskStatus": "in_progress",
                "progress": 32,
                "message": "Scout is researching simulated destinations",
            },
            {
                "agent": "scout",
                "agentStatus": "executing_tool",
                "progress": 36,
                "message": "Simulated research tool queried",
            },
            {
                "agent": "scout",
                "agentStatus": "researching",
                "progress": 40,
                "message": "Simulated findings structured",
            },
            {
                "agent": "scout",
                "agentStatus": "waiting_for_agent",
                "progress": 44,
                "message": "Scout requested preference document",
            },
            {
                "agent": "archive",
                "agentStatus": "assigned",
                "progress": 48,
                "message": "Archive received document request",
            },
            {
                "agent": "archive",
                "agentStatus": "planning",
                "progress": 50,
                "message": "Archive locating simulated fixture",
            },
            {
                "agent": "archive",
                "agentStatus": "researching",
                "progress": 53,
                "message": "Archive retrieved preference fixture",
                "artifact": "preferences",
            },
            {
                "agent": "archive",
                "agentStatus": "delivering",
                "progress": 56,
                "message": "Archive returned preference artifact",
            },
            {
                "agent": "scout",
                "agentStatus": "researching",
                "progress": 60,
                "message": "Scout prepared draft recommendation",
                "artifact": "draft",
            },
            {
                "agent": "sentinel",
                "agentStatus": "assigned",
                "taskStatus": "under_review",
                "progress": 64,
                "message": "Draft submitted to Sentinel",
            },
            {
                "agent": "sentinel",
                "agentStatus": "planning",
                "progress": 66,
                "message": "Sentinel preparing review",
            },
            {
                "agent": "sentinel",
                "agentStatus": "reviewing",
                "progress": 70,
                "message": "Sentinel reviewing claims",
            },
            {
                "agent": "scout",
                "agentStatus": "researching",
                "taskStatus": "revision_requested",
                "progress": 72,
                "message": "Revision requested: clarify transfer-time claim",
            },
            {
                "agent": "scout",
                "agentStatus": "executing_tool",
                "progress": 76,
                "message": "Scout verifies simulated transfer fixture",
            },
            {
                "agent": "scout",
                "agentStatus": "researching",
                "progress": 80,
                "message": "Scout revised the report",
            },
            {
                "agent": "sentinel",
                "agentStatus": "delivering",
                "progress": 84,
                "message": "Sentinel approved the revised report",
            },
            {
                "agent": "scout",
                "agentStatus": "delivering",
                "progress": 88,
                "message": "Scout delivered findings to Atlas",
            },
            {
                "agent": "atlas",
                "agentStatus": "delivering",
                "progress": 94,
                "message": "Atlas combined and delivered findings",
            },
            {
                "agent": "jarvis",
                "agentStatus": "delivering",
                "taskStatus": "completed",
                "progress": 100,
                "message": "Jarvis delivered the final report",
                "artifact": "final",
            },
        ]

    async def start(self) -> SimulatorControl:
        if self._runner and not self._runner.done():
            raise DomainError(
                "SIMULATOR_ALREADY_RUNNING", "The deterministic demo is already active.", 409
            )
        if self.control.state == "completed":
            raise DomainError(
                "SIMULATOR_RESET_REQUIRED", "Reset the completed demo before starting again.", 409
            )
        self._stopped = False
        self._resume.set()
        self.control.state = "running"
        self.run_id = f"run-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}-{id(self):x}"
        self.repository.create_workflow_run(self.run_id, len(self.steps))
        self.repository.stage_checkpoint(
            self.run_id,
            0,
            "workflow.start",
            {"totalSteps": len(self.steps)},
        )
        self._runner = asyncio.create_task(self._run())
        await self.broker.emit(
            "system.simulator.started",
            {"state": "running"},
            "task-demo",
            audit={"summary": "Started the deterministic durable workflow"},
        )
        return self.control

    async def _run(self) -> None:
        while self.control.currentStep < len(self.steps) and not self._stopped:
            await self._resume.wait()
            if self.repository.emergency_stop:
                await asyncio.sleep(0.01)
                continue
            step = self.steps[self.control.currentStep]
            await self._apply_step(step)
            self.control.currentStep += 1
            await asyncio.sleep(self.delay_ms / 1000)
        if not self._stopped and self.control.currentStep >= len(self.steps):
            self.control.state = "completed"
            self._finish_agents()
            self.repository.notifications["notification-demo-complete"] = Notification(
                id="notification-demo-complete",
                title="Trip report completed",
                message="The deterministic simulated workflow finished.",
                level="success",
                taskId="task-demo",
                createdAt=datetime.now(UTC),
            )
            if self.run_id:
                self.repository.stage_checkpoint(
                    self.run_id,
                    self.control.currentStep,
                    "jarvis.complete",
                    {"totalSteps": len(self.steps)},
                    "completed",
                )
            await self.broker.emit(
                "system.simulator.completed", {"state": "completed"}, "task-demo"
            )

    async def _apply_step(self, step: dict[str, Any]) -> None:
        agent = self.repository.agents[step["agent"]]
        new_status: AgentStatus = step["agentStatus"]
        try:
            validate_transition(agent.status, new_status, agent.previousStatus)
        except InvalidTransitionError:
            # deterministic hand-offs may reassign an idle participant directly
            if agent.status == "idle" and new_status not in {"assigned", "paused"}:
                agent.status = "assigned"
            validate_transition(agent.status, new_status, agent.previousStatus)
        previous = agent.status
        agent.status = new_status
        agent.currentTaskId = "task-demo"
        agent.progress = step["progress"]
        agent.statusMessage = step["message"]
        agent.updatedAt = datetime.now(UTC)
        task = self.repository.tasks["task-demo"]
        task.assignedAgentIds = list(dict.fromkeys([*task.assignedAgentIds, agent.id]))
        task.progress = step["progress"]
        task.statusMessage = step["message"]
        task.updatedAt = datetime.now(UTC)
        if "taskStatus" in step:
            task.status = step["taskStatus"]
        if step.get("children") and not task.childTaskIds:
            self._create_children()
        if artifact_kind := step.get("artifact"):
            self._create_artifact(artifact_kind)
        if self.run_id:
            self.repository.stage_checkpoint(
                self.run_id,
                self.control.currentStep + 1,
                str(step["id"]),
                {"totalSteps": len(self.steps)},
            )
        await self.broker.emit(
            "agent.status.changed",
            {"agent": agent.model_dump(mode="json"), "message": step["message"]},
            "task-demo",
            agent.id,
            audit={
                "summary": step["message"],
                "previous": previous,
                "new": new_status,
                "payload": {"step": self.control.currentStep + 1},
            },
        )

    def _create_children(self) -> None:
        parent = self.repository.tasks["task-demo"]
        for task_id, title, agent_id in [
            ("task-demo-research", "Research destinations", "scout"),
            ("task-demo-artifacts", "Organize preference artifacts", "archive"),
        ]:
            child = parent.model_copy(
                deep=True,
                update={
                    "id": task_id,
                    "title": title,
                    "parentTaskId": parent.id,
                    "childTaskIds": [],
                    "assignedManagerId": "atlas",
                    "assignedAgentIds": [agent_id],
                    "approvalIds": [],
                    "artifactIds": [],
                    "status": "in_progress",
                    "progress": 10,
                    "result": None,
                    "completedAt": None,
                },
            )
            self.repository.tasks[task_id] = child
            parent.childTaskIds.append(task_id)

    def _create_artifact(self, kind: str) -> None:
        artifact_id = f"artifact-demo-{kind}"
        if artifact_id in self.repository.artifacts:
            return
        names = {
            "preferences": "Travel preferences.fixture.md",
            "draft": "Caribbean draft.md",
            "final": "Seven-day Caribbean recommendation.md",
        }
        artifact = Artifact(
            id=artifact_id,
            taskId="task-demo",
            name=names[kind],
            type=kind,
            summary=f"Clearly simulated {kind} artifact for the deterministic demonstration.",
            simulatedPath=f"simulated://workspace/travel/{kind}.md",
            createdAt=datetime.now(UTC),
        )
        self.repository.artifacts[artifact_id] = artifact
        self.repository.tasks["task-demo"].artifactIds.append(artifact_id)
        if kind == "final":
            task = self.repository.tasks["task-demo"]
            task.result = "Seven-day Caribbean recommendation prepared from simulated fixtures, revised after Sentinel review, and stored as a simulated artifact."
            task.completedAt = datetime.now(UTC)

    def _finish_agents(self) -> None:
        for agent in self.repository.agents.values():
            if agent.currentTaskId == "task-demo":
                agent.status = "idle"
                agent.previousStatus = None
                agent.currentTaskId = None
                agent.progress = 0
                agent.statusMessage = "Available"

    async def pause(self) -> SimulatorControl:
        if self.control.state != "running":
            raise DomainError("SIMULATOR_NOT_RUNNING", "The simulator is not running.", 409)
        self.control.state = "paused"
        self._resume.clear()
        if self.run_id:
            step_id = str(self.steps[max(0, self.control.currentStep - 1)]["id"])
            self.repository.stage_checkpoint(
                self.run_id, self.control.currentStep, step_id, status="paused"
            )
        await self.broker.emit("system.simulator.paused", {"step": self.control.currentStep})
        return self.control

    async def resume(self) -> SimulatorControl:
        if self.control.state not in {"paused", "recovery_required"}:
            raise DomainError("SIMULATOR_NOT_PAUSED", "The simulator is not paused.", 409)
        if self.repository.emergency_stop:
            raise DomainError(
                "EMERGENCY_STOP_ACTIVE", "Resume the system before resuming the simulator.", 409
            )
        self.control.state = "running"
        self._resume.set()
        if self.run_id:
            active = self.repository.active_workflow()
            if active and active.checkpoint_id:
                checkpoint = self.repository.load_checkpoint(active.checkpoint_id)
                self.control.currentStep = int(checkpoint["stepIndex"])
            self.repository.set_workflow_status(self.run_id, "running")
        if not self._runner or self._runner.done():
            self._stopped = False
            self._runner = asyncio.create_task(self._run())
        await self.broker.emit("system.simulator.resumed", {"step": self.control.currentStep})
        return self.control

    async def reset(self) -> SimulatorControl:
        self._stopped = True
        self._resume.set()
        if self._runner and not self._runner.done():
            self._runner.cancel()
            try:
                await self._runner
            except asyncio.CancelledError:
                pass
        if self.run_id:
            self.repository.set_workflow_status(self.run_id, "cancelled", "Reset by local operator")
        self.repository.add_audit(
            "system.simulator.reset",
            "Reset the deterministic demo while preserving durable audit history",
            self.repository.next_sequence(),
            "task-demo",
        )
        self.repository.reset()
        self.broker.reset_sequence()
        self.control = SimulatorControl(accelerated=self.delay_ms <= 10)
        self._runner = None
        self.run_id = None
        return self.control

    async def emergency_stop(self) -> None:
        self.repository.emergency_stop = True
        if self.control.state == "running":
            self.control.state = "paused"
            self._resume.clear()
        for agent in self.repository.agents.values():
            if agent.status in ACTIVE_STATES:
                agent.previousStatus = agent.status
                agent.status = "paused"
                agent.statusMessage = "Paused by emergency stop"
        if self.run_id:
            step_id = str(self.steps[max(0, self.control.currentStep - 1)]["id"])
            self.repository.stage_checkpoint(
                self.run_id, self.control.currentStep, step_id, {"emergencyStop": True}, "paused"
            )
        await self.broker.emit("system.emergency_stop", {"active": True})

    async def system_resume(self) -> None:
        if not self.repository.emergency_stop:
            raise DomainError("EMERGENCY_STOP_NOT_ACTIVE", "Emergency stop is not active.", 409)
        self.repository.emergency_stop = False
        for agent in self.repository.agents.values():
            if agent.status == "paused" and agent.previousStatus:
                agent.status = agent.previousStatus
                agent.previousStatus = None
        await self.broker.emit("system.resumed", {"active": False})
