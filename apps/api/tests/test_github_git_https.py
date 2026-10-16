"""Credentials and network effects stay inside a continuously fenced host RPC."""

import asyncio
from dataclasses import replace
from hashlib import sha256

import httpx
import pytest
from pydantic import SecretStr

from app.core.errors import DomainError
from app.github_publication.git_https import GitHubPushHTTPS
from app.github_publication.git_protocol import packet, receive_request
from app.github_publication.transport import GitHubRetry
from tests.test_github_git_protocol import TARGET, Object, commit, synthetic_advertisement


class Broker:
    def __init__(self):
        self.calls = 0

    def resolve(self):
        self.calls += 1
        return SecretStr("fake-test-token-" + str(self.calls))


class Fence:
    def __init__(self, expected=None):
        self.allowed, self.expected, self.calls = True, expected, []

    def check(self, operation, digest):
        self.calls.append((operation, digest))
        if not self.allowed or (operation == "branch_push" and digest != self.expected):
            raise DomainError("PUBLICATION_REVOKED", "Publication authority changed.", 403)


def candidate():
    tree = Object("tree", b"")
    child = commit(tree.sha, "a" * 40)
    return receive_request(
        TARGET, "codex/job", "a" * 40, child.sha, synthetic_advertisement(), (tree, child)
    )


def service(handler, *, fence=None, broker=None, publisher_ids=(42,)):
    broker = broker or Broker()
    return GitHubPushHTTPS(
        TARGET,
        broker,
        fence or Fence(),
        publisher_ids=publisher_ids,
        transport=httpx.MockTransport(handler),
    ), broker


def identity(request):
    if request.url.path == "/repos/example/repository":
        return httpx.Response(200, json={"id": 123, "full_name": "example/repository"})
    if request.url.path == "/user":
        return httpx.Response(200, json={"id": 42})
    return None


@pytest.mark.asyncio
async def test_fixed_hosts_scoped_identity_and_fresh_rpc_credentials():
    seen = []
    push = candidate()

    def handler(request):
        seen.append(request)
        assert request.headers["cookie"] == ""
        found = identity(request)
        if found:
            assert request.url.host == "api.github.com"
            return found
        assert request.url.host == "github.com"
        if request.method == "GET":
            assert request.url.path == "/example/repository.git/info/refs"
            assert request.url.params["service"] == "git-receive-pack"
            return httpx.Response(
                200,
                content=synthetic_advertisement(),
                headers={
                    "content-type": "application/x-git-receive-pack-advertisement",
                    "set-cookie": "session=foreign",
                },
            )
        assert request.url.path == "/example/repository.git/git-receive-pack"
        assert request.content == push.body
        assert request.headers["content-type"] == "application/x-git-receive-pack-request"
        return httpx.Response(
            200,
            content=packet(b"unpack ok\n") + packet(b"ok refs/heads/codex/job\n") + b"0000",
            headers={"content-type": "application/x-git-receive-pack-result"},
        )

    fence = Fence(sha256(push.body).hexdigest())
    client, broker = service(handler, fence=fence)
    assert await client.advertise() == synthetic_advertisement()
    await client.push(push)
    assert broker.calls == 2
    git_requests = [request for request in seen if request.url.host == "github.com"]
    assert git_requests[0].headers["authorization"] != git_requests[1].headers["authorization"]
    assert set(operation for operation, _ in fence.calls) == {"git_advertisement", "branch_push"}


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["scope", "extra-command", "bad-pack", "wrong-digest"])
async def test_mutated_push_or_unapproved_digest_refuses_before_credentials(mutation):
    request = candidate()
    fence = Fence(sha256(request.body).hexdigest())
    if mutation == "scope":
        request = replace(request, reference="refs/heads/main")
    elif mutation == "extra-command":
        request = replace(
            request, body=request.body.replace(b"0000PACK", b"0000foreign-commandPACK")
        )
    elif mutation == "bad-pack":
        request = replace(request, body=request.body[:-1] + bytes([request.body[-1] ^ 1]))
    else:
        fence.expected = "0" * 64
    client, broker = service(lambda request: pytest.fail("network should not run"), fence=fence)
    with pytest.raises(DomainError):
        await client.push(request)
    assert broker.calls == 0


@pytest.mark.asyncio
async def test_revocation_between_identity_reads_and_git_effect_prevents_post():
    push, seen = candidate(), []
    fence = Fence(sha256(push.body).hexdigest())

    def handler(request):
        seen.append(request)
        found = identity(request)
        if request.url.path == "/user":
            fence.allowed = False
        return found or pytest.fail("revoked operation reached Git endpoint")

    client, _ = service(handler, fence=fence)
    with pytest.raises(DomainError) as error:
        await client.push(push)
    assert error.value.code == "PUBLICATION_REVOKED"
    assert not any(request.method == "POST" for request in seen)


