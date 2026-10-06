"""Internal read-only transport. Not registered as a runtime or model tool."""

import asyncio
import hashlib
import socket
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpcore

from app.core.errors import DomainError
from app.models.research_retrieval import RedirectRecord, RetrievalLimits
from app.research.url_policy import normalize_url, redirect_destination, validate_dns_answers


def fail(code: str) -> None:
    raise DomainError(code, "Research retrieval failed within configured bounds.", 422) from None


async def resolve(hostname: str, port: int) -> tuple[str, ...]:
    answers = await asyncio.get_running_loop().getaddrinfo(
        hostname, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP
    )
    return tuple(answer[4][0] for answer in answers)


class BoundedStream(httpcore.AsyncNetworkStream):
    def __init__(self, stream, limits):
        self.stream = stream
        self.limits = limits
        self.header = bytearray()
        self.header_done = False
        self.wire_bytes = 0

    async def read(self, max_bytes, timeout=None):
        data = await self.stream.read(min(max_bytes, 8192), timeout)
        self.wire_bytes += len(data)
        if (
            self.wire_bytes
            > self.limits.maximumResponseBytes + self.limits.maximumHeaderBytes + 4096
        ):
            fail("RESEARCH_RESPONSE_TOO_LARGE")
        if not self.header_done:
            self.header.extend(data)
            end = self.header.find(b"\r\n\r\n")
            size = len(self.header) if end < 0 else end + 4
            if size > self.limits.maximumHeaderBytes:
                fail("RESEARCH_HEADERS_TOO_LARGE")
            if end >= 0:
                # No Expect is sent; reject unsolicited interim/upgrade responses
                # rather than letting them bypass the cumulative header budget.
                line = bytes(self.header).split(b"\r\n", 1)[0].split(b" ")
                if len(line) < 2 or len(line[1]) != 3 or line[1].startswith(b"1"):
                    fail("RESEARCH_RESPONSE_INVALID")
                self.header.clear()
                self.header_done = True
        return data

    async def write(self, buffer, timeout=None):
        await self.stream.write(buffer, timeout)

    async def aclose(self):
        await self.stream.aclose()

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        try:
            self.stream = await self.stream.start_tls(ssl_context, server_hostname, timeout)
        except BaseException:
            # The HTTP connection does not yet own this stream during handshake.
            # Close it even when cancellation interrupts TLS initialization.
            await self.stream.aclose()
            raise
        return self

    def get_extra_info(self, info):
        return self.stream.get_extra_info(info)


class PinnedBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, destination, limits, resolver=resolve, connector=None):
        self.destination = destination
        self.limits = limits
        self.resolver = resolver
        self.connector = connector or httpcore.AnyIOBackend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        if (
            host != self.destination.hostname
            or port != self.destination.port
            or local_address is not None
        ):
            fail("RESEARCH_ADDRESS_DENIED")
        async with asyncio.timeout(self.limits.dnsTimeoutSeconds):
            answers = await self.resolver(host, port)
        if len(answers) > self.limits.maximumDnsAnswers:
            fail("RESEARCH_ADDRESS_DENIED")
        addresses = validate_dns_answers(self.destination, answers)
        # No second hostname lookup; the connector receives a canonical numeric IP.
        # No retries/fallback: ambiguous connection failures remain failures.
        stream = await self.connector.connect_tcp(
            addresses[0],
            port,
            timeout=min(
                timeout or self.limits.connectTimeoutSeconds, self.limits.connectTimeoutSeconds
            ),
            socket_options=socket_options,
        )
        try:
            peer = stream.get_extra_info("server_addr")
            if not peer or peer[0] != addresses[0] or peer[1] != port:
                fail("RESEARCH_ADDRESS_DENIED")
            return BoundedStream(stream, self.limits)
        except BaseException:
            await stream.aclose()
            raise

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        fail("RESEARCH_ADDRESS_DENIED")

    async def sleep(self, seconds):
        await asyncio.sleep(seconds)


@dataclass(frozen=True)
class TransportText:
    source_url: str = field(repr=False)
    final_url: str = field(repr=False)
    retrieved_at: datetime
    media_type: str
    encoding: str
    body: bytes = field(repr=False)
    content_digest: str
    redirects: tuple[RedirectRecord, ...] = field(repr=False)


