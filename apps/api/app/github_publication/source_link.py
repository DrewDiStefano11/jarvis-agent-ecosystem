"""Resolve protected journal references; never import caller source or grant effects."""

from app.development_validation.planner import digest as code_digest
from app.github_publication.journal import PublicationJournal
from app.github_publication.service import fail
from app.models.development_validation import CodeState, FileChange
from app.models.publication_source_link import PublicationSourceLink
from app.self_build.commit_completion import checkpoint
from app.self_build.commit_journal import NativeCommitJournal
from app.self_build.policy import digest


def read_source_link(
    service, actor, workspace_id, operation_id, expected_head_sha, observation_digest
):
    publication = PublicationJournal(service)
    snapshot = publication.read(actor, workspace_id, operation_id)
    plan, observed = snapshot.plan, snapshot.observation
    if (
        observed is None
        or not observed.complete
        or observed.head_sha != expected_head_sha
        or observed.head_sha_after_collection != expected_head_sha
        or expected_head_sha != plan.commit_sha
        or observed.repository_id != plan.repository_id
        or observed.pr_number != snapshot.pr_number
        or digest(observed.model_dump(mode="json")) != observation_digest
    ):
        fail("GITHUB_CORRECTION_OBSERVATION_CHANGED")
    publication_checkpoint, publication_id, _ = publication.checkpoint(actor, snapshot)
    if publication_checkpoint is None:
        fail("GITHUB_CORRECTION_CHECKPOINT_REQUIRED")
    native = NativeCommitJournal(service.native)
    history = native.history(actor, workspace_id, plan.native_command_id)
    record = history.current
    proposal = record.plan.preparation.plan.proposal
    source = history.source_snapshot
    if (
        record.state != "checkpointed"
        or record.plan.plan_hash != plan.native_plan_hash
        or record.plan.snapshot_digest != plan.source_snapshot_digest
        or proposal.prospective_commit_sha != expected_head_sha
        or proposal.parent_sha != plan.parent_sha
        or source.head_sha != proposal.parent_sha
        or source.original_base_sha != plan.validation_base_sha
        or source.repository_identity != plan.repository_identity
        or record.workspace_id != plan.workspace_id
        or proposal.lineage.actor_id != plan.actor_id
        or proposal.lineage.runtime_run_id != plan.runtime_run_id
        or proposal.lineage.attempt_id != plan.attempt_id
        or tuple(item.path for item in proposal.files)
        != tuple(item.path for item in source.changes)
    ):
        fail("GITHUB_CORRECTION_SOURCE_LINK_CHANGED")
    actual, native_id, integrity, _ = checkpoint(native, actor, record)
    if actual is None or record.checkpoint_id != native_id:
        fail("GITHUB_CORRECTION_CHECKPOINT_REQUIRED")
    state = CodeState(
        repository_identity=source.repository_identity,
        head_sha=proposal.parent_sha,
        base_sha=source.original_base_sha,
        changes=tuple(
            FileChange(
                path=item.path,
                before_hash=None if item.before is None else item.before.sha256,
                after_hash=None if item.after is None else item.after.sha256,
                before_mode=None if item.before is None else item.before.mode,
                after_mode=None if item.after is None else item.after.mode,
            )
            for item in source.changes
        ),
    )
    payload = dict(
        schema_version="1.0",
        workspace_id=workspace_id,
        task_id=plan.task_id,
        runtime_run_id=plan.runtime_run_id,
        attempt_id=plan.attempt_id,
        publication_operation_id=operation_id,
        publication_digest=snapshot.digest,
        publication_checkpoint_id=publication_id,
        native_operation_id=record.operation_id,
        native_command_id=plan.native_command_id,
        native_plan_hash=plan.native_plan_hash,
        native_checkpoint_id=native_id,
        native_checkpoint_integrity=integrity,
        source_snapshot_digest=plan.source_snapshot_digest,
        applied_source_proof_hash=record.plan.applied_source.proof_hash,
        mutation_operation_id=record.plan.applied_source.mutation_operation_id,
        mutation_checkpoint_id=record.plan.applied_source.mutation_checkpoint_id,
        source_edit_record_id=record.plan.applied_source.editing_record_id,
        source_edit_plan_hash=record.plan.applied_source.editing_plan_hash,
        parent_sha=proposal.parent_sha,
        commit_sha=expected_head_sha,
        tree_sha=proposal.prospective_tree_sha,
        original_code_state=state.model_dump(mode="json"),
        original_code_state_hash=code_digest(state.model_dump(mode="json")),
        observation=observed.model_dump(mode="json"),
        observation_digest=observation_digest,
        historical_only=True,
        mutation_authority=False,
        validation_authority=False,
    )
    return PublicationSourceLink(**payload, link_hash=digest(payload))
