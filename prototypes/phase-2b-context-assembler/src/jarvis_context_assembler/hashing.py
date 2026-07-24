import hashlib
import json
from typing import Any

def deterministic_hash(data: Any) -> str:
    """Generate a deterministic SHA-256 hash for Python dictionaries, lists, strings, etc."""
    if isinstance(data, str):
        encoded = data.encode('utf-8')
    else:
        encoded = json.dumps(data, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()

def hash_content(content: str) -> str:
    return deterministic_hash(content)
