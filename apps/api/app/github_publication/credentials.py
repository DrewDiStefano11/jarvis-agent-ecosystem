"""Operator credentials resolved outside source workspaces, never in public contracts."""

import ctypes
import os
from ctypes import wintypes
from typing import Protocol

from pydantic import SecretStr

from app.core.errors import DomainError


class CredentialBroker(Protocol):
    def resolve(self) -> SecretStr: ...


class WindowsCredentialBroker:
    """Read an operator-provisioned generic credential afresh for every API call.

    No credential installation, export, model tool, file fallback or diagnostics.
    Rotation/revocation becomes visible at the next call. A deployment may instead
    inject a GitHub App installation token broker implementing the same protocol.
    """

    def __init__(self, target: str):
        if not target.startswith("Jarvis/GitHub/") or len(target) > 200:
            raise DomainError("GITHUB_CREDENTIAL_TARGET_INVALID", "Invalid credential target.")
        self._target = target

    def resolve(self) -> SecretStr:
        if os.name != "nt":
            raise DomainError(
                "GITHUB_CREDENTIAL_PLATFORM_UNSUPPORTED", "Credential broker unavailable.", 503
            )

        class Credential(ctypes.Structure):
            _fields_ = [
                ("Flags", wintypes.DWORD),
                ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR),
                ("Comment", wintypes.LPWSTR),
                ("LastWritten", wintypes.FILETIME),
                ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD),
                ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR),
                ("UserName", wintypes.LPWSTR),
            ]

        library = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        read = library.CredReadW
        read.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.POINTER(Credential)),
        ]
        read.restype = wintypes.BOOL
        free = library.CredFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = None
        pointer = ctypes.POINTER(Credential)()
        if not read(self._target, 1, 0, ctypes.byref(pointer)):
            raise DomainError(
                "GITHUB_CREDENTIAL_UNAVAILABLE", "Operator credential unavailable.", 503
            )
        try:
            size = pointer.contents.CredentialBlobSize
            if size < 2 or size > 4096 or size % 2:
                raise ValueError
            token = ctypes.string_at(pointer.contents.CredentialBlob, size).decode("utf-16-le")
            if not token or not token.isascii() or any(character.isspace() for character in token):
                raise ValueError
            return SecretStr(token)
        except (ValueError, UnicodeError):
            raise DomainError(
                "GITHUB_CREDENTIAL_UNAVAILABLE", "Operator credential unavailable.", 503
            ) from None
        finally:
            # Zero the native credential copy before returning it to Windows.
            if pointer.contents.CredentialBlob and pointer.contents.CredentialBlobSize:
                ctypes.memset(
                    pointer.contents.CredentialBlob, 0, pointer.contents.CredentialBlobSize
                )
            free(pointer)
