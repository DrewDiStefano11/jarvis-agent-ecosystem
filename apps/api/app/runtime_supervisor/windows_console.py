from __future__ import annotations

import ctypes
import os
from typing import Any

SW_HIDE = 0


def ensure_hidden_console(
    *,
    platform: str = os.name,
    kernel32: Any | None = None,
    user32: Any | None = None,
) -> None:
    """Give a detached Windows supervisor a console for process-group signals."""

    if platform != "nt":
        return
    kernel32 = kernel32 or ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    user32 = user32 or ctypes.WinDLL("user32", use_last_error=True)  # type: ignore[attr-defined]
    if not kernel32.GetConsoleCP():
        if not kernel32.AllocConsole():
            error = ctypes.get_last_error()
            raise OSError(error, "could not allocate the supervisor console")
    window = kernel32.GetConsoleWindow()
    if window:
        user32.ShowWindow(window, SW_HIDE)
