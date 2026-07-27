from __future__ import annotations

from typing import Any


class AgentRuntimeError(Exception):
    """Base typed error for the isolated agent-runtime domain."""

    code = "agent_runtime_error"
    default_message = "Agent runtime error."

    def __init__(
        self,
        message: str | None = None,
        *,
        run_id: str | None = None,
        attempt_id: str | None = None,
        command_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.default_message
        self.run_id = run_id
        self.attempt_id = attempt_id
        self.command_id = command_id
        self.metadata = dict(metadata or {})
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "runId": self.run_id,
            "attemptId": self.attempt_id,
            "commandId": self.command_id,
            "metadata": dict(self.metadata),
        }


class RunNotFoundError(AgentRuntimeError):
    code = "run_not_found"
    default_message = "The requested run does not exist."


class RunAlreadyExistsError(AgentRuntimeError):
    code = "run_already_exists"
    default_message = "The run already exists."


class InvalidTransitionError(AgentRuntimeError):
    code = "invalid_transition"
    default_message = "The requested runtime transition is not allowed."


class TerminalRunImmutableError(AgentRuntimeError):
    code = "terminal_run_immutable"
    default_message = "Terminal runs are immutable."


class VersionConflictError(AgentRuntimeError):
    code = "version_conflict"
    default_message = "The expected run version does not match the stored version."


class CommandConflictError(AgentRuntimeError):
    code = "command_conflict"
    default_message = "The command ID was reused with different contents."


class AttemptNotFoundError(AgentRuntimeError):
    code = "attempt_not_found"
    default_message = "The requested attempt does not exist."


class ActiveAttemptExistsError(AgentRuntimeError):
    code = "active_attempt_exists"
    default_message = "An active attempt already exists for this run."


class AttemptLimitExceededError(AgentRuntimeError):
    code = "attempt_limit_exceeded"
    default_message = "The run exceeded its maximum permitted attempts."


class InvalidAttemptStateError(AgentRuntimeError):
    code = "invalid_attempt_state"
    default_message = "The attempt is not in a valid state for this operation."


class CheckpointNotAllowedError(AgentRuntimeError):
    code = "checkpoint_not_allowed"
    default_message = "A checkpoint cannot be recorded in the current run state."


class CheckpointSequenceConflictError(AgentRuntimeError):
    code = "checkpoint_sequence_conflict"
    default_message = "The checkpoint sequence is not valid for this run."


class CheckpointLineageError(AgentRuntimeError):
    code = "checkpoint_lineage_error"
    default_message = "The checkpoint lineage is inconsistent with the run history."


class LedgerSequenceError(AgentRuntimeError):
    code = "ledger_sequence_error"
    default_message = "The execution ledger sequence is invalid."


class LedgerReplayError(AgentRuntimeError):
    code = "ledger_replay_error"
    default_message = "The execution ledger cannot be replayed deterministically."


class RecoveryNotAllowedError(AgentRuntimeError):
    code = "recovery_not_allowed"
    default_message = "Recovery is not allowed for this run."


class InvalidRuntimeMetadataError(AgentRuntimeError):
    code = "invalid_runtime_metadata"
    default_message = "Runtime metadata is invalid or unsafe."


class InvalidRuntimeIdentifierError(AgentRuntimeError):
    code = "invalid_runtime_identifier"
    default_message = "Runtime identifiers are invalid."
