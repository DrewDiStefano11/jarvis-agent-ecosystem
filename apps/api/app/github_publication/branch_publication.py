"""One exact branch publication, reconciled before every potentially repeated POST."""

from app.core.errors import DomainError
from app.github_publication.git_protocol import advertisement, receive_request


def fail(code):
    raise DomainError(
        code, "Branch publication requires current protected scope and evidence.", 409
    )


async def publish_branch(
    api, git, *, branch, parent_sha, commit_sha, expected_remote_sha, objects, intent
):
    """Intent is a protected journal adapter, never a request/model assertion.

    `require` must verify an acknowledged native publication checkpoint, current
    source/commit/B receipt scope, independent operator approval, lease and stop.
    `record_request` must durably bind the full request digest before a POST.
    No retries, unknown-ref adoption, force options or provider polling occur here.
    """
    if (
        intent is None
        or not callable(getattr(intent, "require", None))
        or not callable(getattr(intent, "record_request", None))
        or not callable(getattr(intent, "require_reconciliation", None))
    ):
        fail("GITHUB_PUBLICATION_INTENT_REQUIRED")
    if expected_remote_sha not in {None, parent_sha}:
        fail("GITHUB_REMOTE_PARENT_SCOPE_INVALID")
    intent.require()
    remote = await api.branch_head(branch)
    intent.require()
    if remote == commit_sha:
        # Caller independently verifies this is the commit bound to its intent;
        # a lost acknowledgement never causes a second POST.
        intent.require_reconciliation()
        return "reconciled"
    if remote != expected_remote_sha:
        fail("GITHUB_REMOTE_REFERENCE_CONFLICT")
    raw = await git.advertise()
    advertised = advertisement(raw)
    if dict(advertised.refs).get("refs/heads/" + branch) != expected_remote_sha:
        fail("GITHUB_REMOTE_REFERENCE_CONFLICT")
    request = receive_request(git.target, branch, parent_sha, commit_sha, raw, objects)
    intent.record_request(request)
    intent.require()
    await git.push(request)
    intent.require()
    if await api.branch_head(branch) != commit_sha:
        fail("GITHUB_REMOTE_ACKNOWLEDGEMENT_INVALID")
    intent.require()
    return "published"
