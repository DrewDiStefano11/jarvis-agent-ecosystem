import re
from typing import Any, Dict, List, Union
from copy import deepcopy

REDACT_KEYS = re.compile(r"(?i)(authorization|api_key|token|password|secret|credential|private_key|session_cookie)")

def redact_dict(data: Dict[str, Any]) -> Dict[str, Any]:
    redacted = deepcopy(data)
    _recursive_redact(redacted)
    return redacted

def _recursive_redact(data: Union[Dict, List, Any]):
    if isinstance(data, dict):
        for k, v in data.items():
            if REDACT_KEYS.search(k):
                if isinstance(v, str):
                    data[k] = "[REDACTED]"
            else:
                _recursive_redact(v)
    elif isinstance(data, list):
        for item in data:
            _recursive_redact(item)
