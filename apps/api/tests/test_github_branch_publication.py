"""Deterministic CAS and lost-write acknowledgement; not isolated validation."""

from hashlib import sha256

import pytest

from app.core.errors import DomainError
from app.github_publication.branch_publication import publish_branch
from tests.test_github_git_protocol import TARGET, Object, commit, synthetic_advertisement


class Intent:
    def __init__(self):
        self.request_digest = None
        self.allowed = True

    def require(self):
        if not self.allowed:
            raise DomainError("PUBLICATION_REVOKED", "Revoked.", 403)

    def record_request(self, request):
        self.require()
        self.request_digest = sha256(request.body).hexdigest()

    def require_reconciliation(self):
        self.require()
        if self.request_digest is None:
            raise DomainError("PUBLICATION_PRIOR_WRITE_REQUIRED", "No prior intent.", 409)


class Provider:
    target = TARGET

    def __init__(self, intent, *, remote=None):
        self.intent, self.remote = intent, remote
        self.pushes, self.advertisements = 0, 0
        self.lose_ack, self.race = False, False

    async def branch_head(self, branch):
        assert branch == "codex/job"
        return self.remote

    async def advertise(self):
        self.advertisements += 1
        if self.race:
            self.remote = "f" * 40
        refs = {"refs/heads/main": "a" * 40}
        if self.remote is not None:
            refs["refs/heads/codex/job"] = self.remote
        return synthetic_advertisement(refs=refs)

    async def push(self, request):
        assert self.intent.request_digest == sha256(request.body).hexdigest()
        self.intent.require()
        self.pushes += 1
        self.remote = request.new_sha
        if self.lose_ack:
            self.lose_ack = False
            raise DomainError("GITHUB_TRANSPORT_UNAVAILABLE", "Lost response.", 503)


async def publish(provider, intent):
    tree = Object("tree", b"")
    child = commit(tree.sha, "a" * 40)
    return await publish_branch(
        provider,
        provider,
        branch="codex/job",
        parent_sha="a" * 40,
        commit_sha=child.sha,
        expected_remote_sha=None,
        objects=(tree, child),
        intent=intent,
    )


@pytest.mark.asyncio
async def test_lost_push_ack_reconciles_without_a_second_post():
    intent = Intent()
    provider = Provider(intent)
    provider.lose_ack = True
    with pytest.raises(DomainError):
        await publish(provider, intent)
    assert provider.pushes == 1 and intent.request_digest is not None
    assert await publish(provider, intent) == "reconciled"
    assert provider.pushes == provider.advertisements == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("race", [False, True])
async def test_foreign_or_concurrently_changed_branch_never_pushes(race):
    intent = Intent()
    provider = Provider(intent, remote=None if race else "f" * 40)
    provider.race = race
    with pytest.raises(DomainError) as error:
        await publish(provider, intent)
    assert error.value.code == "GITHUB_REMOTE_REFERENCE_CONFLICT"
    assert provider.pushes == 0 and intent.request_digest is None


@pytest.mark.asyncio
async def test_matching_remote_commit_without_prior_write_intent_is_not_adopted():
    intent = Intent()
    tree = Object("tree", b"")
    provider = Provider(intent, remote=commit(tree.sha, "a" * 40).sha)
    with pytest.raises(DomainError) as error:
        await publish(provider, intent)
    assert error.value.code == "PUBLICATION_PRIOR_WRITE_REQUIRED"
    assert provider.pushes == 0


@pytest.mark.asyncio
async def test_revoked_scope_never_reads_or_pushes():
    intent = Intent()
    intent.allowed = False
    provider = Provider(intent)
    with pytest.raises(DomainError):
        await publish(provider, intent)
    assert provider.pushes == provider.advertisements == 0
