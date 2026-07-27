from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from threading import RLock
from typing import Protocol

from app.agent_runtime.errors import (
    CommandConflictError,
    LedgerSequenceError,
    RunAlreadyExistsError,
    RunNotFoundError,
    VersionConflictError,
)
from app.agent_runtime.ledger import replay_execution_ledger
from app.agent_runtime.transitions import TERMINAL_STATES
from app.models.agent_runtime import (
    AgentRunAttempt,
    AgentRunCheckpoint,
    AgentRunQuery,
    AgentRunQueryResult,
    AgentRunSnapshot,
    CommandId,
    ProcessedCommandRecord,
    RunId,
    RuntimeEventEnvelope,
)


class ExecutionLedgerAppender(Protocol):
    def append_events(
        self,
        run_id: RunId,
        events: Sequence[RuntimeEventEnvelope],
        *,
        expected_sequence: int,
    ) -> None: ...


class AgentRuntimeRepository(ExecutionLedgerAppender, Protocol):
    def create_run(
        self,
        snapshot: AgentRunSnapshot,
        *,
        events: Sequence[RuntimeEventEnvelope] = (),
    ) -> None: ...

    def load_run(self, run_id: RunId) -> AgentRunSnapshot | None: ...

    def save_run(self, snapshot: AgentRunSnapshot, *, expected_version: int) -> None: ...

    def list_events(self, run_id: RunId) -> list[RuntimeEventEnvelope]: ...

    def load_attempt_history(self, run_id: RunId) -> list[AgentRunAttempt]: ...

    def list_checkpoints(self, run_id: RunId) -> list[AgentRunCheckpoint]: ...

    def get_processed_command(
        self,
        run_id: RunId,
        command_id: CommandId,
    ) -> ProcessedCommandRecord | None: ...

    def store_processed_command_result(
        self,
        record: ProcessedCommandRecord,
    ) -> None: ...

    def query_runs(self, query: AgentRunQuery) -> AgentRunQueryResult: ...

    def commit_command(
        self,
        *,
        snapshot: AgentRunSnapshot,
        events: Sequence[RuntimeEventEnvelope],
        processed_command: ProcessedCommandRecord,
        expected_version: int,
        expected_sequence: int,
        create: bool = False,
    ) -> None: ...


