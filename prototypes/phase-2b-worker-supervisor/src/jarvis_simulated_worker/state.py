import json
import time
from pathlib import Path


def mark_ready(runtime_dir: str, instance_id: str, start_token: str, scenario: str) -> None:
    state_dir = Path(runtime_dir) / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    ready_file = state_dir / f"ready_{instance_id}.json"

    data = {
        "instance_id": instance_id,
        "process_start_token": start_token,
        "timestamp": time.time(),
        "status": "ready",
        "scenario": scenario,
    }

    # Write atomically
    temp_file = ready_file.with_suffix(".tmp")
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(data, f)

    temp_file.replace(ready_file)
