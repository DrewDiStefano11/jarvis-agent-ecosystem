"""Pure monotonic projection of protected publication audit entries."""

from app.github_publication.readiness import assess
from app.models.publication_lifecycle import PublicationEntry, PublicationSnapshot

ZERO = "0" * 64


def fold(entries):
    snapshot = None
    for ordinal, candidate in enumerate(entries, 1):
        entry = PublicationEntry.model_validate(candidate.model_dump(mode="json"))
        if entry.sequence != ordinal or entry.previous_digest != (
            ZERO if snapshot is None else snapshot.digest
        ):
            raise ValueError("publication history is not contiguous")
        if snapshot is None:
            if entry.kind != "prepared":
                raise ValueError("publication history requires original intent")
            snapshot = PublicationSnapshot(
                operation_id=entry.operation_id,
                plan=entry.plan,
                sequence=1,
                digest=entry.entry_digest,
                state="preparing",
            )
            continue
        if entry.operation_id != snapshot.operation_id or entry.kind == "prepared":
            raise ValueError("publication operation cannot change or restart")
        changes = dict(sequence=entry.sequence, digest=entry.entry_digest)
        if (
            entry.kind == "push_intent"
            and snapshot.push_request_digest is None
            and snapshot.pr_number is None
        ):
            changes.update(state="pushing", push_request_digest=entry.request_digest)
        elif (
            entry.kind == "pushed"
            and snapshot.state == "pushing"
            and snapshot.push_request_digest is not None
        ):
            changes.update(state="validation_pending", branch_published=True)
        elif (
            entry.kind == "pr_intent"
            and snapshot.state == "validation_pending"
            and snapshot.pr_request_digest is None
        ):
            changes.update(pr_request_digest=entry.request_digest)
        elif (
            entry.kind == "pr_created"
            and snapshot.pr_request_digest is not None
            and snapshot.pr_number is None
        ):
            if not entry.pr_url.endswith("/pull/" + str(entry.pr_number)):
                raise ValueError("PR identity differs")
            changes.update(state="pr_created", pr_number=entry.pr_number, pr_url=entry.pr_url)
        elif entry.kind == "observation" and snapshot.pr_number is not None:
            observation = entry.observation
            if (
                observation.repository_id != snapshot.plan.repository_id
                or observation.pr_number != snapshot.pr_number
                or observation.approved_base_sha != snapshot.plan.approved_base_sha
            ):
                raise ValueError("observation belongs to a different publication")
            readiness = assess(observation)
            current = (
                observation.head_sha == snapshot.plan.commit_sha
                and observation.head_sha_after_collection == snapshot.plan.commit_sha
            )
            changes.update(
                observation=observation,
                state="validation_pending"
                if entry.validation_confirmed_digest != snapshot.plan.validation_journal_digest
                else "completed"
                if current and readiness.ready_for_manual_merge
                else "review_pending",
            )
        elif entry.kind == "blocked" and snapshot.state != "completed":
            changes.update(
                state="blocked",
                error_code=entry.error_code,
                retry_not_before=entry.retry_not_before,
            )
        elif entry.kind == "resumed" and snapshot.state == "blocked":
            if (
                snapshot.retry_not_before is not None
                and entry.timestamp < snapshot.retry_not_before
            ):
                raise ValueError("publication retry deadline has not elapsed")
            changes.update(
                error_code=None,
                retry_not_before=None,
                state="review_pending"
                if snapshot.pr_number is not None
                else "validation_pending"
                if snapshot.branch_published
                else "pushing"
                if snapshot.push_request_digest is not None
                else "preparing",
            )
        else:
            raise ValueError("publication transition skips intent or replays effects")
        snapshot = PublicationSnapshot.model_validate(snapshot.model_dump(mode="json") | changes)
    if snapshot is None:
        raise ValueError("publication history is empty")
    return snapshot
