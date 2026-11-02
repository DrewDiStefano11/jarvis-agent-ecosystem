"""Explicit direct-TLS listener configuration owned by the existing API process."""

from __future__ import annotations

import http.client
import ipaddress
import json
import socket
import ssl
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from app.runtime_supervisor.health import HealthResult


def _enabled(values: dict[str, str], name: str) -> bool:
    value = values.get(name, "false").strip().lower()
    if value not in {"true", "1", "yes", "on", "false", "0", "no", "off"}:
        raise ValueError(f"{name} must be true or false")
    return value in {"true", "1", "yes", "on"}


def _file(values: dict[str, str], name: str) -> Path:
    path = Path(values.get(name, ""))
    if not path.is_absolute() or not path.is_file():
        raise ValueError(f"{name} must name an existing absolute file")
    return path.resolve()


@dataclass(frozen=True)
class RemoteListener:
    host: str
    port: int
    origin: str
    certificate: Path
    private_key: Path
    ca_file: Path | None = None

    @classmethod
    def load(cls, values: dict[str, str], *, local_ports: set[int]) -> RemoteListener | None:
        if not _enabled(values, "JARVIS_SUPERVISOR_REMOTE_ENABLED"):
            return None
        if not _enabled(values, "JARVIS_REMOTE_CONTROL_ENABLED"):
            raise ValueError("supervised remote TLS requires explicit remote control activation")
        host = values.get("JARVIS_SUPERVISOR_REMOTE_HOST", "127.0.0.1")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            raise ValueError("remote bind must be an explicit private or loopback IP") from None
        if not (address.is_private or address.is_loopback) or (
            address.is_unspecified
            or address.is_multicast
            or address.is_link_local
            or (address.is_reserved and not address.is_loopback)
        ):
            raise ValueError("remote bind must be an explicit private or loopback IP")
        origin = values.get("JARVIS_REMOTE_ORIGIN", "")
        try:
            parsed = urlsplit(origin)
            port = parsed.port or 443
            requested = int(values.get("JARVIS_SUPERVISOR_REMOTE_PORT", str(port)))
            valid = (
                parsed.scheme == "https"
                and parsed.hostname
                and not parsed.username
                and not parsed.password
                and parsed.path in {"", "/"}
                and not parsed.query
                and not parsed.fragment
                and 1 <= port <= 65535
                and requested == port
                and port not in local_ports
            )
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("remote HTTPS origin and distinct listener port must match")
        cert = _file(values, "JARVIS_SUPERVISOR_REMOTE_CERTIFICATE")
        key = _file(values, "JARVIS_SUPERVISOR_REMOTE_PRIVATE_KEY")
        ca = (
            _file(values, "JARVIS_SUPERVISOR_REMOTE_CA_FILE")
            if values.get("JARVIS_SUPERVISOR_REMOTE_CA_FILE")
            else None
        )
        try:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(cert, key, password=lambda: "")
            ssl.create_default_context(cafile=str(ca) if ca else None)
        except (OSError, ValueError):
            raise ValueError(
                "remote TLS certificate, key or trust configuration is invalid"
            ) from None
        return cls(host, port, origin.rstrip("/"), cert, key, ca)


class ListenerSurface:
    """Listener-created scope authority; request headers never grant local access."""

    def __init__(self, app, *, loopback: bool):
        self.app = app
        self.loopback = loopback

    async def __call__(self, scope, receive, send):
        await self.app({**scope, "jarvis.owned_loopback": self.loopback}, receive, send)


def probe_remote(listener: RemoteListener, *, timeout: float = 2) -> HealthResult:
    """Verify TLS and the authentication fence without transmitting any credential.

    Connect only to the owned bind IP, while verifying the configured origin's
    hostname and certificate. DNS, proxies and redirects cannot change the target.
    """
    parsed = urlsplit(listener.origin)
    connection = None
    try:
        context = ssl.create_default_context(
            cafile=str(listener.ca_file) if listener.ca_file else None
        )
        connection = http.client.HTTPSConnection(
            parsed.hostname, listener.port, timeout=timeout, context=context
        )
        raw_socket = socket.create_connection((listener.host, listener.port), timeout)
        try:
            connection.sock = context.wrap_socket(raw_socket, server_hostname=parsed.hostname)
        except BaseException:
            raw_socket.close()
            raise
        connection.request("GET", "/api/remote/goals?limit=1", headers={"Host": parsed.netloc})
        response = connection.getresponse()
        content = response.read(8193)
        if response.status not in {401, 429} or len(content) > 8192:
            return HealthResult(False, "failed", "remote authentication fence unavailable")
        payload = json.loads(content)
        if (
            response.status == 429
            and isinstance(payload, dict)
            and isinstance(payload.get("error"), dict)
            and payload["error"].get("code") == "REMOTE_RATE_LIMITED"
        ):
            return HealthResult(True, "degraded", "remote request budget exhausted")
        if (
            response.status != 401
            or not isinstance(payload, dict)
            or not isinstance(payload.get("error"), dict)
            or (payload["error"].get("code") != "REMOTE_AUTHENTICATION_REQUIRED")
        ):
            return HealthResult(False, "failed", "remote authentication fence unavailable")
        return HealthResult(True, "healthy")
    except (OSError, ValueError, http.client.HTTPException) as exc:
        return HealthResult(False, "unavailable", exc.__class__.__name__)
    finally:
        if connection is not None:
            connection.close()
