import json
from pathlib import Path
from typing import Optional

def check_heartbeat(runtime_dir: str, instance_id: str, start_token: str) -> Optional[dict]:
    heartbeat_file = Path(runtime_dir) / "state" / f"heartbeat_{instance_id}.json"
    if not heartbeat_file.exists():
        return None

    try:
        with open(heartbeat_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        if data.get("instance_id") == instance_id and data.get("process_start_token") == start_token:
            return data
    except Exception:
        pass

    return None
