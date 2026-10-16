"""Deterministic provider failures verify reconciliation, not real acceptance."""

import json
from copy import deepcopy

import httpx
import pytest

from app.core.errors import DomainError
from app.github_publication.publisher_api import GitHubPublisherAPI
from tests.test_github_git_https import Broker, Fence, identity
from tests.test_github_git_protocol import TARGET

HEAD = "b" * 40
OPERATION = "c" * 64


class Provider:
    def __init__(self):
        self.prs, self.posts, self.patches, self.reads = [], 0, 0, 0
        self.lose_ack, self.change_head = False, False

    def __call__(self, request):
        assert request.url.host == "api.github.com"
        assert request.headers["cookie"] == ""
        found = identity(request)
        if found:
            return found
        prefix = "/repos/example/repository/pulls"
        if request.method == "GET" and request.url.path == prefix:
            assert request.url.params["state"] == "all"
            assert request.url.params["head"] == "example:codex/job"
            assert request.url.params["base"] == "main"
            return httpx.Response(200, json=self.prs)
        if request.method == "GET" and request.url.path == prefix + "/7":
            self.reads += 1
            value = deepcopy(self.prs[0])
            if self.change_head:
                value["head"]["sha"] = "d" * 40
            return httpx.Response(200, json=value)
        if request.method == "POST" and request.url.path == prefix:
            self.posts += 1
            body = json.loads(request.content)
            assert set(body) == {"title", "body", "head", "base", "draft"}
            assert body["draft"] is True
            value = dict(
                number=7,
                state="open",
                draft=True,
                merged_at=None,
                title=body["title"],
                body=body["body"],
                user={"id": 42},
                head={"ref": body["head"], "sha": HEAD, "repo": {"id": 123}},
                base={"ref": body["base"], "repo": {"id": 123}},
                html_url="https://github.com/example/repository/pull/7",
            )
            self.prs = [value]
            if self.lose_ack:
                self.lose_ack = False
                raise httpx.ReadError("secret-bearing provider diagnostic", request=request)
            return httpx.Response(201, json=value)
        if request.method == "PATCH" and request.url.path == prefix + "/7":
            self.patches += 1
            body = json.loads(request.content)
            assert set(body) == {"title", "body"}
            self.prs[0].update(body)
            return httpx.Response(200, json=self.prs[0])
        pytest.fail("unexpected provider path or method")


def client(provider, fence=None):
    return GitHubPublisherAPI(
        TARGET,
        Broker(),
        fence or Fence(),
        publisher_ids=(42,),
        transport=httpx.MockTransport(provider),
    )


async def ensure(api, title="Feature", body="Verified claims supplied by the host"):
    return await api.ensure_draft("codex/job", HEAD, OPERATION, title, body)


@pytest.mark.asyncio
async def test_lost_creation_ack_reconciles_one_existing_draft_and_updates_metadata():
    provider = Provider()
    provider.lose_ack = True
    with pytest.raises(DomainError) as error:
        await ensure(client(provider))
    assert error.value.code == "GITHUB_TRANSPORT_UNAVAILABLE"
    assert "secret-bearing" not in str(error.value)
    # A fresh process/client lists remote state first. It never replays creation.
    value = await ensure(client(provider))
    assert value["number"] == 7 and provider.posts == 1
    assert provider.reads == 1
    await ensure(client(provider), title="Updated verified feature")
    assert provider.posts == 1 and provider.patches == 1


@pytest.mark.asyncio
async def test_changed_head_after_creation_cannot_be_acknowledged():
    provider = Provider()
    provider.change_head = True
    with pytest.raises(DomainError) as error:
        await ensure(client(provider))
    assert error.value.code == "GITHUB_PR_RECONCILIATION_CONFLICT"
    assert provider.posts == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "conflict", ["duplicate", "marker", "author", "base", "repository", "closed", "ready", "url"]
)
async def test_foreign_or_ambiguous_existing_pr_never_triggers_a_write(conflict):
    provider = Provider()
    await ensure(client(provider))
    value = provider.prs[0]
    if conflict == "duplicate":
        provider.prs.append(deepcopy(value))
    elif conflict == "marker":
        value["body"] = "foreign work"
    elif conflict == "author":
        value["user"]["id"] = 999
    elif conflict == "base":
        value["base"]["ref"] = "foreign"
    elif conflict == "repository":
        value["head"]["repo"]["id"] = 999
    elif conflict == "closed":
        value["state"] = "closed"
    elif conflict == "ready":
        value["draft"] = False
    elif conflict == "url":
        value["html_url"] = "https://foreign.example/pull/7"
    with pytest.raises(DomainError) as error:
        await ensure(client(provider), title="Do not overwrite foreign PR")
    assert error.value.code == "GITHUB_PR_RECONCILIATION_CONFLICT"
    assert provider.posts == 1 and provider.patches == 0


@pytest.mark.asyncio
async def test_pr_write_scope_digest_binds_exact_commit():
    provider, fence = Provider(), Fence()
    await ensure(client(provider, fence))
    first = {value for operation, value in fence.calls if operation == "pr_reconcile"}
    fence.calls.clear()
    with pytest.raises(DomainError):
        await client(provider, fence).ensure_draft(
            "codex/job", "e" * 40, OPERATION, "Feature", "Verified claims supplied by the host"
        )
    second = {value for operation, value in fence.calls if operation == "pr_reconcile"}
    assert len(first) == len(second) == 1 and first != second


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [404, 403])
async def test_missing_remote_ref_is_distinct_from_access_denial(status):
    def provider(request):
        found = identity(request)
        if found:
            return found
        assert request.url.path.endswith("/git/ref/heads/codex/job")
        return httpx.Response(status, json={"message": "untrusted error"})

    api = client(provider)
    if status == 404:
        assert await api.branch_head("codex/job") is None
    else:
        with pytest.raises(DomainError) as error:
            await api.branch_head("codex/job")
        assert error.value.code == "GITHUB_ACCESS_DENIED"


@pytest.mark.asyncio
async def test_private_api_cannot_write_arbitrary_paths_or_merge():
    provider = Provider()
    api = client(provider)
    for method, path, body in (
        ("PUT", "/pulls/7/merge", {}),
        ("POST", "/issues", {}),
        ("PATCH", "/pulls/7", {"base": "main"}),
    ):
        with pytest.raises(DomainError) as error:
            await api._guarded(
                "pr_reconcile",
                "a" * 64,
                lambda method=method, path=path, body=body: api._api(method, path, body=body),
            )
        assert error.value.code == "GITHUB_PUBLICATION_EFFECT_SCOPE_INVALID"
    assert provider.posts == provider.patches == 0
