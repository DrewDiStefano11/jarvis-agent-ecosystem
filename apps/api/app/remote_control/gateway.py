"""Disabled-by-default dedicated remote surface; legacy routes stay inaccessible."""

import asyncio
from threading import Lock
from time import monotonic
from urllib.parse import urlsplit

import httpx
from starlette.responses import JSONResponse

from app.core.config import Settings


def normalize_authority(authority: str) -> tuple[bytes, int] | None:
    """Compare HTTPS authorities using the supported client's URL normalization."""
    if (
        not authority
        or len(authority) > 512
        or any(character.isspace() or character in "/\\?#@" for character in authority)
    ):
        return None
    try:
        url = httpx.URL("https://" + authority)
        if not url.raw_host or url.port == 0:
            return None
        return url.raw_host, 443 if url.port is None else url.port
    except (httpx.InvalidURL, ValueError, UnicodeError):
        return None


class RequestBudget:
    """One bounded process budget, including failed authentication attempts."""

    def __init__(self, per_minute: int, clock=monotonic):
        self.capacity = per_minute
        self.tokens = float(per_minute)
        self.clock = clock
        self.last = clock()
        self.lock = Lock()

    def take(self) -> bool:
        with self.lock:
            now = self.clock()
            self.tokens = min(
                self.capacity, self.tokens + max(0, now - self.last) * self.capacity / 60
            )
            self.last = now
            if self.tokens < 1:
                return False
            self.tokens -= 1
            return True


class RemoteGateway:
    def __init__(self, app, *, settings: Settings):
        self.app = app
        self.enabled = settings.remote_control_enabled
        self.authority = normalize_authority(urlsplit(settings.remote_origin).netloc)
        if self.enabled and self.authority is None:
            raise ValueError("Remote HTTPS authority is invalid.")
        self.budget = RequestBudget(settings.remote_requests_per_minute)

    async def __call__(self, scope, receive, send):
        if not self.enabled or scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        headers = scope.get("headers", [])
        hosts = [
            normalize_authority(value.decode("latin-1")) for key, value in headers if key == b"host"
        ]
        code, message, status = None, None, 403
        if not self.budget.take():
            code, message, status = "REMOTE_RATE_LIMITED", "Remote request budget exhausted.", 429
        elif scope.get("scheme") != "https" or any(
            key == b"forwarded" or key.startswith(b"x-forwarded-") for key, _ in headers
        ):
            code, message = "REMOTE_SECURE_TRANSPORT_REQUIRED", "Direct HTTPS is required."
        elif hosts != [self.authority]:
            code, message = "REMOTE_HOST_REJECTED", "Remote authority does not match configuration."
        elif not scope.get("path", "").startswith("/api/remote/"):
            code, message = (
                "REMOTE_ROUTE_UNAVAILABLE",
                "Only dedicated remote routes are available.",
            )
        elif any(key == b"origin" for key, _ in headers):
            # This first surface is an operator CLI API, with no browser CORS authority.
            code, message = "REMOTE_ORIGIN_REJECTED", "Browser-origin remote access is unavailable."
        if code is not None:
            response = JSONResponse(
                {"error": {"code": code, "message": message, "details": {}}},
                status_code=status,
                headers={
                    "Cache-Control": "no-store",
                    **({"Retry-After": "60"} if status == 429 else {}),
                },
            )
            return await response(scope, receive, send)
        # Bound actual streamed input too; a client cannot evade Content-Length.
        body = bytearray()
        try:
            async with asyncio.timeout(5):
                while True:
                    chunk = await receive()
                    if chunk["type"] == "http.disconnect":
                        return
                    body.extend(chunk.get("body", b""))
                    if len(body) > 65_536:
                        response = JSONResponse(
                            {
                                "error": {
                                    "code": "REMOTE_BODY_TOO_LARGE",
                                    "message": "Remote request body exceeds its bound.",
                                    "details": {},
                                }
                            },
                            status_code=413,
                        )
                        return await response(scope, receive, send)
                    if not chunk.get("more_body", False):
                        break
        except TimeoutError:
            response = JSONResponse(
                {
                    "error": {
                        "code": "REMOTE_BODY_TIMEOUT",
                        "message": "Remote request body timed out.",
                        "details": {},
                    }
                },
                status_code=408,
            )
            return await response(scope, receive, send)
        delivered = False

        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        async def private_send(message):
            if message["type"] == "http.response.start":
                message = dict(message)
                message["headers"] = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key != b"cache-control"
                ] + [(b"cache-control", b"no-store")]
            await send(message)

        await self.app(scope, bounded_receive, private_send)
