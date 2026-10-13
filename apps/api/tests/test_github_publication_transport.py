import httpx
import pytest
from pydantic import SecretStr

from app.core.errors import DomainError
from app.github_publication.credentials import WindowsCredentialBroker
from app.github_publication.transport import GitHubReader, GitHubRetry, RepositoryTarget

TARGET = RepositoryTarget(1, "operator", "repository", "main", "codex/jarvis/")


class Broker:
    def __init__(self):
        self.calls = 0

    def resolve(self):
        self.calls += 1
        return SecretStr("private-credential-" + str(self.calls))


def reader(handler):
    return GitHubReader(TARGET, Broker(), transport=httpx.MockTransport(handler))


def test_fixed_host_identity_and_credentials_rotate_on_every_call():
    seen = []

    def handle(request):
        assert request.url.host == "api.github.com"
        assert request.method == "GET"
        assert request.headers["X-GitHub-Api-Version"] == "2022-11-28"
        seen.append(request.headers["authorization"])
        return httpx.Response(200, json={"id": 1, "full_name": "operator/repository"})

    client = reader(handle)
    try:
        client.repository()
        client.repository()
        assert seen == ["Bearer private-credential-1", "Bearer private-credential-2"]
        assert "private-credential" not in repr(client)
    finally:
        client.close()


@pytest.mark.parametrize(
    "payload",
    [
        {"id": 2, "full_name": "operator/repository"},
        {"id": 1, "full_name": "foreign/repository"},
        [],
    ],
)
def test_repository_transfer_or_identity_drift_refuses(payload):
    client = reader(lambda request: httpx.Response(200, json=payload))
    try:
        with pytest.raises(DomainError, match="identity changed"):
            client.repository()
    finally:
        client.close()


@pytest.mark.parametrize(
    "status,headers,code",
    [
        (401, {}, "GITHUB_AUTHENTICATION_REQUIRED"),
        (403, {}, "GITHUB_ACCESS_DENIED"),
        (404, {}, "GITHUB_RESOURCE_UNAVAILABLE"),
        (302, {"location": "https://attacker.invalid"}, "GITHUB_RESPONSE_REJECTED"),
        (500, {}, "GITHUB_RESPONSE_REJECTED"),
    ],
)
def test_failures_never_include_provider_text_or_credentials(status, headers, code):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            status, headers=headers, text="private-credential or untrusted diagnostic"
        )

    client = reader(handle)
    try:
        with pytest.raises(DomainError) as caught:
            client.repository()
        assert caught.value.code == code
        assert "private-credential" not in str(caught.value)
        assert "untrusted diagnostic" not in str(caught.value)
        assert len(calls) == 1
    finally:
        client.close()


@pytest.mark.parametrize(
    "status,headers,delay",
    [
        (429, {"retry-after": "125"}, 125),
        (403, {"x-ratelimit-remaining": "0"}, 60),
        (403, {"retry-after": "malformed"}, 60),
        (503, {}, 60),
    ],
)
def test_rate_limit_requests_durable_retry_without_busy_replay(status, headers, delay):
    client = reader(lambda request: httpx.Response(status, headers=headers))
    try:
        with pytest.raises(GitHubRetry) as caught:
            client.repository()
        assert caught.value.retry_after_seconds == delay
    finally:
        client.close()


@pytest.mark.parametrize(
    "reset,expected",
    [
        ("999999999999", 86400),
        ("100000000100", 86400),
        ("1120", 120),
        ("900", 60),
    ],
)
def test_reset_derived_retry_is_bounded(reset, expected, monkeypatch):
    monkeypatch.setattr("app.github_publication.transport.time.time", lambda: 1000)
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            403,
            headers={
                "x-ratelimit-remaining": "0",
                "x-ratelimit-reset": reset,
            },
        )

    client = reader(handle)
    try:
        with pytest.raises(GitHubRetry) as caught:
            client.repository()
        assert caught.value.retry_after_seconds == expected
        assert 1 <= caught.value.retry_after_seconds <= 86400
        assert len(calls) == 1
    finally:
        client.close()


