"""Operator-visible state combines fresh native position with dated PR observations."""

from datetime import UTC, datetime

from app.core.errors import DomainError
from app.github_publication.journal import PublicationJournal
from app.models.publication_lifecycle import PublicationStatus


def status(native, publication, actor, workspace_id, native_command_id, operation_id=None):
    from app.self_build.commit_journal import NativeCommitJournal

    history = NativeCommitJournal(native).history(actor, workspace_id, native_command_id)
    proposal = history.current.plan.preparation.plan.proposal
    recovered = native.recovery(actor, workspace_id, native_command_id)
    head = (
        proposal.parent_sha
        if recovered.position.branch == "parent"
        else proposal.prospective_commit_sha
        if recovered.position.branch == "exact_commit"
        else None
    )
    value = dict(
        workspace_id=workspace_id,
        branch=proposal.branch,
        current_commit_sha=head,
        state="committing" if history.current.state != "checkpointed" else "validation_pending",
    )
    if publication is None or operation_id is None:
        return PublicationStatus(**value)
    snapshot = PublicationJournal(publication).read(actor, workspace_id, operation_id)
    if (
        snapshot.plan.native_command_id != native_command_id
        or snapshot.plan.native_plan_hash != history.current.plan.plan_hash
    ):
        raise DomainError(
            "GITHUB_PUBLICATION_NATIVE_IDENTITY_CHANGED",
            "Publication refers to a different native operation.",
            409,
        )
    value.update(
        state=snapshot.state,
        operation_id=operation_id,
        pr_number=snapshot.pr_number,
        pr_url=snapshot.pr_url,
        observation=snapshot.observation,
        error_code=snapshot.error_code,
        retry_not_before=snapshot.retry_not_before,
    )
    if (
        snapshot.state == "blocked"
        and snapshot.error_code is not None
        and snapshot.error_code.startswith(("GITHUB_VALIDATION_", "VALIDATION_"))
    ):
        value["state"] = "validation_pending"
    if snapshot.state == "completed":
        measured = snapshot.observation
        current = (
            head == snapshot.plan.commit_sha
            and measured is not None
            and measured.head_sha == head
            and measured.head_sha_after_collection == head
            and 0 <= (datetime.now(UTC) - measured.observed_at).total_seconds() <= 300
        )
        try:
            publication.validation.require(actor, snapshot.plan)
        except DomainError:
            current = False
            value["state"] = "validation_pending"
        if current:
            value["ready_for_manual_merge"] = True
        elif value["state"] == "completed":
            value["state"] = "review_pending"
    return PublicationStatus(**value)
