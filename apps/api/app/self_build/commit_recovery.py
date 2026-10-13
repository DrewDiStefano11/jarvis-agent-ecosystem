"""Recovery decisions consume measured positions, never optimistic journal labels."""

from pydantic import TypeAdapter

from app.models.git_execution import CommitPhase, CommitRecoveryPosition


def recovery_step(phase, position):
    # The native service authenticates the journal before supplying its phase.
    phase = TypeAdapter(CommitPhase).validate_python(phase)
    position = CommitRecoveryPosition.model_validate(position.model_dump(mode="json"))
    if position.next_step == "blocked":
        return "blocked"
    if position.index == "installing":
        return "index" if phase == "index_prepared" else "blocked"
    if phase in {"committed", "checkpointed"}:
        return "checkpoint" if position.next_step == "checkpoint" else "blocked"
    if phase == "reference_prepared":
        return (
            position.next_step if position.next_step in {"reference", "checkpoint"} else "blocked"
        )
    if phase == "indexed":
        return "prepare_reference" if position.next_step == "reference" else "blocked"
    if phase == "index_prepared":
        if position.next_step == "reference":
            return "acknowledge_index"
        return "index" if position.next_step == "index" else "blocked"
    if phase == "objects_verified":
        return "prepare_index" if position.next_step == "index" else "blocked"
    # Objects are content-addressed and independently verified, not adopted as
    # filesystem cleanup ownership. Index/ref changes require prior durable intent.
    if position.next_step == "index":
        return "acknowledge_objects"
    return "objects" if position.next_step == "objects" else "blocked"
