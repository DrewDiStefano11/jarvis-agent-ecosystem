"""Bounded advisory cycles; native idempotency and backlog own admission/recovery."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import psutil
from sqlalchemy import exists, select, text
from sqlalchemy.orm import aliased

from app.core.errors import DomainError
from app.db.models import IdempotencyRecordRow, SystemStateRow
from app.models.improvement_backlog import SelectImprovementRequest
from app.models.improvement_schedule import (
    ImprovementScheduleCapture,
    ImprovementSchedulePolicy,
    ImprovementScheduleResult,
    ImprovementScheduleStatus,
)
from app.repositories.sqlalchemy import IdempotencyResult
from app.self_improvement.backlog_service import ImprovementBacklogService
from app.self_improvement.engine import digest
from app.self_improvement.repository import ImprovementRecordRow
from app.self_improvement.runtime import RuntimeHistorySource
from app.self_improvement.service import ImprovementService

CAPTURE = "schedule_capture"
RESULT = "schedule_result"
COMMAND = "improvement.schedule"
# Changes to capture, analysis, authority or native admission invalidate policy identity.
POLICY_SOURCES = (
    "self_improvement/schedule.py",
    "models/improvement_schedule.py",
    "self_improvement/service.py",
    "self_improvement/repository.py",
    "self_improvement/runtime.py",
    "self_improvement/adapters.py",
    "self_improvement/engine.py",
    "models/self_improvement.py",
    "self_improvement/backlog_service.py",
    "self_improvement/backlog_repository.py",
    "self_improvement/backlog_selection.py",
    "identity/service.py",
    "repositories/sqlalchemy.py",
)


def policy_digest(policy):
    root = Path(__file__).resolve().parents[1]
    return digest(
        [
            policy.model_dump(mode="json"),
            {name: digest((root / name).read_text(encoding="utf-8")) for name in POLICY_SOURCES},
        ]
    )


def resources_available(directory):
    """Read-only, bounded sample. Unknown resource state fails closed."""
    try:
        import shutil

        return (
            psutil.cpu_percent(interval=0.1) < 85
            and psutil.virtual_memory().available >= 512 * 1024**2
            and shutil.disk_usage(directory).free >= 1024**3
        )
    except (OSError, psutil.Error):
        return False


class ImprovementScheduler:
    def __init__(
        self, repository, broker, identity, policy, *, source_factory=None, resources=None
    ):
        self.repository, self.identity, self.policy = repository, identity, policy
        self.sessions = repository.session_factory
        self.backlog = ImprovementBacklogService(repository, broker, identity)
        self.analyzer = ImprovementService(self.sessions)
        self.source_factory = source_factory or (
            lambda start, end: RuntimeHistorySource(self.sessions, start, end)
        )
        self.resources = resources or (lambda: False)

    def authorize(self, session, expected, *, claim=None, admission=False):
        policy = ImprovementSchedulePolicy.model_validate_json(self.policy.model_dump_json())
        if not policy.enabled or policy_digest(policy) != expected:
            raise DomainError("IMPROVEMENT_SCHEDULE_CHANGED", "Scheduled policy changed.", 409)
        state = session.get(SystemStateRow, 1)
        if state is None or state.emergency_stop:
            raise DomainError("EMERGENCY_STOP_ACTIVE", "Scheduling is stopped.", 423)
        decision = self.identity.check_resource_access(
            policy.actor_id,
            "administrative_function",
            "improvement_schedule",
            "analyze_improvement",
            session=session,
        )
        if not decision.allowed:
            raise DomainError(
                "IMPROVEMENT_SCHEDULE_PERMISSION_DENIED", "Scheduled analysis is denied.", 403
            )
        if claim:
            key, expiration = claim
            row = session.scalar(
                select(IdempotencyRecordRow).where(
                    IdempotencyRecordRow.idempotency_key == key,
                    IdempotencyRecordRow.command_type == COMMAND,
                    IdempotencyRecordRow.response_status == 0,
                    IdempotencyRecordRow.expiration_at == expiration,
                    IdempotencyRecordRow.expiration_at > datetime.now(UTC),
                )
            )
            if row is None:
                raise DomainError(
                    "IMPROVEMENT_SCHEDULE_LEASE_LOST",
                    "Scheduled cycle no longer owns its claim.",
                    409,
                )
        if not self.resources():
            raise DomainError(
                "IMPROVEMENT_SCHEDULE_RESOURCE_BUSY",
                "Scheduled resource budget is unavailable.",
                409,
            )
        if admission:
            count, _ = self.backlog.backlog.active_scopes_in_session(session)
            if count >= 1:
                raise DomainError(
                    "IMPROVEMENT_SCHEDULE_WORK_ACTIVE",
                    "An improvement task is already active.",
                    409,
                )

    def _check(self, expected, **kwargs):
        with self.sessions() as session:
            self.authorize(session, expected, **kwargs)

    def _capture(self, now, expected):
        # Recover one oldest incomplete cycle for this exact policy before opening
        # a new window. Completed historical windows are never caught up in bulk.
        result = aliased(ImprovementRecordRow)
        with self.sessions() as session:
            pending = session.scalar(
                select(ImprovementRecordRow)
                .where(
                    ImprovementRecordRow.kind == CAPTURE,
                    ImprovementRecordRow.baseline_id == expected,
                    ~exists(
                        select(result.id).where(
                            result.kind == RESULT, result.baseline_id == ImprovementRecordRow.id
                        )
                    ),
                )
                .order_by(ImprovementRecordRow.created_at, ImprovementRecordRow.id)
                .limit(1)
            )
            if pending:
                return ImprovementScheduleCapture.model_validate(pending.payload)
        epoch = int(now.timestamp()) // self.policy.interval_seconds * self.policy.interval_seconds
        capture_id = digest(["improvement-schedule-v1", expected, epoch])
        with self.sessions() as session:
            stored = session.get(ImprovementRecordRow, capture_id)
            if stored:
                if stored.kind != CAPTURE or stored.baseline_id != expected:
                    raise ValueError("schedule record identity conflict")
                return ImprovementScheduleCapture.model_validate(stored.payload)
        end = datetime.fromtimestamp(epoch, UTC)
        provenance, observations = self.source_factory(
            end - timedelta(seconds=self.policy.lookback_seconds), end
        ).collect()
        capture = ImprovementScheduleCapture(
            id=capture_id,
            policy_digest=expected,
            window_end=end,
            provenance=provenance,
            observations=tuple(observations),
            snapshot_digest=digest(
                [
                    provenance.model_dump(mode="json"),
                    [item.model_dump(mode="json") for item in observations],
                ]
            ),
        )
        # The first committed capture wins if two processes observed changing rows.
        # A later process must replay that snapshot, not overwrite it.
        from sqlalchemy.exc import IntegrityError

        try:
            with self.sessions() as session, session.begin():
                session.execute(text("BEGIN IMMEDIATE"))
                self.authorize(session, expected)
                session.add(
                    ImprovementRecordRow(
                        id=capture.id,
                        kind=CAPTURE,
                        baseline_id=expected,
                        created_at=now,
                        payload=capture.model_dump(mode="json"),
                    )
                )
        except IntegrityError:
            with self.sessions() as session:
                row = session.get(ImprovementRecordRow, capture.id)
                if row is None or row.kind != CAPTURE or row.baseline_id != expected:
                    raise
                capture = ImprovementScheduleCapture.model_validate(row.payload)
        return capture

    async def tick(self, now=None):
        if not self.policy.enabled:
            return None
        now = now or datetime.now(UTC)
        if now.tzinfo is None:
            raise ValueError("schedule clock requires a timezone")
        self.backlog.backlog.assert_supported()
        expected = policy_digest(self.policy)
        if not self.resources():
            return None
        self._check(expected)
        capture = self._capture(now, expected)
        key = "improvement-schedule-" + capture.id
        payload = {"capture_id": capture.id, "snapshot_digest": capture.snapshot_digest}
        claim = self.repository.idempotency_claim(key, COMMAND, payload)
        if claim.response:
            return ImprovementScheduleResult.model_validate(claim.response[1]["data"])
        assert claim.owned and claim.lease_expires_at is not None
        fence = (key, claim.lease_expires_at)
        try:
            scheduler = self

            class FrozenSource:
                def collect(self):
                    return capture.provenance, capture.observations

            analysis = self.analyzer.analyze(
                [FrozenSource()],
                repo_sha=self.policy.repo_sha,
                configuration_fingerprint=self.policy.configuration_fingerprint,
                safety_fingerprint=self.policy.safety_fingerprint,
                authorize=lambda session: scheduler.authorize(session, expected, claim=fence),
            )
            outcome, task_id = "analyzed", None
            if self.policy.auto_admit:
                try:
                    self._check(expected, claim=fence)
                    selected = await self.backlog.select(
                        SimpleNamespace(actor_id=self.policy.actor_id),
                        SelectImprovementRequest(baseline_ids=(analysis.baseline.id,)),
                        capture.id,
                        additional_authorize=lambda session: scheduler.authorize(
                            session, expected, claim=fence, admission=True
                        ),
                    )
                    outcome = "selected" if selected.entry else selected.outcome
                    task_id = selected.entry.task_id if selected.entry else None
                except DomainError as error:
                    if error.code != "IMPROVEMENT_SCHEDULE_WORK_ACTIVE":
                        raise
                    outcome = "blocked"
            completed = ImprovementScheduleResult(
                capture_id=capture.id,
                baseline_id=analysis.baseline.id,
                outcome=outcome,
                task_id=task_id,
            )

            def commit(session):
                scheduler.authorize(session, expected, claim=fence)
                session.add(
                    ImprovementRecordRow(
                        id=digest(["improvement-schedule-result-v1", capture.id]),
                        kind=RESULT,
                        baseline_id=capture.id,
                        created_at=now,
                        payload=completed.model_dump(mode="json"),
                    )
                )

            self.repository.complete_idempotency(
                IdempotencyResult(
                    key=key,
                    command=COMMAND,
                    payload=payload,
                    status=200,
                    body={"data": completed.model_dump(mode="json")},
                    lease_expires_at=claim.lease_expires_at,
                ),
                authorize=commit,
            )
            return completed
        finally:
            self.repository.idempotency_abandon(key, COMMAND, claim.lease_expires_at)

    def status(self, actor_id):
        decision = self.identity.check_resource_access(
            actor_id, "administrative_function", "improvement_schedule", "read_improvement"
        )
        if not decision.allowed:
            raise DomainError(
                "IMPROVEMENT_SCHEDULE_PERMISSION_DENIED", "Scheduled evidence read is denied.", 403
            )
        expected = policy_digest(self.policy)
        capture, completed = None, None
        with self.sessions() as session:
            row = session.scalar(
                select(ImprovementRecordRow)
                .where(
                    ImprovementRecordRow.kind == CAPTURE,
                    ImprovementRecordRow.baseline_id == expected,
                )
                .order_by(ImprovementRecordRow.created_at.desc(), ImprovementRecordRow.id)
                .limit(1)
            )
            if row:
                capture = ImprovementScheduleCapture.model_validate(row.payload)
                result = session.get(
                    ImprovementRecordRow, digest(["improvement-schedule-result-v1", capture.id])
                )
                if result:
                    if result.kind != RESULT or result.baseline_id != capture.id:
                        raise ValueError("schedule result identity conflicts")
                    completed = ImprovementScheduleResult.model_validate(result.payload)
        return ImprovementScheduleStatus(
            enabled=self.policy.enabled,
            auto_admit=self.policy.auto_admit,
            interval_seconds=self.policy.interval_seconds,
            capture_id=capture.id if capture else None,
            window_end=capture.window_end if capture else None,
            result=completed,
        )

    def records(self):
        """Bounded internal inspection; public reads require separate operator auth."""
        with self.sessions() as session:
            return [
                row.payload
                for row in session.scalars(
                    select(ImprovementRecordRow)
                    .where(ImprovementRecordRow.kind.in_((CAPTURE, RESULT)))
                    .order_by(ImprovementRecordRow.created_at, ImprovementRecordRow.id)
                    .limit(100)
                )
            ]
