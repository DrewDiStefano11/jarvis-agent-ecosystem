"""SQLAlchemy persistence adapter for the authoritative runtime ledger."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from threading import RLock

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.agent_runtime.errors import (
    CommandConflictError,
    LedgerReplayError,
    LedgerSequenceError,
    RunAlreadyExistsError,
    RunNotFoundError,
    RuntimeParentUnavailableError,
    RuntimePersistenceError,
    RuntimeReplayActorMismatchError,
    VersionConflictError,
)
from app.agent_runtime.ledger import replay_execution_ledger
from app.agent_runtime.repository import AgentRuntimeRepository, validate_lineage_invariant
from app.agent_runtime.transitions import TERMINAL_STATES
from app.core.errors import DomainError
from app.db.models import (
    AgentRuntimeAttemptRow,
    AgentRuntimeCheckpointRow,
    AgentRuntimeEventRow,
    AgentRuntimeProcessedCommandRow,
    AgentRuntimeRunRow,
    AuditEventRow,
    OutboxEventRow,
    SystemStateRow,
    TaskRow,
)
from app.models.agent_runtime import (
    AgentRunAttempt,
    AgentRunCheckpoint,
    AgentRunQueryResult,
    AgentRunSnapshot,
    AgentRunState,
    ProcessedCommandRecord,
    RuntimeCommandResult,
    RuntimeEventEnvelope,
    canonical_json,
)
from app.models.domain import EventEnvelope

KNOWN_RUN_STATE_VALUES = tuple(state.value for state in AgentRunState)


def dump(x):
    return canonical_json(x.model_dump(mode="json"))


def load(text, cls):
    return cls.model_validate(json.loads(text))


class SqlAlchemyAgentRuntimeRepository(AgentRuntimeRepository):
    def __init__(self, sessions: sessionmaker[Session], *, outbox_max_attempts: int = 10):
        self.sessions = sessions
        self.outbox_max_attempts = outbox_max_attempts
        self._commit_lock = RLock()

    def _awaiting_run_lock(self, run_id: str, command_id: str) -> None:
        """Deterministic test seam for the run-lock contention window.

        Production behavior is a no-op. On a database with real row-level locks
        a competing transaction can commit while this caller blocks on the
        ``SELECT ... FOR UPDATE`` below, so the lock resolves against
        post-commit state. Concurrency regressions override this to commit a
        competitor here, reproducing that ordering without relying on SQLite
        serializing writes.
        """

    def load_run(self, run_id):
        with self.sessions() as s:
            x = s.get(AgentRuntimeRunRow, run_id)
            return None if x is None else load(x.snapshot_json, AgentRunSnapshot)

    def load_run_state(self, run_id):
        with self.sessions() as s:
            x = s.get(AgentRuntimeRunRow, run_id)
            if not x:
                return None
            es = s.scalars(
                select(AgentRuntimeEventRow)
                .where(AgentRuntimeEventRow.run_id == run_id)
                .order_by(AgentRuntimeEventRow.sequence_number)
            ).all()
            return load(x.snapshot_json, AgentRunSnapshot), [
                load(e.envelope_json, RuntimeEventEnvelope) for e in es
            ]

    def list_events(self, run_id):
        x = self.load_run_state(run_id)
        if not x:
            raise RunNotFoundError(run_id=run_id)
        return x[1]

    def load_attempt_history(self, run_id):
        return self._contracts(run_id, AgentRuntimeAttemptRow, AgentRunAttempt, "attempt_number")

    def list_checkpoints(self, run_id):
        return self._contracts(
            run_id, AgentRuntimeCheckpointRow, AgentRunCheckpoint, "checkpoint_sequence"
        )

    def _contracts(self, run_id, row, cls, order):
        with self.sessions() as s:
            if not s.get(AgentRuntimeRunRow, run_id):
                raise RunNotFoundError(run_id=run_id)
            return [
                load(x.contract_json, cls)
                for x in s.scalars(
                    select(row).where(row.run_id == run_id).order_by(getattr(row, order))
                ).all()
            ]

    def get_processed_command(self, run_id, command_id):
        with self.sessions() as s:
            x = s.get(AgentRuntimeProcessedCommandRow, (run_id, command_id))
            return (
                None
                if not x
                else ProcessedCommandRecord(
                    run_id=run_id,
                    command_id=command_id,
                    command_hash=x.command_hash,
                    result=load(x.result_json, RuntimeCommandResult),
                    recorded_at=x.processed_at.replace(tzinfo=UTC)
                    if x.processed_at.tzinfo is None
                    else x.processed_at,
                    verified_actor_id=x.verified_actor_id,
                    command_type=x.command_type,
                    authorization=json.loads(x.authorization_json or "{}"),
                )
            )

    def create_run(self, snapshot, *, events=()):
        raise RuntimeError("Use commit_command")

    def save_run(self, snapshot, *, expected_version):
        old = self.load_run(snapshot.specification.run_id)
        if old is None:
            raise RunNotFoundError(run_id=snapshot.specification.run_id)
        if old.version != expected_version or old != snapshot:
            raise VersionConflictError(run_id=snapshot.specification.run_id)

    def append_events(self, run_id, events, *, expected_sequence):
        raise LedgerSequenceError("Use command commits.", run_id=run_id)

    def store_processed_command_result(self, record):
        with self.sessions.begin() as s:
            self._store(s, record)

    def query_runs(self, q):
        with self.sessions() as s:
            st = select(AgentRuntimeRunRow)
            for c, v in (
                (AgentRuntimeRunRow.run_id, q.run_id),
                (AgentRuntimeRunRow.task_id, q.task_id),
                (AgentRuntimeRunRow.agent_id, q.agent_id),
                (AgentRuntimeRunRow.correlation_id, q.correlation_id),
                (AgentRuntimeRunRow.parent_run_id, q.parent_run_id),
                (AgentRuntimeRunRow.state, None if not q.state else q.state.value),
            ):
                if v is not None:
                    st = st.where(c == v)
            if q.created_from is not None:
                st = st.where(AgentRuntimeRunRow.created_at >= q.created_from)
            if q.created_to is not None:
                st = st.where(AgentRuntimeRunRow.created_at <= q.created_to)
            if q.terminal is not None:
                st = st.where(
                    AgentRuntimeRunRow.state.in_([x.value for x in TERMINAL_STATES])
                    if q.terminal
                    else AgentRuntimeRunRow.state.not_in([x.value for x in TERMINAL_STATES])
                )
            total = s.scalar(select(func.count()).select_from(st.subquery())) or 0
            rows = s.scalars(
                st.order_by(AgentRuntimeRunRow.created_at, AgentRuntimeRunRow.run_id)
                .offset(q.offset)
                .limit(q.limit)
            ).all()
            return AgentRunQueryResult(
                items=tuple(load(x.snapshot_json, AgentRunSnapshot) for x in rows),
                offset=q.offset,
                limit=q.limit,
                next_offset=q.offset + q.limit if q.offset + q.limit < total else None,
                total_count=total,
            )

    def commit_command(
        self,
        *,
        snapshot,
        events,
        processed_command,
        expected_version,
        expected_sequence,
        create=False,
        require_execution_enabled=False,
    ):
        run_id = snapshot.specification.run_id
        try:
            with self._commit_lock, self.sessions.begin() as s:
                replay = self._replay_processed_command(s, run_id, processed_command)
                if replay is not None:
                    return replay
                self._awaiting_run_lock(run_id, processed_command.command_id)
                row = s.scalar(
                    select(AgentRuntimeRunRow)
                    .where(AgentRuntimeRunRow.run_id == run_id)
                    .with_for_update()
                )
                # Two identical concurrent commands can both observe no processed
                # command before either commits. On a database with real row-level
                # locks the loser blocks on the run lock above, so the record must
                # be re-read after the lock is held and before expected-version
                # validation; otherwise the exact retry would see the committed
                # version increment and wrongly return version_conflict.
                replay = self._replay_processed_command(s, run_id, processed_command, refresh=True)
                if replay is not None:
                    return replay
                if require_execution_enabled:
                    system_state = s.scalar(
                        select(SystemStateRow).where(SystemStateRow.id == 1).with_for_update()
                    )
                    if system_state is not None and system_state.emergency_stop:
                        raise DomainError(
                            "EMERGENCY_STOP_ACTIVE",
                            "Emergency stop is active.",
                            423,
                        )
                old = []
                previous_state: str | None = None
                if create:
                    if row:
                        raise RunAlreadyExistsError(run_id=run_id)
                    if processed_command.authorization.get("parentCheckRequired") is True:
                        current_parent = snapshot.specification.parent_run_id
                        visited = {run_id}
                        while current_parent is not None:
                            if current_parent in visited:
                                break
                            parent_row = s.get(AgentRuntimeRunRow, current_parent)
                            if parent_row is None:
                                raise RuntimeParentUnavailableError()
                            visited.add(current_parent)
                            parent_snapshot = load(parent_row.snapshot_json, AgentRunSnapshot)
                            current_parent = parent_snapshot.specification.parent_run_id
                    validate_lineage_invariant(
                        run_id,
                        snapshot.specification.parent_run_id,
                        lookup=lambda i: (
                            None
                            if (r := s.get(AgentRuntimeRunRow, i)) is None
                            else load(r.snapshot_json, AgentRunSnapshot)
                        ),
                    )
                else:
                    if not row:
                        raise RunNotFoundError(run_id=run_id)
                    previous_state = row.state
                    if row.version != expected_version:
                        replay = self._replay_processed_command(
                            s, run_id, processed_command, refresh=True
                        )
                        if replay is not None:
                            return replay
                        raise VersionConflictError(
                            run_id=run_id,
                            command_id=processed_command.command_id,
                            metadata={
                                "expectedVersion": expected_version,
                                "storedVersion": row.version,
                            },
                        )
                    old = [
                        load(e.envelope_json, RuntimeEventEnvelope)
                        for e in s.scalars(
                            select(AgentRuntimeEventRow)
                            .where(AgentRuntimeEventRow.run_id == run_id)
                            .order_by(AgentRuntimeEventRow.sequence_number)
                        )
                    ]
                    if len(old) != expected_sequence:
                        replay = self._replay_processed_command(
                            s, run_id, processed_command, refresh=True
                        )
                        if replay is not None:
                            return replay
                        raise LedgerSequenceError(
                            run_id=run_id,
                            command_id=processed_command.command_id,
                            metadata={
                                "expectedSequence": expected_sequence,
                                "storedSequence": len(old),
                            },
                        )
                ag = replay_execution_ledger(old + list(events))
                if not ag or ag.snapshot != snapshot:
                    raise LedgerReplayError("Ledger/projection mismatch", run_id=run_id)
                if not create and not events:
                    self._store(s, processed_command)
                    self._store_audit(s, processed_command, snapshot, events, previous_state)
                    s.flush()
                    return None
                if create:
                    run_row = AgentRuntimeRunRow(
                        run_id=run_id,
                        task_id=snapshot.specification.task_id,
                        agent_id=snapshot.specification.agent_id,
                        correlation_id=snapshot.specification.correlation_id,
                        parent_run_id=snapshot.specification.parent_run_id,
                        state=snapshot.state.value,
                        version=snapshot.version,
                        event_sequence_number=snapshot.event_sequence_number,
                        attempt_count=snapshot.attempt_count,
                        active_attempt_id=snapshot.active_attempt_id,
                        latest_checkpoint_id=snapshot.latest_checkpoint_id,
                        recovery_status=snapshot.recovery_status.value,
                        created_at=snapshot.created_at,
                        updated_at=snapshot.created_at,
                        deadline=snapshot.specification.deadline,
                        terminal_at=snapshot.completed_at,
                        specification_json=dump(snapshot.specification),
                        snapshot_json=dump(snapshot),
                    )
                    s.add(run_row)
                    # The ledger/event rows have an FK to the durable run projection.
                    # Flush only the parent; the encompassing transaction remains atomic.
                    s.flush([run_row])
                else:
                    row.state = snapshot.state.value
                    row.version = snapshot.version
                    row.event_sequence_number = snapshot.event_sequence_number
                    row.attempt_count = snapshot.attempt_count
                    row.active_attempt_id = snapshot.active_attempt_id
                    row.latest_checkpoint_id = snapshot.latest_checkpoint_id
                    row.recovery_status = snapshot.recovery_status.value
                    row.terminal_at = snapshot.completed_at
                    row.updated_at = datetime.now(UTC)
                    row.snapshot_json = dump(snapshot)
                for e in events:
                    s.add(
                        AgentRuntimeEventRow(
                            event_id=e.event_id,
                            run_id=e.run_id,
                            attempt_id=e.attempt_id,
                            event_type=e.event_type.value,
                            schema_version=e.event_schema_version,
                            sequence_number=e.sequence_number,
                            run_version=e.run_version,
                            timestamp=e.timestamp,
                            actor_reference=e.actor_reference,
                            command_id=e.command_id,
                            correlation_id=e.correlation_id,
                            causation_id=e.causation_id,
                            payload_json=canonical_json(e.payload),
                            metadata_json=canonical_json(e.metadata),
                            envelope_json=dump(e),
                        )
                    )
                    self._store_outbox(s, e)
                s.query(AgentRuntimeCheckpointRow).filter_by(run_id=run_id).delete()
                s.query(AgentRuntimeAttemptRow).filter_by(run_id=run_id).delete()
                for x in ag.attempts:
                    s.add(
                        AgentRuntimeAttemptRow(
                            attempt_id=x.attempt_id,
                            run_id=run_id,
                            attempt_number=x.attempt_number,
                            contract_json=dump(x),
                        )
                    )
                for x in ag.checkpoints:
                    s.add(
                        AgentRuntimeCheckpointRow(
                            checkpoint_id=x.checkpoint_id,
                            run_id=run_id,
                            attempt_id=x.attempt_id,
                            checkpoint_sequence=x.checkpoint_sequence,
                            contract_json=dump(x),
                        )
                    )
                self._store(s, processed_command)
                self._store_audit(s, processed_command, snapshot, events, previous_state)
                s.flush()
                return None
        except IntegrityError as exc:
            # A duplicate processed-command constraint is the only integrity race
            # that is safely recoverable at this boundary.  Every other database
            # integrity failure is rolled back and surfaced as a persistence error.
            persisted = self.get_processed_command(run_id, processed_command.command_id)
            if persisted is not None:
                if persisted.command_hash != processed_command.command_hash:
                    raise CommandConflictError(
                        run_id=run_id,
                        command_id=processed_command.command_id,
                    ) from exc
                return persisted
            if create:
                raise RunAlreadyExistsError(
                    run_id=run_id, command_id=processed_command.command_id
                ) from exc
            raise RuntimePersistenceError(
                run_id=run_id,
                command_id=processed_command.command_id,
                metadata={"constraint": "runtime_transaction"},
            ) from exc

    @staticmethod
    def _replay_processed_command(
        session: Session,
        run_id: str,
        processed_command: ProcessedCommandRecord,
        *,
        refresh: bool = False,
    ) -> ProcessedCommandRecord | None:
        """Return the stored record for an exact replay, or raise on a changed command.

        Returning ``None`` means the command has not been processed yet and the
        caller must continue with expected-version and transition validation.
        ``refresh`` forces a database read instead of an identity-map hit, which
        the post-lock recheck requires to observe a concurrently committed row.
        """
        prior = session.get(
            AgentRuntimeProcessedCommandRow,
            (run_id, processed_command.command_id),
            populate_existing=refresh,
        )
        if prior is None:
            return None
        if (
            prior.verified_actor_id
            and processed_command.verified_actor_id
            and prior.verified_actor_id != processed_command.verified_actor_id
        ):
            raise RuntimeReplayActorMismatchError(
                run_id=run_id, command_id=processed_command.command_id
            )
        if prior.command_hash != processed_command.command_hash:
            raise CommandConflictError(run_id=run_id, command_id=processed_command.command_id)
        return ProcessedCommandRecord(
            run_id=run_id,
            command_id=prior.command_id,
            command_hash=prior.command_hash,
            result=load(prior.result_json, RuntimeCommandResult),
            recorded_at=prior.processed_at.replace(tzinfo=UTC)
            if prior.processed_at.tzinfo is None
            else prior.processed_at,
            verified_actor_id=prior.verified_actor_id,
            command_type=prior.command_type,
            authorization=json.loads(prior.authorization_json or "{}"),
        )

    def _store(self, s, x):
        s.add(
            AgentRuntimeProcessedCommandRow(
                run_id=x.run_id,
                command_id=x.command_id,
                command_hash=x.command_hash,
                command_type=x.command_type,
                verified_actor_id=x.verified_actor_id,
                authorization_json=canonical_json(x.authorization),
                result_json=dump(x.result),
                processed_at=x.recorded_at,
            )
        )

    @staticmethod
    def _runtime_session_id(run_id: str) -> str:
        return f"runtime-{sha256(run_id.encode()).hexdigest()[:64]}"

    def _store_outbox(self, session: Session, event: RuntimeEventEnvelope) -> None:
        event_type = f"agent_runtime.{event.event_type.value}"
        event_session_id = self._runtime_session_id(event.run_id)
        correlation_id = event.correlation_id or event.run_id
        dispatcher_envelope = EventEnvelope(
            eventId=event.event_id,
            schemaVersion=event.event_schema_version,
            eventType=event_type,
            timestamp=event.timestamp,
            sequenceNumber=event.sequence_number,
            eventSessionId=event_session_id,
            correlationId=correlation_id,
            source="agent_runtime",
            payload={"runtimeEvent": event.model_dump(mode="json")},
        )
        session.add(
            OutboxEventRow(
                id=event.event_id,
                event_type=event_type,
                envelope=dispatcher_envelope.model_dump(mode="json"),
                correlation_id=correlation_id,
                event_session_id=event_session_id,
                sequence_number=event.sequence_number,
                status="pending",
                created_at=datetime.now(UTC),
                published_at=None,
                publish_attempt_count=0,
                last_publish_error=None,
            )
        )

    def _store_audit(
        self,
        session: Session,
        command: ProcessedCommandRecord,
        snapshot: AgentRunSnapshot,
        events: object,
        previous_state: str | None,
    ) -> None:
        event_list = list(events)
        event_session_id = self._runtime_session_id(command.run_id)
        audit_sequence = (
            session.scalar(
                select(func.max(AuditEventRow.sequence_number)).where(
                    AuditEventRow.event_session_id == event_session_id
                )
            )
            or 0
        ) + 1
        audit_identity = canonical_json(
            {"commandId": command.command_id, "runId": command.run_id}
        ).encode()
        audit_id = f"runtime-{sha256(audit_identity).hexdigest()}"
        actor_id = command.verified_actor_id or "runtime-control-plane"
        task_id = (
            snapshot.specification.task_id
            if session.get(TaskRow, snapshot.specification.task_id) is not None
            else None
        )
        session.add(
            AuditEventRow(
                id=audit_id,
                event_type="agent_runtime.command",
                actor=actor_id,
                agent_id=None,
                task_id=task_id,
                approval_id=None,
                previous_state=previous_state,
                new_state=snapshot.state.value,
                correlation_id=snapshot.specification.correlation_id or command.run_id,
                sequence_number=audit_sequence,
                event_session_id=event_session_id,
                timestamp=command.recorded_at,
                payload={
                    "summary": f"Runtime command {command.command_type}",
                    "payload": {
                        "verifiedActorId": command.verified_actor_id,
                        "commandType": command.command_type,
                        "commandId": command.command_id,
                        "runId": command.run_id,
                        "taskId": snapshot.specification.task_id,
                        "targetAgentId": snapshot.specification.agent_id,
                        "eventIds": [event.event_id for event in event_list],
                        "authorization": command.authorization,
                        "idempotentReplay": False,
                    },
                    "artifactIds": [],
                },
                schema_version="1.0",
            )
        )

    def health_status(self) -> dict[str, int | bool]:
        """Bounded health summary; it intentionally never replays every ledger."""
        try:
            with self.sessions() as session:
                nonterminal = session.scalar(
                    select(func.count())
                    .select_from(AgentRuntimeRunRow)
                    .where(
                        AgentRuntimeRunRow.state.not_in([state.value for state in TERMINAL_STATES])
                    )
                )
                exhausted = session.scalar(
                    select(func.count())
                    .select_from(OutboxEventRow)
                    .where(
                        OutboxEventRow.status == "failed",
                        OutboxEventRow.publish_attempt_count >= self.outbox_max_attempts,
                        OutboxEventRow.event_type.like("agent_runtime.%"),
                    )
                )
                # Bounded corruption probe: an unknown persisted state means the
                # durable projection can no longer be replayed or trusted.
                corrupt = session.scalar(
                    select(func.count())
                    .select_from(AgentRuntimeRunRow)
                    .where(AgentRuntimeRunRow.state.not_in(KNOWN_RUN_STATE_VALUES))
                )
        except OperationalError as exc:
            if "no such table" not in str(exc).lower():
                raise
            return {"configured": False, "nonterminalRunCount": 0}
        health: dict[str, int | bool | str] = {
            "configured": True,
            "nonterminalRunCount": nonterminal or 0,
            "outboxExhaustedCount": exhausted or 0,
        }
        if corrupt:
            health["status"] = "degraded"
            health["reasonCode"] = "runtime_projection_corrupt"
        return health

    def integrity_check(self, run_id):
        state = self.load_run_state(run_id)
        if not state:
            raise RunNotFoundError(run_id=run_id)
        ag = replay_execution_ledger(state[1])
        if (
            not ag
            or ag.snapshot != state[0]
            or list(ag.attempts) != self.load_attempt_history(run_id)
            or list(ag.checkpoints) != self.list_checkpoints(run_id)
        ):
            raise LedgerReplayError("Durable projection mismatch", run_id=run_id)
        return True
