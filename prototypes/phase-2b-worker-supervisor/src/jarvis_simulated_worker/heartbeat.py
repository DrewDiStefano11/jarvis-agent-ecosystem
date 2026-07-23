import json
import time
from pathlib import Path

def emit_heartbeat(runtime_dir: str, instance_id: str, start_token: str, sequence: int) -> None:
    state_dir = Path(runtime_dir) / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    heartbeat_file = state_dir / f"heartbeat_{instance_id}.json"

    data = {
        "instance_id": instance_id,
        "process_start_token": start_token,
        "timestamp": time.time(),
        "sequence_number": sequence,
        "state": "running"
    }

    # Write atomically
    temp_file = heartbeat_file.with_suffix('.tmp')
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(data, f)

    temp_file.replace(heartbeat_file)
