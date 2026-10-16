"""Public-client OAuth with PKCE, verified OIDC identity and protected storage."""

from __future__ import annotations

import base64
import ctypes
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import httpx
import jwt
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.model_providers.errors import AuthenticationError

ISSUER = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"
TOKEN_URL = ISSUER + "/api/accounts/oauth/token"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"


class ChatGPTSession(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, allow_inf_nan=False)
    subject: str
    client_id: str
    host_id: str
    scopes: frozenset[str]
    expires_at: float = Field(gt=0)
    access_token: SecretStr = Field(repr=False)
    refresh_token: SecretStr = Field(repr=False)
    id_token: SecretStr = Field(repr=False)

    def require_plan_usage(self, account: str | None = None) -> None:
        if (
            not {"resource.invoke", "chatgpt.tokens.use.direct"} <= self.scopes
            or self.expires_at <= time.time()
            or self.client_id == "dynamic_agent_client"
            or self.access_token.get_secret_value().startswith("sk-")
            or not self.access_token.get_secret_value()
            or (account is not None and self.subject != account)
        ):
            raise AuthenticationError("current verified ChatGPT plan session required")


@dataclass(repr=False)
class PendingSignIn:
    host_id: str
    redirect_uri: str
    client_id: str = "dynamic_agent_client"
    expected_subject: str | None = None
    state: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    nonce: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    verifier: str = field(default_factory=lambda: secrets.token_urlsafe(64))
    created_at: float = field(default_factory=time.time)
    consumed: bool = False

    def authorization_url(self) -> str:
        callback = urlsplit(self.redirect_uri)
        if (
            callback.scheme != "http"
            or callback.hostname != "127.0.0.1"
            or callback.path != "/auth/callback"
            or callback.query
            or callback.fragment
            or callback.username
            or callback.password
            or callback.port is None
        ):
            raise AuthenticationError("OAuth requires a bound 127.0.0.1 callback")
        challenge = (
            base64.urlsafe_b64encode(sha256(self.verifier.encode()).digest()).rstrip(b"=").decode()
        )
        values = dict(
            client_id=self.client_id,
            ext_agent_host_id=self.host_id,
            response_type="code",
            redirect_uri=self.redirect_uri,
            scope=SCOPES,
            resource=RESOURCE,
            state=self.state,
            nonce=self.nonce,
            code_challenge_method="S256",
            code_challenge=challenge,
        )
        if self.client_id == "dynamic_agent_client":
            values["agent_name_hint"] = "Jarvis"
        return ISSUER + "/api/accounts/authorize?" + urlencode(values)

    async def complete(self, callback: dict[str, str], client: httpx.AsyncClient) -> ChatGPTSession:
        if (
            self.consumed
            or time.time() - self.created_at > 600
            or not secrets.compare_digest(callback.get("state", ""), self.state)
        ):
            raise AuthenticationError("invalid or expired OAuth attempt")
        self.consumed = True
        if callback.get("error") or not callback.get("code"):
            raise AuthenticationError("ChatGPT authorization was not granted")
        issued = callback.get("client_id", self.client_id)
        if (
            issued == "dynamic_agent_client"
            or not issued
            or (self.client_id != "dynamic_agent_client" and issued != self.client_id)
        ):
            raise AuthenticationError("OAuth client registration mismatch")
        failure = None
        try:
            result = await client.post(
                TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "client_id": issued,
                    "code": callback["code"],
                    "code_verifier": self.verifier,
                    "redirect_uri": self.redirect_uri,
                    "resource": RESOURCE,
                },
            )
            result.raise_for_status()
            tokens = result.json()
            discovery = await client.get(ISSUER + "/.well-known/openid-configuration")
            discovery.raise_for_status()
            jwks_uri = discovery.json()["jwks_uri"]
            parsed = urlsplit(jwks_uri)
            if (
                parsed.scheme != "https"
                or parsed.hostname != "auth.openai.com"
                or parsed.username
                or parsed.password
                or parsed.port not in {None, 443}
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("untrusted JWKS endpoint")
            keys = await client.get(jwks_uri)
            keys.raise_for_status()
            header = jwt.get_unverified_header(tokens["id_token"])
            matching = [key for key in keys.json()["keys"] if key.get("kid") == header.get("kid")]
            if len(matching) != 1:
                raise ValueError("missing unique signature key")
            claims = jwt.decode(
                tokens["id_token"],
                jwt.PyJWK.from_dict(matching[0]).key,
                algorithms=["RS256"],
                issuer=ISSUER,
                audience=issued,
                options={"require": ["exp", "iss", "aud", "sub", "nonce"]},
            )
            if (
                not secrets.compare_digest(claims["nonce"], self.nonce)
                or (self.expected_subject and claims["sub"] != self.expected_subject)
                or tokens.get("token_type", "").lower() != "bearer"
            ):
                raise ValueError("identity mismatch")
            session = ChatGPTSession(
                subject=claims["sub"],
                client_id=issued,
                host_id=self.host_id,
                scopes=frozenset(tokens.get("scope", "").split()),
                expires_at=time.time() + min(float(tokens["expires_in"]), 86400),
                access_token=tokens["access_token"],
                refresh_token=tokens["refresh_token"],
                id_token=tokens["id_token"],
            )
            session.require_plan_usage()
            return session
        except (httpx.HTTPError, ValueError, KeyError, TypeError, jwt.PyJWTError):
            failure = AuthenticationError("ChatGPT OAuth identity or permission validation failed")
        raise failure


async def refresh_session(session: ChatGPTSession, client: httpx.AsyncClient) -> ChatGPTSession:
    failure = None
    try:
        response = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": session.client_id,
                "refresh_token": session.refresh_token.get_secret_value(),
                "resource": RESOURCE,
            },
        )
        response.raise_for_status()
        body = response.json()
        renewed = ChatGPTSession.model_validate(
            {
                **session.model_dump(),
                "access_token": SecretStr(body["access_token"]),
                "refresh_token": SecretStr(
                    body.get("refresh_token", session.refresh_token.get_secret_value())
                ),
                "scopes": frozenset(body.get("scope", " ".join(session.scopes)).split()),
                "expires_at": time.time() + min(float(body["expires_in"]), 86400),
            }
        )
        renewed.require_plan_usage()
        return renewed
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        failure = AuthenticationError("ChatGPT session renewal failed; sign in again")
    raise failure