class InMemoryAgentRuntimeRepository(AgentRuntimeRepository):
    """Deterministic nonpersistent reference repository for runtime-domain testing."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._snapshots: dict[str, AgentRunSnapshot] = {}
        self._events: dict[str, list[RuntimeEventEnvelope]] = {}
        self._processed: dict[str, dict[str, ProcessedCommandRecord]] = {}

    def create_run(
        self,
        snapshot: AgentRunSnapshot,
        *,
        events: Sequence[RuntimeEventEnvelope] = (),
    ) -> None:
        with self._lock:
            if snapshot.specification.run_id in self._snapshots:
                raise RunAlreadyExistsError(run_id=snapshot.specification.run_id)
            self._snapshots[snapshot.specification.run_id] = snapshot.model_copy(deep=True)
            self._events[snapshot.specification.run_id] = [
                item.model_copy(deep=True) for item in events
            ]
            self._processed[snapshot.specification.run_id] = {}

    def load_run(self, run_id: RunId) -> AgentRunSnapshot | None:
        with self._lock:
            snapshot = self._snapshots.get(run_id)
            return None if snapshot is None else snapshot.model_copy(deep=True)

    def save_run(self, snapshot: AgentRunSnapshot, *, expected_version: int) -> None:
        with self._lock:
            existing = self._snapshots.get(snapshot.specification.run_id)
            if existing is None:
                raise RunNotFoundError(run_id=snapshot.specification.run_id)
            if existing.version != expected_version:
                raise VersionConflictError(
                    run_id=snapshot.specification.run_id,
                    metadata={
                        "expectedVersion": expected_version,
                        "storedVersion": existing.version,
                    },
                )
            self._snapshots[snapshot.specification.run_id] = snapshot.model_copy(deep=True)

    def append_events(
        self,
        run_id: RunId,
        events: Sequence[RuntimeEventEnvelope],
        *,
        expected_sequence: int,
    ) -> None:
        with self._lock:
            if run_id not in self._events:
                raise RunNotFoundError(run_id=run_id)
            current_events = [item.model_copy(deep=True) for item in self._events[run_id]]
            if len(current_events) != expected_sequence:
                raise LedgerSequenceError(
                    "The expected ledger sequence does not match the stored ledger.",
                    run_id=run_id,
                    metadata={
                        "expectedSequence": expected_sequence,
                        "storedSequence": len(current_events),
                    },
                )
            appended = [item.model_copy(deep=True) for item in events]
            aggregate = replay_execution_ledger(current_events + appended)
            if aggregate is None:
                raise LedgerSequenceError(
                    "Appending events must produce a runtime snapshot.",
                    run_id=run_id,
                )
            self._events[run_id] = current_events + appended
            self._snapshots[run_id] = aggregate.snapshot.model_copy(deep=True)

    def list_events(self, run_id: RunId) -> list[RuntimeEventEnvelope]:
        with self._lock:
            events = self._events.get(run_id)
            if events is None:
                raise RunNotFoundError(run_id=run_id)
            return [item.model_copy(deep=True) for item in events]

    def load_attempt_history(self, run_id: RunId) -> list[AgentRunAttempt]:
        aggregate = self._replay(run_id)
        return [item.model_copy(deep=True) for item in aggregate.attempts]

    def list_checkpoints(self, run_id: RunId) -> list[AgentRunCheckpoint]:
        aggregate = self._replay(run_id)
        return [item.model_copy(deep=True) for item in aggregate.checkpoints]

    def get_processed_command(
        self,
        run_id: RunId,
        command_id: CommandId,
    ) -> ProcessedCommandRecord | None:
        with self._lock:
            records = self._processed.get(run_id)
            if records is None:
                return None
            record = records.get(command_id)
            return None if record is None else record.model_copy(deep=True)

    def store_processed_command_result(self, record: ProcessedCommandRecord) -> None:
        with self._lock:
            if record.run_id not in self._processed:
                raise RunNotFoundError(run_id=record.run_id)
            existing = self._processed[record.run_id].get(record.command_id)
            if existing is not None and existing.command_hash != record.command_hash:
                raise CommandConflictError(
                    run_id=record.run_id,
                    command_id=record.command_id,
                )
            self._processed[record.run_id][record.command_id] = record.model_copy(deep=True)

    def query_runs(self, query: AgentRunQuery) -> AgentRunQueryResult:
        with self._lock:
            snapshots = [item.model_copy(deep=True) for item in self._snapshots.values()]
        filtered = [snapshot for snapshot in snapshots if self._matches_query(snapshot, query)]
        ordered = sorted(filtered, key=lambda item: (item.created_at, item.specification.run_id))
        items = tuple(ordered[query.offset : query.offset + query.limit])
        next_offset = (
            query.offset + query.limit if query.offset + query.limit < len(ordered) else None
        )
        return AgentRunQueryResult(
            items=items,
            offset=query.offset,
            limit=query.limit,
            next_offset=next_offset,
            total_count=len(ordered),
        )

    def commit_command(
        self,
        *,
        snapshot: AgentRunSnapshot,
        events: Sequence[RuntimeEventEnvelope],
        processed_command: ProcessedCommandRecord,
        expected_version: int,
        expected_sequence: int,
        create: bool = False,
    ) -> None:
        run_id = snapshot.specification.run_id
        with self._lock:
            existing_snapshot = self._snapshots.get(run_id)
            existing_events = self._events.get(run_id, [])
            existing_processed = self._processed.get(run_id, {})
            if create:
                if existing_snapshot is not None:
                    raise RunAlreadyExistsError(run_id=run_id)
                if expected_version != 0 or expected_sequence != 0:
                    raise VersionConflictError(
                        run_id=run_id,
                        command_id=processed_command.command_id,
                        metadata={
                            "expectedVersion": expected_version,
                            "expectedSequence": expected_sequence,
                        },
                    )
            else:
                if existing_snapshot is None:
                    raise RunNotFoundError(run_id=run_id)
                if existing_snapshot.version != expected_version:
                    raise VersionConflictError(
                        run_id=run_id,
                        command_id=processed_command.command_id,
                        metadata={
                            "expectedVersion": expected_version,
                            "storedVersion": existing_snapshot.version,
                        },
                    )
                if len(existing_events) != expected_sequence:
                    raise LedgerSequenceError(
                        "The expected ledger sequence does not match the stored ledger.",
                        run_id=run_id,
                        command_id=processed_command.command_id,
                        metadata={
                            "expectedSequence": expected_sequence,
                            "storedSequence": len(existing_events),
                        },
                    )
            existing_record = existing_processed.get(processed_command.command_id)
            if (
                existing_record is not None
                and existing_record.command_hash != processed_command.command_hash
            ):
                raise CommandConflictError(
                    run_id=run_id,
                    command_id=processed_command.command_id,
                )
            appended_events = [item.model_copy(deep=True) for item in events]
            full_events = [item.model_copy(deep=True) for item in existing_events] + appended_events
            aggregate = replay_execution_ledger(full_events)
            if aggregate is None:
                raise LedgerSequenceError(
                    "A committed command must leave a non-empty ledger.",
                    run_id=run_id,
                    command_id=processed_command.command_id,
                )
            if aggregate.snapshot != snapshot:
                raise VersionConflictError(
                    "The supplied snapshot does not match the replayed ledger.",
                    run_id=run_id,
                    command_id=processed_command.command_id,
                )
            next_processed = deepcopy(existing_processed)
            next_processed[processed_command.command_id] = processed_command.model_copy(deep=True)
            self._events[run_id] = full_events
            self._snapshots[run_id] = aggregate.snapshot.model_copy(deep=True)
            self._processed[run_id] = next_processed

    def _replay(self, run_id: RunId):
        with self._lock:
            events = self._events.get(run_id)
            if events is None:
                raise RunNotFoundError(run_id=run_id)
            replay_input = [item.model_copy(deep=True) for item in events]
        aggregate = replay_execution_ledger(replay_input)
        assert aggregate is not None
        return aggregate

    @staticmethod
    def _matches_query(snapshot: AgentRunSnapshot, query: AgentRunQuery) -> bool:
        spec = snapshot.specification
        if query.run_id is not None and spec.run_id != query.run_id:
            return False
        if query.task_id is not None and spec.task_id != query.task_id:
            return False
        if query.agent_id is not None and spec.agent_id != query.agent_id:
            return False
        if query.state is not None and snapshot.state != query.state:
            return False
        if query.terminal is not None and (snapshot.state in TERMINAL_STATES) != query.terminal:
            return False
        if query.correlation_id is not None and spec.correlation_id != query.correlation_id:
            return False
        if query.parent_run_id is not None and spec.parent_run_id != query.parent_run_id:
            return False
        if query.created_from is not None and snapshot.created_at < query.created_from:
            return False
        if query.created_to is not None and snapshot.created_at > query.created_to:
            return False
        return True
