"""Fixed operator Credential Manager target; no workspace file or environment key."""

import base64
import ctypes
import os
from ctypes import wintypes

from app.core.errors import DomainError

TARGET = "Jarvis.ValidationCollector.v1"


def decode_key(blob):
    if type(blob) is not bytes or not 1 <= len(blob) <= 128:
        raise ValueError("COLLECTOR_KEY_UNAVAILABLE")
    try:
        key = base64.b64decode(blob, validate=True)
    except ValueError:
        raise ValueError("COLLECTOR_KEY_UNAVAILABLE") from None
    if len(key) != 32:
        raise ValueError("COLLECTOR_KEY_UNAVAILABLE")
    return key


class WindowsCollectorKey:
    """Read only under the separately protected collector service identity.

    No enrollment, key generation, target override or candidate endpoint exists.
    Credential Manager alone does not protect a key from same-user host code;
    candidate code must remain inside the independently verified guest boundary.
    """

    def __call__(self):
        if os.name != "nt":
            raise DomainError("COLLECTOR_KEY_UNAVAILABLE", "Collector credential unavailable.", 503)

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

        pointer = ctypes.POINTER(Credential)()
        api = None
        try:
            api = ctypes.WinDLL("advapi32.dll", winmode=0x00000800)
            api.CredReadW.argtypes = [
                wintypes.LPCWSTR,
                wintypes.DWORD,
                wintypes.DWORD,
                ctypes.POINTER(ctypes.POINTER(Credential)),
            ]
            api.CredReadW.restype = wintypes.BOOL
            api.CredFree.argtypes = [ctypes.c_void_p]
            api.CredFree.restype = None
            if not api.CredReadW(TARGET, 1, 0, ctypes.byref(pointer)):
                raise ValueError("missing key")
            credential = pointer.contents
            if (
                credential.Type != 1
                or credential.TargetName != TARGET
                or not 1 <= credential.CredentialBlobSize <= 128
                or not credential.CredentialBlob
            ):
                raise ValueError("invalid credential")
            return decode_key(
                ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
            )
        except (OSError, ValueError, AttributeError):
            raise DomainError(
                "COLLECTOR_KEY_UNAVAILABLE", "Collector credential unavailable.", 503
            ) from None
        finally:
            if api is not None and pointer:
                api.CredFree(pointer)
