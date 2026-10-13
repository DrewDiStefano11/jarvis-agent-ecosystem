"""Private authenticated native progress, retaining every durable inode claim."""

from typing import Literal

from pydantic import Field, model_validator

from app.models.git_changes import GitChangeObservation
from app.models.git_commit_metadata import CommitMetadataSnapshot
from app.models.git_execution import NativeCommitProgress, NativeCommitRecord
from app.models.self_build import Contract
from app.self_build.policy import digest


class NativeCommitHistory(Contract):
    schema_version: Literal["1.0"] = "1.0"
    original: NativeCommitRecord
    current: NativeCommitRecord
    source_snapshot: GitChangeObservation
    metadata_snapshot: CommitMetadataSnapshot
    progress: tuple[NativeCommitProgress, ...] = Field(max_length=49160)

    @model_validator(mode="after")
    def exact(self):
        from app.self_build.commit_progress import apply_progress

        if (
            self.original.state != "prepared"
            or self.original.intent_checkpoint_id is not None
            or self.original.checkpoint_id is not None
            or self.original.completed_object_count
            or self.original.pending_object_index is not None
            or self.original.index_stage_inode is not None
            or self.current.intent_checkpoint_id is None
            or digest(self.source_snapshot.model_dump(mode="json"))
            != self.original.plan.snapshot_digest
            or self.metadata_snapshot.snapshot_digest
            != self.original.plan.git_metadata_snapshot_digest
            or len(self.progress) > 5 * self.original.plan.object_count + 6
        ):
            raise ValueError("native history requires immutable acknowledged original evidence")
        projected = self.original.model_copy(
            update={"intent_checkpoint_id": self.current.intent_checkpoint_id}
        )
        for ordinal, entry in enumerate(self.progress, 1):
            if entry.ordinal != ordinal:
                raise ValueError("native history progress is incomplete or reordered")
            projected = apply_progress(projected, entry)
        expected = self.current
        if self.current.state == "checkpointed":
            if (
                self.current.checkpoint_id
                != "checkpoint-" + self.current.operation_id + "-complete"
            ):
                raise ValueError("native final history requires the stable complete checkpoint")
            expected = self.current.model_copy(update={"state": "committed", "checkpoint_id": None})
        if projected != expected:
            raise ValueError("native history differs from its complete progress projection")
        if len(self.model_dump_json().encode()) > 16777216:
            raise ValueError("native history exceeds its private evidence bound")
        return self
