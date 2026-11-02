"""Short-lived browser transport credentials. Native domain authority is unchanged."""

import re
from dataclasses import dataclass
from hashlib import sha256
from hmac import compare_digest
from http.cookies import SimpleCookie
from pathlib import Path
from secrets import token_urlsafe
from threading import Lock
from time import monotonic

import httpx
from fastapi import APIRouter, Request, Response
from starlette.responses import FileResponse

from app.core.errors import DomainError
from app.models.domain import TypedApiResponse
from app.models.remote_control import RemoteBrowserSession

COOKIE = "__Host-jarvis-session"
MAX_AGE = 900
IDLE = 300
MAX_SESSIONS = 32
HASHED_ASSET = re.compile(r"[A-Za-z0-9_-]{1,100}-[A-Za-z0-9_-]{6,32}\.(js|css)\Z")
router = APIRouter(tags=["authenticated remote browser"])


def cookie_token(request):
    values = request.headers.getlist("cookie")
    raw = "; ".join(values)
    if len(raw) > 8192 or len(re.findall(r"(?:^|;)\s*" + COOKIE + r"\s*=", raw)) > 1:
        raise DomainError("REMOTE_AUTHENTICATION_REQUIRED", "One session credential required.", 401)
    cookie = SimpleCookie()
    try:
        cookie.load(raw)
        return cookie[COOKIE].value if COOKIE in cookie else None
    except Exception:
        raise DomainError(
            "REMOTE_AUTHENTICATION_REQUIRED", "Invalid session credential.", 401
        ) from None


@dataclass
class Session:
    csrf: str
    expires: float
    last_seen: float


class BrowserSessions:
    def __init__(self, access, *, clock=monotonic):
        self.access, self.clock = access, clock
        self._items = {}
        self._lock = Lock()

    def _prune(self, now):
        self._items = {
            key: value
            for key, value in self._items.items()
            if now < value.expires and now - value.last_seen < IDLE
        }

    def create(self, actor):
        self.access.authorize(actor, "read")
        now = self.clock()
        with self._lock:
            self._prune(now)
            if len(self._items) >= MAX_SESSIONS:
                raise DomainError("REMOTE_SESSION_LIMIT", "Session capacity reached.", 429)
            token, csrf = token_urlsafe(32), token_urlsafe(32)
            self._items[sha256(token.encode()).digest()] = Session(csrf, now + MAX_AGE, now)
        return token, RemoteBrowserSession(
            actorId=actor.actor_id, csrfToken=csrf, expiresInSeconds=MAX_AGE
        )

    def authenticate(self, token, csrf=None, *, restore=False):
        if not isinstance(token, str) or re.fullmatch(r"[A-Za-z0-9_-]{43}", token) is None:
            raise DomainError("REMOTE_AUTHENTICATION_REQUIRED", "Browser sign-in required.", 401)
        # Authenticate current native lifecycle before returning any session principal.
        actor = self.access.authorizer.authenticate(self.access.actor_id)
        now = self.clock()
        with self._lock:
            self._prune(now)
            item = self._items.get(sha256(token.encode()).digest())
            if item is None:
                raise DomainError(
                    "REMOTE_AUTHENTICATION_REQUIRED", "Browser sign-in required.", 401
                )
            if not restore and (
                not isinstance(csrf, str)
                or re.fullmatch(r"[A-Za-z0-9_-]{43}", csrf) is None
                or not compare_digest(item.csrf, csrf)
            ):
                raise DomainError("REMOTE_CSRF_REQUIRED", "Session CSRF header required.", 403)
            item.last_seen = now
            summary = RemoteBrowserSession(
                actorId=actor.actor_id,
                csrfToken=item.csrf,
                expiresInSeconds=max(1, int(item.expires - now)),
            )
        return actor, summary

    def revoke(self, token):
        with self._lock:
            self._items.pop(sha256(token.encode()).digest(), None)


def sessions(request):
    if not request.app.state.settings.remote_browser_enabled:
        raise DomainError("REMOTE_ROUTE_UNAVAILABLE", "Browser access is disabled.", 404)
    return request.app.state.remote_browser_sessions


def same_origin(request):
    # The gateway performs host/TLS/duplicate-origin validation first. These
    # entry routes additionally require Origin, including cookie restoration.
    if request.headers.getlist("origin") != [
        str(httpx.URL(request.app.state.settings.remote_origin)).rstrip("/")
    ]:
        raise DomainError("REMOTE_ORIGIN_REJECTED", "Exact browser origin required.", 403)


@router.post("/api/remote/session", response_model=TypedApiResponse[RemoteBrowserSession])
def sign_in(request: Request, response: Response):
    from app.remote_control.router import bearer_actor

    same_origin(request)
    token, summary = sessions(request).create(bearer_actor(request))
    response.set_cookie(
        COOKIE, token, max_age=MAX_AGE, path="/", secure=True, httponly=True, samesite="strict"
    )
    return TypedApiResponse(data=summary)


@router.post("/api/remote/session/restore", response_model=TypedApiResponse[RemoteBrowserSession])
def restore(request: Request):
    same_origin(request)
    if request.headers.getlist("x-jarvis-session") != ["restore"]:
        raise DomainError("REMOTE_CSRF_REQUIRED", "Session restoration header required.", 403)
    if request.headers.getlist("authorization"):
        raise DomainError("REMOTE_AUTHENTICATION_REQUIRED", "Ambiguous credentials.", 401)
    principal, summary = sessions(request).authenticate(cookie_token(request), restore=True)
    sessions(request).access.authorize(principal, "read")
    return TypedApiResponse(data=summary)


@router.post("/api/remote/session/logout", response_model=TypedApiResponse[bool])
def sign_out(request: Request, response: Response):
    from app.remote_control.router import actor

    actor(request)
    token = cookie_token(request)
    if not token:
        raise DomainError("REMOTE_AUTHENTICATION_REQUIRED", "Browser sign-in required.", 401)
    sessions(request).revoke(token)
    response.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="strict")
    return TypedApiResponse(data=True)


def trusted_file(request, relative):
    sessions(request)
    try:
        root = Path(request.app.state.settings.remote_browser_assets).resolve(strict=True)
        candidate = root / relative
        resolved = candidate.resolve(strict=True)
        if candidate.is_symlink() or not resolved.is_relative_to(root) or not resolved.is_file():
            raise OSError()
    except OSError:
        raise DomainError("REMOTE_ASSET_NOT_FOUND", "Built asset unavailable.", 404) from None
    return FileResponse(resolved)


@router.get("/remote", include_in_schema=False)
def browser_page(request: Request):
    return trusted_file(request, "remote.html")


@router.get("/assets/{filename}", include_in_schema=False)
def browser_asset(filename: str, request: Request):
    if HASHED_ASSET.fullmatch(filename) is None:
        raise DomainError("REMOTE_ASSET_NOT_FOUND", "Built asset unavailable.", 404)
    return trusted_file(request, Path("assets") / filename)
