from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def ensure_runtime_home(path: Path, repository: Path) -> None:
    existed = path.exists()
    path.mkdir(parents=True, exist_ok=True)
    marker_path = path / ".jarvis-supervisor-runtime.json"
    marker = read_json(marker_path)
    if marker_path.exists() and marker is None:
        raise RuntimeError("runtime home ownership marker is unreadable")
    expected = str(repository.resolve())
    if marker is not None and marker.get("repository") != expected:
        raise RuntimeError("runtime home belongs to another Jarvis installation")
    if marker is None:
        if existed and any(path.iterdir()):
            raise RuntimeError("refusing to adopt a nonempty directory as the runtime home")
        atomic_write_json(
            marker_path,
            {"kind": "jarvis-supervisor-runtime", "repository": expected, "createdAt": utc_now()},
        )


def verified_runtime_home(path: Path, repository: Path) -> bool:
    marker = read_json(path / ".jarvis-supervisor-runtime.json")
    return bool(
        marker
        and marker.get("kind") == "jarvis-supervisor-runtime"
        and marker.get("repository") == str(repository.resolve())
    )
