"""Private, guarded GitHub smart-HTTPS boundary; no public publication entry point.

The composed service must hold source pins and bind the push digest to protected
intent, native checkpoint, validation and operator scope. No default grant exists.
"""

import asyncio
import base64
import inspect
import json
import struct
import time
from contextvars import ContextVar
from hashlib import sha1, sha256
from typing import Protocol

import httpx
from pydantic import SecretStr

from app.core.errors import DomainError
from app.github_publication.credentials import CredentialBroker
from app.github_publication.git_protocol import (
    MAX_OBJECTS,
    MAX_PACK_BYTES,
    MAX_RESPONSE_BYTES,
    OID,
    PushRequest,
    advertisement,
    packet,
    receive_result,
)
from app.github_publication.transport import GitHubRetry, RepositoryTarget


class PublicationFence(Protocol):
    def check(self, operation: str, request_digest: str | None) -> None: ...


def fail(code, status=503):
    raise DomainError(
        code, "GitHub HTTPS publication failed a required boundary check.", status
    ) from None


class GitHubPushHTTPS:
    """One bounded RPC, with fresh credentials and continuous live fencing.

    This trusted host primitive has no retry loop, model input, credential export,
    shell, redirect or environment proxy. Ambiguous writes require reconciliation
    against durable intent before any retry by the future publication service.
    A deployment must provision an appropriately repository-restricted broker.
    """

    def __init__(
        self,
        target: RepositoryTarget,
        broker: CredentialBroker,
        fence: PublicationFence,
        *,
        publisher_ids: tuple[int, ...],
        transport=None,
    ):
        if not isinstance(target, RepositoryTarget) or not callable(getattr(fence, "check", None)):
            fail("GITHUB_PUBLICATION_FENCE_REQUIRED", 403)
        if (
            not isinstance(publisher_ids, tuple)
            or not 1 <= len(publisher_ids) <= 256
            or any(type(value) is not int or value <= 0 for value in publisher_ids)
            or len(set(publisher_ids)) != len(publisher_ids)
        ):
            fail("GITHUB_PUBLISHER_AUTHORITY_REQUIRED", 403)
        self.target, self._broker, self._fence = target, broker, fence
        self._publisher_ids = frozenset(publisher_ids)
        self._transport = transport
        self._scope = ContextVar("github_publication_rpc", default=None)

    def _check(self, operation, digest):
        result = self._fence.check(operation, digest)
        if result is not None:
            if inspect.iscoroutine(result):
                result.close()
            fail("GITHUB_PUBLICATION_FENCE_REQUIRED", 403)

    async def _guarded(self, operation, digest, call):
        self._check(operation, digest)
        scope = self._scope.set((operation, digest))

        async def monitor():
            while True:
                self._check(operation, digest)
                await asyncio.sleep(0.2)

        work, watch = asyncio.create_task(call()), asyncio.create_task(monitor())
        try:
            done, _ = await asyncio.wait(
                {work, watch}, timeout=30, return_when=asyncio.FIRST_COMPLETED
            )
            if watch in done:
                await watch
                fail("GITHUB_PUBLICATION_FENCE_REQUIRED", 403)
            if work not in done:
                fail("GITHUB_TRANSPORT_UNAVAILABLE")
            result = await work
            self._check(operation, digest)
            return result
        finally:
            work.cancel()
            watch.cancel()
            await asyncio.gather(work, watch, return_exceptions=True)
            self._scope.reset(scope)

    async def _exchange(self, *, identity=False, publisher=False, body=None, token=None):
        scope = self._scope.get()
        if scope is None:
            fail("GITHUB_PUBLICATION_FENCE_REQUIRED", 403)
        self._check(*scope)
        try:
            token = self._broker.resolve() if token is None else token
            if not isinstance(token, SecretStr):
                raise ValueError
            raw = token.get_secret_value()
            if not 1 <= len(raw) <= 4096 or any(
                not 33 <= ord(character) <= 126 for character in raw
            ):
                raise ValueError
        except Exception:
            fail("GITHUB_CREDENTIAL_UNAVAILABLE")
        if not identity:
            await self._identity(token)
            try:
                principal = json.loads(
                    await self._exchange(identity=True, publisher=True, token=token)
                )
                if (
                    not isinstance(principal, dict)
                    or type(principal.get("id")) is not int
                    or principal["id"] not in self._publisher_ids
                ):
                    raise ValueError
            except (ValueError, UnicodeError, KeyError, TypeError):
                fail("GITHUB_PUBLISHER_IDENTITY_CHANGED", 403)
        repository = f"/{self.target.owner}/{self.target.name}"
        if identity:
            host = "https://api.github.com"
            path, method = "/user" if publisher else "/repos" + repository, "GET"
            authorization, accept = "Bearer " + raw, "application/vnd.github+json"
            params = None
        else:
            host = "https://github.com"
            path = repository + ".git/" + ("info/refs" if body is None else "git-receive-pack")
            method = "GET" if body is None else "POST"
            authorization = "Basic " + base64.b64encode(("x-access-token:" + raw).encode()).decode()
            accept = "application/x-git-receive-pack-" + (
                "advertisement" if body is None else "result"
            )
            params = {"service": "git-receive-pack"} if body is None else None
        headers = {
            "Authorization": authorization,
            "Accept": accept,
            "Cookie": "",
            "Cache-Control": "no-cache",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if body is not None:
            headers["Content-Type"] = "application/x-git-receive-pack-request"
        try:
            async with httpx.AsyncClient(
                base_url=host,
                transport=self._transport,
                timeout=20,
                trust_env=False,
                follow_redirects=False,
            ) as client:
                self._check(*scope)
                async with client.stream(
                    method, path, params=params, content=body, headers=headers
                ) as response:
                    payload = bytearray()
                    async for chunk in response.aiter_bytes():
                        self._check(*scope)
                        if len(payload) + len(chunk) > MAX_RESPONSE_BYTES:
                            fail("GITHUB_RESPONSE_LIMIT", 502)
                        payload.extend(chunk)
                    status = response.status_code
                    if status in {429, 502, 503, 504} or (
                        status == 403
                        and (
                            "retry-after" in response.headers
                            or response.headers.get("x-ratelimit-remaining") == "0"
                        )
                    ):
                        retry = response.headers.get("retry-after", "60")
                        seconds = int(retry) if retry.isdigit() and len(retry) <= 6 else 60
                        reset = response.headers.get("x-ratelimit-reset", "")
                        if (
                            response.headers.get("x-ratelimit-remaining") == "0"
                            and reset.isdigit()
                            and len(reset) <= 12
                        ):
                            seconds = max(seconds, int(reset) - int(time.time()))
                        raise GitHubRetry(min(max(seconds, 1), 86400))
                    if status == 401:
                        fail("GITHUB_AUTHENTICATION_REQUIRED")
                    if status == 403:
                        try:
                            error = json.loads(payload)
                        except (ValueError, UnicodeError):
                            error = None
                        if (
                            isinstance(error, dict)
                            and isinstance(error.get("message"), str)
                            and "secondary rate limit" in error["message"].casefold()
                        ):
                            raise GitHubRetry(60)
                        fail("GITHUB_ACCESS_DENIED", 403)
                    if status == 404:
                        fail("GITHUB_RESOURCE_UNAVAILABLE", 404)
                    if status != 200:
                        fail("GITHUB_RESPONSE_REJECTED", 502)
                    if (
                        not identity
                        and response.headers.get("content-type", "")
                        .split(";", 1)[0]
                        .strip()
                        .lower()
                        != accept
                    ):
                        fail("GITHUB_GIT_PROTOCOL_INVALID", 502)
                    if not identity:
                        await self._identity(token)
                    return bytes(payload)
        except httpx.HTTPError:
            # Request exceptions retain authentication headers: never expose them.
            fail("GITHUB_TRANSPORT_UNAVAILABLE")

    async def _identity(self, token=None):
        try:
            payload = json.loads(await self._exchange(identity=True, token=token))
            if (
                not isinstance(payload, dict)
                or type(payload.get("id")) is not int
                or payload["id"] != self.target.repository_id
                or not isinstance(payload.get("full_name"), str)
                or payload["full_name"].casefold()
                != f"{self.target.owner}/{self.target.name}".casefold()
            ):
                raise ValueError
        except (ValueError, UnicodeError, KeyError, TypeError):
            fail("GITHUB_REPOSITORY_IDENTITY_CHANGED", 409)

    async def advertise(self):
        async def call():
            raw = await self._exchange()
            advertisement(raw)
            return raw

        return await self._guarded("git_advertisement", None, call)

    async def push(self, request: PushRequest):
        if not isinstance(request, PushRequest):
            fail("GITHUB_GIT_COMMIT_SCOPE_INVALID", 403)
        if not isinstance(request.reference, str) or not request.reference.startswith(
            "refs/heads/"
        ):
            fail("GITHUB_BRANCH_SCOPE_DENIED", 403)
        self.target.branch(request.reference[11:])
        if (
            any(
                not isinstance(value, str) or not OID.fullmatch(value)
                for value in (request.old_sha, request.new_sha)
            )
            or request.new_sha == "0" * 40
            or request.new_sha == request.old_sha
        ):
            fail("GITHUB_GIT_COMMIT_SCOPE_INVALID", 403)
        prefix = (
            packet(
                f"{request.old_sha} {request.new_sha} {request.reference}".encode()
                + b"\0report-status\n"
            )
            + b"0000"
        )
        if (
            type(request.body) is not bytes
            or not request.body.startswith(prefix)
            or not len(prefix) + 32 <= len(request.body) <= len(prefix) + MAX_PACK_BYTES
        ):
            fail("GITHUB_GIT_COMMIT_SCOPE_INVALID", 403)
        pack = request.body[len(prefix) :]
        if (
            pack[:4] != b"PACK"
            or struct.unpack("!I", pack[4:8])[0] != 2
            or not 1 <= struct.unpack("!I", pack[8:12])[0] <= MAX_OBJECTS
            or sha1(pack[:-20], usedforsecurity=False).digest() != pack[-20:]
        ):
            fail("GITHUB_GIT_COMMIT_SCOPE_INVALID", 403)
        digest = sha256(request.body).hexdigest()

        async def call():
            result = await self._exchange(body=request.body)
            receive_result(result, request.reference)

        return await self._guarded("branch_push", digest, call)
