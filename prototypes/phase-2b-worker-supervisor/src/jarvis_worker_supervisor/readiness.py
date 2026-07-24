from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def check_readiness(
    runtime_dir: str,
    instance_id: str,
    start_token: str,
    *,
    not_before: float = 0.0,
) -> dict[str, Any] | None:
    ready_file = Path(runtime_dir) / "state" / f"ready_{instance_id}.json"
    try:
        data = json.loads(ready_file.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError, UnicodeError):
        return None
    if (
        data.get("instance_id") != instance_id
        or data.get("process_start_token") != start_token
        or data.get("status") != "ready"
        or not isinstance(data.get("timestamp"), (int, float))
        or data["timestamp"] < not_before
    ):
        return None
    return data
