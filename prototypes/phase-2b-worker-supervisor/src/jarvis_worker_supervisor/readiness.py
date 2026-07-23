import json
from pathlib import Path
from typing import Optional

def check_readiness(runtime_dir: str, instance_id: str, start_token: str) -> Optional[dict]:
    ready_file = Path(runtime_dir) / "state" / f"ready_{instance_id}.json"
    if not ready_file.exists():
        return None

    try:
        with open(ready_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        if data.get("instance_id") == instance_id and data.get("process_start_token") == start_token:
            return data
    except Exception:
        pass

    return None