@pytest.mark.asyncio
async def test_waiting_provider_is_cancelled_when_live_fence_is_revoked():
    cancelled = asyncio.Event()
    fence = Fence()

    async def handler(request):
        try:
            await asyncio.sleep(2)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return identity(request)

    client, _ = service(handler, fence=fence)
    task = asyncio.create_task(client.advertise())
    await asyncio.sleep(0.01)
    fence.allowed = False
    with pytest.raises(DomainError) as error:
        await task
    assert error.value.code == "PUBLICATION_REVOKED"
    assert cancelled.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("foreign", ["repository", "publisher", "publisher-type"])
async def test_authenticated_identity_mismatch_refuses_git_access(foreign):
    def handler(request):
        if request.url.path == "/repos/example/repository":
            return httpx.Response(
                200,
                json={
                    "id": 999 if foreign == "repository" else 123,
                    "full_name": "example/repository",
                },
            )
        if request.url.path == "/user":
            return httpx.Response(200, json={"id": True if foreign == "publisher-type" else 999})
        pytest.fail("foreign identity reached Git endpoint")

    client, _ = service(handler)
    with pytest.raises(DomainError):
        await client.advertise()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,code",
    [
        (401, "GITHUB_AUTHENTICATION_REQUIRED"),
        (403, "GITHUB_ACCESS_DENIED"),
        (302, "GITHUB_RESPONSE_REJECTED"),
    ],
)
async def test_authentication_denial_and_redirect_never_echo_provider_text(status, code):
    client, _ = service(
        lambda request: httpx.Response(
            status,
            json={"message": "fake-secret-provider-diagnostic"},
            headers={"location": "https://foreign.example/"},
        )
    )
    with pytest.raises(DomainError) as error:
        await client.advertise()
    assert error.value.code == code
    assert "fake-secret" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        {"retry-after": "999999"},
        {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "999999999999"},
        {},
    ],
)
async def test_rate_limits_are_bounded_including_headerless_secondary_limits(headers):
    client, _ = service(
        lambda request: httpx.Response(
            403, json={"message": "secondary rate limit"}, headers=headers
        )
    )
    with pytest.raises(GitHubRetry) as error:
        await client.advertise()
    assert 1 <= error.value.retry_after_seconds <= 86400


@pytest.mark.parametrize("ids", [(), (True,), (0,), (42, 42), [42]])
def test_complete_publisher_authority_is_required_before_provider_access(ids):
    with pytest.raises(DomainError):
        service(lambda request: pytest.fail("network should not run"), publisher_ids=ids)


@pytest.mark.asyncio
async def test_http_exception_context_does_not_disclose_authentication_header():
    def handler(request):
        raise httpx.ConnectError("fake-test-token transport failure", request=request)

    client, _ = service(handler)
    with pytest.raises(DomainError) as error:
        await client.advertise()
    assert error.value.code == "GITHUB_TRANSPORT_UNAVAILABLE"
    assert error.value.__suppress_context__
    assert "fake-test-token" not in str(error.value)


@pytest.mark.asyncio
async def test_async_fence_cannot_grant_publication_authority():
    class AsyncFence:
        async def check(self, operation, digest):
            return None

    client, broker = service(
        lambda request: pytest.fail("network should not run"), fence=AsyncFence()
    )
    with pytest.raises(DomainError) as error:
        await client.advertise()
    assert error.value.code == "GITHUB_PUBLICATION_FENCE_REQUIRED"
    assert broker.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("credential", ["token\nforeign", "token foreign", "", "é"])
async def test_invalid_credential_bytes_never_reach_provider(credential):
    class InvalidBroker:
        def resolve(self):
            return SecretStr(credential)

    client, _ = service(
        lambda request: pytest.fail("network should not run"), broker=InvalidBroker()
    )
    with pytest.raises(DomainError) as error:
        await client.advertise()
    assert error.value.code == "GITHUB_CREDENTIAL_UNAVAILABLE"


@pytest.mark.asyncio
@pytest.mark.parametrize("response_kind", ["oversized", "missing-media", "changed-repository"])
async def test_response_cannot_acknowledge_an_unverified_publication(response_kind):
    repository_reads = 0

    def handler(request):
        nonlocal repository_reads
        if request.url.path == "/repos/example/repository":
            repository_reads += 1
            return httpx.Response(
                200,
                json={
                    "id": 999
                    if response_kind == "changed-repository" and repository_reads > 1
                    else 123,
                    "full_name": "example/repository",
                },
            )
        found = identity(request)
        if found:
            return found
        return httpx.Response(
            200,
            content=b"x" * (1024 * 1024 + 1)
            if response_kind == "oversized"
            else synthetic_advertisement(),
            headers={}
            if response_kind == "missing-media"
            else {
                "content-type": "application/x-git-receive-pack-advertisement",
            },
        )

    client, _ = service(handler)
    with pytest.raises(DomainError) as error:
        await client.advertise()
    assert (
        error.value.code
        == {
            "oversized": "GITHUB_RESPONSE_LIMIT",
            "missing-media": "GITHUB_GIT_PROTOCOL_INVALID",
            "changed-repository": "GITHUB_REPOSITORY_IDENTITY_CHANGED",
        }[response_kind]
    )