def test_headerless_secondary_rate_limit_yields_bounded_retry_without_replay():
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            403, json={"message": "You have exceeded a secondary rate limit. private-credential"}
        )

    client = reader(handle)
    try:
        with pytest.raises(GitHubRetry) as caught:
            client.repository()
        assert caught.value.retry_after_seconds == 60
        assert len(calls) == 1
        assert "private-credential" not in str(caught.value)
    finally:
        client.close()


@pytest.mark.parametrize(
    "payload",
    [
        {"message": "Resource not accessible by integration"},
        {"documentation_url": "secondary rate limit"},
        ["secondary rate limit"],
        {"message": 403},
    ],
)
def test_headerless_permission_denial_never_becomes_a_retry(payload):
    client = reader(lambda request: httpx.Response(403, json=payload))
    try:
        with pytest.raises(DomainError) as caught:
            client.repository()
        assert caught.value.code == "GITHUB_ACCESS_DENIED"
    finally:
        client.close()


def test_headerless_secondary_limit_response_is_size_bounded():
    client = reader(lambda request: httpx.Response(403, content=b"x" * 1048577))
    try:
        with pytest.raises(DomainError) as caught:
            client.repository()
        assert caught.value.code == "GITHUB_RESPONSE_LIMIT"
    finally:
        client.close()


def test_bounded_pagination_does_not_follow_provider_urls():
    calls = []

    def handle(request):
        calls.append(request.url)
        page = int(request.url.params["page"])
        return httpx.Response(
            200,
            json=[{"id": i} for i in range(100 if page == 1 else 2)],
            headers={"link": '<https://attacker.invalid>; rel="next"'},
        )

    client = reader(handle)
    try:
        assert len(client.reviews(12)) == 102
        assert len(calls) == 2
        assert all(url.host == "api.github.com" for url in calls)
    finally:
        client.close()


def test_evidence_overflow_refuses_instead_of_returning_partial_success():
    client = reader(lambda request: httpx.Response(200, json=[{"id": i} for i in range(100)]))
    try:
        with pytest.raises(DomainError) as caught:
            client.reviews(12)
        assert caught.value.code == "GITHUB_EVIDENCE_LIMIT"
    finally:
        client.close()


def test_response_size_and_invalid_json_refuse():
    for content, expected in [
        (b"x" * 1048577, "GITHUB_RESPONSE_LIMIT"),
        (b"not JSON", "GITHUB_RESPONSE_INVALID"),
    ]:
        client = reader(lambda request, content=content: httpx.Response(200, content=content))
        try:
            with pytest.raises(DomainError) as caught:
                client.repository()
            assert caught.value.code == expected
        finally:
            client.close()


def test_transport_errors_strip_credential_bearing_request():
    def handle(request):
        raise httpx.ConnectError("private-credential", request=request)

    client = reader(handle)
    try:
        with pytest.raises(DomainError) as caught:
            client.repository()
        assert caught.value.code == "GITHUB_TRANSPORT_UNAVAILABLE"
        assert "private-credential" not in str(caught.value)
        assert caught.value.__suppress_context__
    finally:
        client.close()


@pytest.mark.parametrize(
    "branch",
    [
        "main",
        "other/topic",
        "codex/jarvis/../main",
        "codex/jarvis/topic.lock",
        "codex/jarvis/.hidden",
        "codex/jarvis/topic//child",
    ],
)
def test_branch_scope_cannot_escape(branch):
    with pytest.raises(DomainError):
        TARGET.branch(branch)


def test_exact_owned_branch_is_accepted():
    assert TARGET.branch("codex/jarvis/task-123") == "codex/jarvis/task-123"


def test_credentials_have_no_arbitrary_file_target():
    with pytest.raises(DomainError):
        WindowsCredentialBroker("C:/candidate/token.txt")