class ReadOnlyTransport:
    """Operator-only internal API. Admission/persistence is a later native service.

    Enabled and allowed_origins must come from trusted operator configuration.
    They are not fields in RetrievalRequest or in model-generated plans.
    """

    def __init__(
        self, *, enabled=False, allowed_origins=(), limits=None, resolver=resolve, connector=None
    ):
        self.enabled = enabled
        self.allowed_origins = frozenset(allowed_origins)
        self.limits = limits or RetrievalLimits()
        self._slots = asyncio.Semaphore(self.limits.maximumConnections)
        self.resolver: Callable[[str, int], Awaitable[tuple[str, ...]]] = resolver
        self.connector = connector

    async def retrieve(self, source_url: str) -> TransportText:
        if self.enabled is not True:
            fail("RESEARCH_AUTHORITY_DENIED")
        try:
            async with asyncio.timeout(self.limits.totalTimeoutSeconds):
                async with self._slots:
                    return await self._retrieve(source_url)
        except DomainError:
            raise
        except TimeoutError:
            fail("RESEARCH_TIMEOUT")
        except httpcore.TimeoutException:
            fail("RESEARCH_TIMEOUT")
        except httpcore.ProtocolError:
            fail("RESEARCH_RESPONSE_INVALID")
        except (httpcore.NetworkError, OSError):
            fail("RESEARCH_NETWORK_UNAVAILABLE")

    async def _retrieve(self, source_url):
        destination = normalize_url(source_url)
        redirects = []
        seen = {destination.url}
        while True:
            origin = destination.url.split("/", 3)[:3]
            if "/".join(origin) not in self.allowed_origins:
                fail("RESEARCH_AUTHORITY_DENIED")
            backend = PinnedBackend(destination, self.limits, self.resolver, self.connector)
            async with httpcore.AsyncConnectionPool(
                network_backend=backend,
                max_connections=1,
                max_keepalive_connections=0,
                http2=False,
                retries=0,
            ) as pool:
                # httpcore has no environment proxy, cookie jar or automatic
                # auth/redirect/decompression behavior. Only fixed headers are sent.
                async with pool.stream(
                    "GET",
                    destination.url,
                    headers={
                        "User-Agent": "Jarvis-Research/1",
                        "Accept-Encoding": "identity",
                        "Connection": "close",
                    },
                    extensions={
                        "timeout": {
                            "connect": self.limits.connectTimeoutSeconds,
                            "read": self.limits.totalTimeoutSeconds,
                            "write": self.limits.connectTimeoutSeconds,
                            "pool": self.limits.connectTimeoutSeconds,
                        }
                    },
                ) as response:
                    headers = {}
                    for key, value in response.headers:
                        key = key.lower()
                        if key in {
                            b"location",
                            b"content-type",
                            b"content-encoding",
                            b"content-length",
                        }:
                            if key in headers:
                                fail("RESEARCH_RESPONSE_INVALID")
                            headers[key] = value
                    if response.status in {301, 302, 303, 307, 308}:
                        try:
                            location = headers[b"location"].decode("ascii")
                        except (KeyError, UnicodeError):
                            fail("RESEARCH_RESPONSE_INVALID")
                        target = redirect_destination(
                            destination,
                            location,
                            hops=len(redirects),
                            maximum_hops=self.limits.maximumRedirects,
                        )
                        if target.url in seen:
                            fail("RESEARCH_REDIRECT_DENIED")
                        seen.add(target.url)
                        redirects.append(
                            RedirectRecord(
                                fromUrl=destination.url,
                                toUrl=target.url,
                                statusCode=response.status,
                            )
                        )
                        destination = target
                        continue
                    if response.status != 200:
                        fail("RESEARCH_RESPONSE_INVALID")
                    if headers.get(b"content-encoding", b"identity").lower() != b"identity":
                        fail("RESEARCH_CONTENT_UNSUPPORTED")
                    try:
                        content_type = headers[b"content-type"].decode("ascii").lower()
                    except (KeyError, UnicodeError):
                        fail("RESEARCH_CONTENT_UNSUPPORTED")
                    parts = [part.strip() for part in content_type.split(";")]
                    media_type = parts[0]
                    if media_type not in {"text/plain", "text/html", "application/xhtml+xml"}:
                        fail("RESEARCH_CONTENT_UNSUPPORTED")
                    if len(parts) > 2:
                        fail("RESEARCH_CONTENT_UNSUPPORTED")
                    encoding = "utf-8"
                    for parameter in parts[1:]:
                        if parameter not in {"charset=utf-8", "charset=us-ascii"}:
                            fail("RESEARCH_CONTENT_UNSUPPORTED")
                        encoding = "ascii" if parameter == "charset=us-ascii" else "utf-8"
                    if b"content-length" in headers:
                        value = headers[b"content-length"]
                        if not value.isdigit() or len(value) > 10:
                            fail("RESEARCH_RESPONSE_INVALID")
                        if int(value) > self.limits.maximumResponseBytes:
                            fail("RESEARCH_RESPONSE_TOO_LARGE")
                    body = bytearray()
                    async for chunk in response.aiter_stream():
                        if len(body) + len(chunk) > self.limits.maximumResponseBytes:
                            fail("RESEARCH_RESPONSE_TOO_LARGE")
                        body.extend(chunk)
                    try:
                        decoded = body.decode(encoding, errors="strict")
                    except UnicodeError:
                        fail("RESEARCH_CONTENT_UNSUPPORTED")
                    if any(
                        unicodedata.category(char) == "Cc" and char not in "\t\n\r"
                        for char in decoded
                    ):
                        fail("RESEARCH_CONTENT_UNSUPPORTED")
                    return TransportText(
                        source_url,
                        destination.url,
                        datetime.now(UTC),
                        media_type,
                        encoding,
                        bytes(body),
                        hashlib.sha256(body).hexdigest(),
                        tuple(redirects),
                    )