def _protect(data: bytes, *, decrypt: bool = False) -> bytes:
    if os.name != "nt":
        return data

    class Blob(ctypes.Structure):
        _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_byte))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    output = Blob()
    operation = (
        ctypes.windll.crypt32.CryptUnprotectData
        if decrypt
        else ctypes.windll.crypt32.CryptProtectData
    )
    if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise AuthenticationError("credential protection failed")
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        ctypes.windll.kernel32.LocalFree(output.data)


def save_session(path: Path, session: ChatGPTSession) -> None:
    if path.is_symlink() or path.parent.is_symlink():
        raise AuthenticationError("credential storage cannot use a symlink")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = session.model_dump(exclude={"access_token", "refresh_token", "id_token"}, mode="json")
    for name in ("access_token", "refresh_token", "id_token"):
        payload[name] = getattr(session, name).get_secret_value()
    temporary = path.with_name(path.name + "." + secrets.token_hex(8))
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(_protect(json.dumps(payload).encode()))
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def load_session(path: Path) -> ChatGPTSession:
    if path.is_symlink() or path.parent.is_symlink():
        raise AuthenticationError("credential storage cannot use a symlink")
    if os.name != "nt" and path.stat().st_mode & 0o077:
        raise AuthenticationError("credential storage must be owner-only")
    failure = None
    try:
        return ChatGPTSession.model_validate_json(_protect(path.read_bytes(), decrypt=True))
    except (ValueError, OSError):
        failure = AuthenticationError("protected ChatGPT session unavailable")
    raise failure
