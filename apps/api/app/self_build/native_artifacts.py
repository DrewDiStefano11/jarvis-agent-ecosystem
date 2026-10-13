"""Derive exact native backup allowlists from protected completed mutation history."""

from dataclasses import dataclass

from app.core.errors import DomainError
from app.models.git_changes import NativeArtifact
from app.self_build.policy import digest
from app.source_mutation.driver import expected_observation, staging_names
from app.source_mutation.history import MutationHistoryReader
from app.source_mutation.service import intent_digest


@dataclass(frozen=True)
class ArtifactManifest:
    artifacts: tuple[NativeArtifact, ...]
    provenance_digest: str | None

    @property
    def fingerprint(self):
        return digest(
            [self.provenance_digest, [item.model_dump(mode="json") for item in self.artifacts]]
        )


def mutation_artifacts(app, actor, source):
    try:
        history = MutationHistoryReader(app).read(actor, source.record_id)
    except DomainError as error:
        if error.code == "SOURCE_MUTATION_INTENT_REQUIRED":
            return ArtifactManifest((), None)
        raise
    record = history.record
    changes = source.plan.source_plan.changes
    expected = expected_observation(source.plan.observation, changes, len(changes))
    if (
        record.state != "complete"
        or not history.native_checkpoint_present
        or not history.journal_checkpoint_acknowledged
        or not record.checkpoint_id
        or record.plan.editing_plan_hash != source.plan.plan_hash
        or record.completed_count != len(changes)
        or record.pending_index is not None
        or record.staging_inode is not None
        or record.final_observation_digest != digest(expected.model_dump(mode="json"))
    ):
        raise DomainError(
            "GIT_COMMIT_APPLIED_SOURCE_REQUIRED",
            "Complete acknowledged source history is required.",
            409,
        )
    originals = {item.change_index: item for item in record.original_identities}
    required = {index for index, change in enumerate(changes) if change.before is not None}
    if originals.keys() != required:
        raise DomainError(
            "GIT_COMMIT_APPLIED_SOURCE_REQUIRED",
            "Checkpointed original identities are required for retained backups.",
            409,
        )
    provenance = intent_digest(record)
    result = []
    for index, change in enumerate(changes):
        if change.before is None:
            continue
        backup, _ = staging_names(record.operation_id, index)
        parent = change.path.rpartition("/")[0]
        path = parent + "/" + backup if parent else backup
        result.append(
            NativeArtifact(
                path=path,
                kind="source_backup",
                sha256=change.before.digest,
                byte_count=change.before.byte_count,
                provenance_digest=provenance,
                device=originals[index].device,
                inode=originals[index].inode,
            )
        )
    return ArtifactManifest(tuple(sorted(result, key=lambda value: value.path)), provenance)
