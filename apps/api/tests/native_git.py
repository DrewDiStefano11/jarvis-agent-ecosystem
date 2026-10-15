"""Select the native fixture executable, never the Git for Windows launcher."""

import os
import shutil
from pathlib import Path

FAMILIES = ("ucrt64", "mingw64", "mingw32", "clangarm64")


def implementation_path(executable, *, windows):
    assert executable, "Native Git is required for repository acceptance"
    path = Path(executable)
    if windows and path.parent.name.casefold() in {"cmd", "bin"}:
        if path.parent.name.casefold() == "bin" and path.parent.parent.name.casefold() in FAMILIES:
            assert path.is_file(), "Native Git implementation is missing"
            return path
        installation = path.parent.parent
        for family in FAMILIES:
            candidate = installation / family / "bin/git.exe"
            if candidate.is_file():
                return candidate
        raise AssertionError("No supported native Git implementation beside the Windows launcher")
    assert path.is_file(), "Native Git implementation is missing"
    return path


def native_git_executable():
    return implementation_path(shutil.which("git"), windows=os.name == "nt")
