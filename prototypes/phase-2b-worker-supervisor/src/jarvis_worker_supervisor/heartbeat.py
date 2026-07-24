from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def check_heartbeat(
    runtime_dir: str,
    instance_id: str,
    start_token: str,
    *,
    minimum_sequence: int = 0,
    not_before: float = 0.0,
) -> dict[str, Any] | None:
    heartbeat_file = Path(runtime_dir) / "state" / f"heartbeat_{instance_id}.json"
    try:
        data = json.loads(heartbeat_file.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError, UnicodeError):
        return None
    sequence = data.get("sequence_number")
    if (
        data.get("instance_id") != instance_id
        or data.get("process_start_token") != start_token
        or not isinstance(data.get("timestamp"), (int, float))
        or data["timestamp"] < not_before
        or not isinstance(sequence, int)
        or sequence <= minimum_sequence
    ):
        return None
    return data
