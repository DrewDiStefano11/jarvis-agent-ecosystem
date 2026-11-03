"""Direct local browser authority for HTTP and WebSocket entry points."""

import ipaddress

from starlette.responses import JSONResponse

from app.remote_control.gateway import normalize_authority


def _loopback(host):
    try:
        return host == "localhost" or ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
    except (ValueError, AttributeError):
        return False


class LocalBrowserBoundary:
    def __init__(self, app, *, settings):
        self.app = app
        self.origin = settings.web_origin
        self.remote_enabled = settings.remote_control_enabled

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"} or (
            self.remote_enabled and scope.get("jarvis.owned_loopback") is not True
        ):
            # The outer remote gateway owns the direct TLS surface.
            return await self.app(scope, receive, send)
        peer = (scope.get("client") or (None,))[0]
        if peer != "testclient" and not _loopback(peer):
            # Preserve existing local-peer refusals and their error codes.
            return await self.app(scope, receive, send)
        headers = [(key.lower(), value) for key, value in scope.get("headers", [])]
        hosts = [value.decode("latin-1") for key, value in headers if key == b"host"]
        authority = normalize_authority(hosts[0]) if len(hosts) == 1 else None
        hostname = authority[0].decode("ascii") if authority else None
        # Starlette's in-process transport has no TCP peer; this literal cannot
        # be produced by the production listener. No arbitrary test Host bypass.
        test_host = peer == "testclient" and hostname == "testserver"
        code = None
        if not authority or not (test_host or _loopback(hostname)):
            code = "LOCAL_BROWSER_HOST_REJECTED"
        path = scope.get("path", "")
        configuration = path == "/api/model-configuration" or path.startswith("/api/model-configuration/")
        origins = [value.decode("latin-1") for key, value in headers if key == b"origin"]
        sites = [value for key, value in headers if key == b"sec-fetch-site"]
        if code is None and len(origins) > 1:
            code = "LOCAL_BROWSER_ORIGIN_REJECTED"
        if code is None and not configuration and scope.get("method") != "OPTIONS":
            if (
                origins and origins != [self.origin]
                or not origins and any(site not in {b"none", b"same-origin"} for site in sites)
                or len(sites) > 1
            ):
                code = "LOCAL_BROWSER_ORIGIN_REJECTED"
            elif any(
                key == b"forwarded" or key.startswith(b"x-forwarded-") or key == b"x-real-ip"
                for key, _ in headers
            ):
                code = "LOCAL_CONTROL_PLANE_ONLY"
        # Model configuration retains its stricter required Origin, native actor
        # check and existing forwarding/origin error contract.
        if code is None:
            return await self.app(scope, receive, send)
        if scope["type"] == "websocket":
            return await send({"type": "websocket.close", "code": 1008, "reason": "Local browser authority required."})
        response = JSONResponse(
            {"error": {"code": code, "message": "Use the configured local dashboard or a direct loopback client.", "details": {}}},
            status_code=403, headers={"Cache-Control": "no-store"},
        )
        return await response(scope, receive, send)
