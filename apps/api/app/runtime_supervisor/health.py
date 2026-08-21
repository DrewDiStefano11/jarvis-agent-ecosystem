from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class HealthResult:
    available: bool
    status: str
    detail: str | None = None
    payload: dict[str, Any] | None = None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: object, **_kwargs: object) -> None:
        return None


def probe_http(url: str, *, expect_json: bool = False, timeout: float = 2) -> HealthResult:
    request = urllib.request.Request(url, headers={"User-Agent": "Jarvis-Supervisor/1"})
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        with opener.open(request, timeout=timeout) as response:
            content = response.read(1_048_576)
            if response.status < 200 or response.status >= 400:
                return HealthResult(False, "failed", f"HTTP {response.status}")
    except (http.client.HTTPException, urllib.error.URLError, TimeoutError, OSError) as exc:
        return HealthResult(False, "unavailable", exc.__class__.__name__)
    if not expect_json:
        return HealthResult(True, "healthy")
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return HealthResult(True, "degraded", "invalid JSON response")
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return HealthResult(True, "degraded", "missing health data")
    application_status = data.get("status")
    return HealthResult(
        True,
        "healthy" if application_status == "healthy" else "degraded",
        payload=data,
    )
