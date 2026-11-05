"""Reuse Windows user-bound protection; never fall back to plaintext storage."""

import os

from pydantic import SecretStr

from app.core.errors import DomainError
from app.model_providers.chatgpt_auth import _protect
from app.model_providers.errors import AuthenticationError


def supported():
    return os.name == "nt"


def protect(key: SecretStr) -> bytes:
    if not supported():
        raise DomainError(
            "MODEL_CREDENTIAL_PLATFORM_UNSUPPORTED",
            "Protected credential storage is unavailable.",
            503,
        )
    try:
        return _protect(key.get_secret_value().encode())
    except (OSError, AuthenticationError, ValueError):
        raise DomainError(
            "MODEL_CREDENTIAL_UNAVAILABLE", "Protected credential storage is unavailable.", 503
        ) from None


def unprotect(payload: bytes) -> SecretStr:
    if not supported():
        raise DomainError(
            "MODEL_CREDENTIAL_PLATFORM_UNSUPPORTED",
            "Protected credential storage is unavailable.",
            503,
        )
    try:
        return SecretStr(_protect(payload, decrypt=True).decode())
    except (OSError, AuthenticationError, ValueError, UnicodeError):
        raise DomainError(
            "MODEL_CREDENTIAL_UNAVAILABLE", "Protected credential storage is unavailable.", 503
        ) from None
