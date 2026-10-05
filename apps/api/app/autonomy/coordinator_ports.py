"""Production acceptance ports. All execution belongs to the existing worker.

Ports consume an explicitly prepared/queued task and never provision identities,
grant permissions, activate catalog agents, or create another execution ledger.
The fixture harness remains available independently for CI positive controls.
"""

from __future__ import annotations

import asyncio
import json

from app.autonomy.graph import WorkGraph, WorkNode
from app.autonomy.ports import (
    STAGE_PROVENANCE,
    SpecialistOutcome,
    StageKind,
    SynthesisResult,
    TeamDecision,
)
from app.core.errors import DomainError
from app.decomposition.service import DecompositionService
from app.team_selection.service import TeamSelectionService


class ProductionTeamSelector:
    provenance = STAGE_PROVENANCE[StageKind.SELECT_TEAM]

    def __init__(self, app, task_id):
        self.app, self.task_id = app, task_id

    def select_team(self, *, required_capabilities=(), workforce=()):
        service = TeamSelectionService(
            repository=self.app.state.repository,
            identity_service=self.app.state.identity_service,
            model_router=self.app.state.model_router,
            event_broker=self.app.state.broker,
        )
        task = asyncio.run(
            service.assign_team(self.app.state.repository.get_task_durable(self.task_id))
        )
        selection = task.teamSelection
        return TeamDecision(
            status=selection.status,
            manager_id=selection.managerId,
            member_ids=tuple(selection.selectedAgentIds),
            required_capabilities=tuple(selection.requiredCapabilities),
            missing_capabilities=tuple(selection.requiredCapabilities)
            if selection.status != "completed"
            else (),
        )


class ProductionDecomposer:
    provenance = STAGE_PROVENANCE[StageKind.DECOMPOSE]

    def __init__(self, app, task_id, assembly_id):
        self.app, self.task_id, self.assembly_id = app, task_id, assembly_id

    def decompose(self, *, objective_key, team, required_capabilities=()):
        service = DecompositionService(
            self.app.state.repository, self.app.state.identity_service, self.app.state.model_router
        )
        record = service.current(self.task_id)
        if record is None:
            record = asyncio.run(service.prepare(self.task_id, self.assembly_id))
        if record.status != "ready":
            raise DomainError(
                "COORDINATION_STALE_PLAN", "Production acceptance requires a ready graph.", 409
            )
        by_key = {node.key: node.id for node in record.subtasks}
        return WorkGraph(
            nodes=tuple(
                WorkNode(
                    node_id=node.id,
                    title=node.title,
                    capability=node.requiredCapabilities[0],
                    depends_on=tuple(by_key[key] for key in node.dependsOn),
                    summary_hint=node.deliverable[:500],
                )
                for node in record.subtasks
            )
        )


class ProductionSpecialistExecutor:
    provenance = STAGE_PROVENANCE[StageKind.EXECUTE_SPECIALIST]

    def __init__(self, app, worker_id, task_id):
        self.app, self.worker_id, self.task_id = app, worker_id, task_id

    def advance(self):
        asyncio.run(
            self.app.state.autonomous_worker_service.run_once(self.worker_id, task_id=self.task_id)
        )
        return self.app.state.coordinator_service.repository.current(self.task_id)

    def execute(self, *, node_id, attempt_number, is_repair, assignment):
        record = self.app.state.coordinator_service.repository.current(self.task_id)
        node = next((node for node in record.nodes if node.subtaskId == node_id), None)
        if node is None or node.assignedAgentId != assignment.agent_id:
            raise DomainError(
                "COORDINATION_ASSIGNMENT_INELIGIBLE",
                "Acceptance assignment differs from durable truth.",
                409,
            )
        record = self.advance()
        node = next(node for node in record.nodes if node.subtaskId == node_id)
        return SpecialistOutcome(
            status="succeeded" if node.status == "succeeded" else "failed",
            output_text=json.dumps({"node_id": node_id, "summary": node.resultSummary or ""}),
            summary=node.resultSummary or "",
            failure_category=node.failureCategory,
            failure_detail=node.failureDetail,
        )


class ProductionSynthesizer:
    provenance = STAGE_PROVENANCE[StageKind.SYNTHESIZE]

    def __init__(self, executor):
        self.executor = executor

    def synthesize(self, *, objective_key, results, expected_node_ids):
        record = self.executor.advance()
        if record.synthesis.status != "succeeded":
            raise DomainError("COORDINATION_INCOMPLETE", "Synthesis is not durably accepted.", 409)
        if tuple(record.synthesis.inputSubtaskIds) != expected_node_ids:
            raise DomainError(
                "COORDINATION_SYNTHESIS_STALE", "Acceptance contributors differ.", 409
            )
        return SynthesisResult(
            summary=record.synthesis.summary,
            input_node_ids=expected_node_ids,
            inputs_digest=record.synthesis.inputsDigest,
        )
