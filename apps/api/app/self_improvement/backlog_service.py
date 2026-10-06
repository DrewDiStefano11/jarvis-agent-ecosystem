"""One authorized task admission from recorded proposals; no execution authority."""

from datetime import UTC, datetime
from hashlib import sha256

from app.core.errors import DomainError
from app.models.domain import CreateTaskRequest
from app.models.improvement_backlog import (
    ImprovementBacklogEntry,
    ImprovementBacklogSelection,
    SelectImprovementRequest,
)
from app.repositories.sqlalchemy import IdempotencyResult
from app.self_improvement.backlog_repository import ImprovementBacklogRepository
from app.self_improvement.backlog_selection import ordered_candidates
from app.self_improvement.engine import digest
from app.services.task_creation import prepare_task_creation


class ImprovementBacklogService:
    def __init__(self, repository, broker, identity):
        self.repository, self.broker, self.identity = repository, broker, identity
        self.backlog = ImprovementBacklogRepository(repository.session_factory)

    def authorize(self, actor, session=None, *, operation="select"):
        decision = self.identity.check_resource_access(
            actor.actor_id,
            "administrative_function",
            "improvement_backlog",
            {"select": "select_improvement", "read": "read_improvement"}[operation],
            **({"session": session} if session is not None else {}),
        )
        if not decision.allowed:
            raise DomainError(
                "IMPROVEMENT_BACKLOG_PERMISSION_DENIED", "Backlog access is denied.", 403
            )

    async def select(self, actor, request, idempotency_key):
        self.authorize(actor)
        self.backlog.assert_supported()
        request = SelectImprovementRequest.model_validate_json(request.model_dump_json())
        if (
            not isinstance(idempotency_key, str)
            or not 1 <= len(idempotency_key) <= 200
            or any(ord(character) < 32 or ord(character) == 127 for character in idempotency_key)
        ):
            raise DomainError(
                "INVALID_IDEMPOTENCY_KEY", "A bounded idempotency key is required.", 422
            )
        key = (
            "improvement-select-"
            + sha256((actor.actor_id + ":" + idempotency_key).encode()).hexdigest()
        )
        command, payload = "improvement.backlog.select", request.model_dump(mode="json")
        claim = self.repository.idempotency_claim(key, command, payload)
        if claim.response is not None:
            result = ImprovementBacklogSelection.model_validate(claim.response[1]["data"])
            return result.model_copy(update={"outcome": "replayed"}) if result.entry else result
        assert claim.owned and claim.lease_expires_at is not None
        try:
            candidates = ordered_candidates(self.backlog.analyses(request.baseline_ids))
            admitted, active = self.backlog.admission_state(request.baseline_ids)
            blocked = 0
            for candidate in candidates:
                proposal, analysis = candidate.proposal, candidate.analysis
                if proposal.id in admitted or candidate.scope_key in active:
                    blocked += 1
                    continue
                entry_id = digest(
                    ["improvement-backlog-entry-v1", analysis.baseline.id, proposal.id]
                )
                entry = ImprovementBacklogEntry(
                    id=entry_id,
                    baseline_id=analysis.baseline.id,
                    proposal_id=proposal.id,
                    weakness_id=proposal.weakness_id,
                    scope_key=candidate.scope_key,
                    task_id="task-improve-" + entry_id[:32],
                    priority=proposal.priority,
                    work_kind=candidate.work_kind,
                    evidence_ids=proposal.evidence_ids,
                    experiment_digest=digest(proposal.experiment) if proposal.experiment else None,
                    selected_by=actor.actor_id,
                    selected_at=datetime.now(UTC),
                )
                task = prepare_task_creation(
                    CreateTaskRequest(
                        title=(
                            "Gather evidence: "
                            if entry.work_kind == "gather_evidence"
                            else "Prepare experiment: "
                        )
                        + proposal.target_subsystem[:120],
                        description=(
                            f"Recorded improvement opportunity; no change is approved.\n"
                            f"Baseline: {entry.baseline_id}\nProposal: {entry.proposal_id}\n"
                            f"Work kind: {entry.work_kind}\n"
                            f"Proposed scope: {proposal.change_description[:1000]}\n"
                            "Retain the baseline evidence and frozen experiment criteria. "
                            "Execution still requires the native reviewed-plan and runtime permissions."
                        ),
                        priority="urgent" if proposal.priority == "critical" else proposal.priority,
                    )
                )
                task.id, task.createdBy = entry.task_id, actor.actor_id
                result = ImprovementBacklogSelection(
                    outcome="selected",
                    entry=entry,
                    scanned_proposals=len(candidates),
                    blocked_proposals=blocked,
                )

                def admit(session, selected=entry):
                    self.authorize(actor, session)
                    self.backlog.admit_in_session(session, selected)

                try:
                    await self.broker.emit(
                        "task.created",
                        {"task": task.model_dump(mode="json")},
                        task.id,
                        correlation_id=key,
                        source="improvement-selector",
                        created_task=task,
                        authorize=admit,
                        audit={
                            "summary": "Admitted evidence-backed improvement work",
                            "payload": {
                                "verifiedActorId": actor.actor_id,
                                "improvementEntryId": entry.id,
                                "baselineId": entry.baseline_id,
                                "proposalId": entry.proposal_id,
                            },
                        },
                        idempotency=IdempotencyResult(
                            key=key,
                            command=command,
                            payload=payload,
                            status=201,
                            body={"data": result.model_dump(mode="json")},
                            lease_expires_at=claim.lease_expires_at,
                            resource_id=task.id,
                        ),
                    )
                except DomainError as error:
                    if error.code != "IMPROVEMENT_WORK_ALREADY_ADMITTED":
                        raise
                    blocked += 1
                    active.add(candidate.scope_key)
                    continue
                return result
            result = ImprovementBacklogSelection(
                outcome="blocked" if blocked else "empty",
                scanned_proposals=len(candidates),
                blocked_proposals=blocked,
            )
            self.repository.complete_idempotency(
                IdempotencyResult(
                    key=key,
                    command=command,
                    payload=payload,
                    status=200,
                    body={"data": result.model_dump(mode="json")},
                    lease_expires_at=claim.lease_expires_at,
                ),
                authorize=lambda session: self.authorize(actor, session),
            )
            return result
        finally:
            self.repository.idempotency_abandon(key, command, claim.lease_expires_at)

    def list_items(self, actor, *, offset=0, limit=20):
        self.authorize(actor, operation="read")
        return self.backlog.items(offset=offset, limit=limit)
