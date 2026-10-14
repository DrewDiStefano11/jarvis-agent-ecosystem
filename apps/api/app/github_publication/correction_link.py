"""Resolve original publication and child native checkpoints, without importing history."""

from app.github_publication.journal import PublicationJournal
from app.github_publication.service import fail
from app.github_publication.source_link import read_source_link
from app.models.correction_publication_link import CorrectionPublicationLink
from app.self_build.commit_completion import checkpoint
from app.self_build.commit_journal import NativeCommitJournal
from app.self_build.policy import digest


def read_correction_link(
    service,
    actor,
    workspace_id,
    native_command_id,
    original_workspace_id,
    original_publication_id,
    expected_parent_sha,
    observation_digest,
):
    original = read_source_link(
        service,
        actor,
        original_workspace_id,
        original_publication_id,
        expected_parent_sha,
        observation_digest,
    )
    published = PublicationJournal(service).read(
        actor, original_workspace_id, original_publication_id
    )
    if workspace_id == original_workspace_id:
        fail("GITHUB_CORRECTION_WORKSPACE_REQUIRED")
    native = NativeCommitJournal(service.native)
    history = native.history(actor, workspace_id, native_command_id)
    record, source = history.current, history.source_snapshot
    proposal = record.plan.preparation.plan.proposal
    with service.native.repository.sessions() as session:
        row = service.native.repository.lookup(session, workspace_id)
        if (
            row.task_id != original.task_id
            or row.actor_id != published.plan.actor_id
            or row.runtime_run_id != proposal.lineage.runtime_run_id
        ):
            fail("GITHUB_CORRECTION_OWNER_CHANGED")
    if (
        record.state != "checkpointed"
        or record.workspace_id != workspace_id
        or proposal.parent_sha != original.commit_sha
        or source.head_sha != original.commit_sha
        or source.original_base_sha != original.commit_sha
        or source.repository_identity != original.original_code_state.repository_identity
        or proposal.lineage.actor_id != published.plan.actor_id
        or proposal.branch == published.plan.branch
        or tuple(item.path for item in proposal.files)
        != tuple(item.path for item in source.changes)
    ):
        fail("GITHUB_CORRECTION_DIRECT_CHILD_REQUIRED")
    actual, identifier, integrity, _ = checkpoint(native, actor, record)
    if actual is None or record.checkpoint_id != identifier:
        fail("GITHUB_CORRECTION_CHECKPOINT_REQUIRED")
    # A newer protected observation cannot be substituted mid-read.
    latest = PublicationJournal(service).read(actor, original_workspace_id, original_publication_id)
    if latest != published or latest.digest != original.publication_digest:
        fail("GITHUB_CORRECTION_OBSERVATION_CHANGED")
    payload = dict(
        schema_version="1.0",
        task_id=original.task_id,
        original_workspace_id=original_workspace_id,
        original_publication_id=original_publication_id,
        original_source_link_hash=original.link_hash,
        workspace_id=workspace_id,
        native_command_id=native_command_id,
        native_operation_id=record.operation_id,
        native_plan_hash=record.plan.plan_hash,
        native_checkpoint_id=identifier,
        native_checkpoint_integrity=integrity,
        source_snapshot_digest=record.plan.snapshot_digest,
        repository_identity=source.repository_identity,
        remote_branch=published.plan.branch,
        local_branch=proposal.branch,
        pr_number=published.pr_number,
        approved_pr_base_sha=published.plan.approved_base_sha,
        source_base_sha=source.original_base_sha,
        expected_remote_sha=original.commit_sha,
        commit_sha=proposal.prospective_commit_sha,
        tree_sha=proposal.prospective_tree_sha,
        historical_only=True,
        publication_authority=False,
        lineage_admission_verified=False,
    )
    return CorrectionPublicationLink(**payload, link_hash=digest(payload))
