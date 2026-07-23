import json
import re
from typing import Tuple, Dict, Any, Optional

class JSONExtractionError(Exception):
    pass

class MultipleJSONObjectsError(JSONExtractionError):
    pass

def _check_depth(obj: Any, current_depth: int, max_depth: int):
    if current_depth > max_depth:
        raise JSONExtractionError(f"JSON depth exceeds maximum of {max_depth}")
    if isinstance(obj, dict):
        for v in obj.values():
            _check_depth(v, current_depth + 1, max_depth)
    elif isinstance(obj, list):
        for v in obj:
            _check_depth(v, current_depth + 1, max_depth)

def extract_json_from_model_response(
    text: str,
    strict: bool = False,
    max_bytes: int = 100_000,
    max_depth: int = 20
) -> Tuple[Dict[str, Any], bool, str]:
    """
    Extracts exactly one JSON object from the text.
    Returns: (parsed_json, extra_prose_detected, extraction_method)
    """
    if len(text.encode("utf-8")) > max_bytes:
        raise JSONExtractionError(f"Input exceeds maximum allowed bytes ({max_bytes})")

    stripped_text = text.strip()
    extra_prose = False

    # Try pure JSON first
    try:
        parsed = json.loads(stripped_text)
        if not isinstance(parsed, dict):
            raise JSONExtractionError("Root JSON element must be an object")
        _check_depth(parsed, 1, max_depth)
        return parsed, False, "pure_json"
    except json.JSONDecodeError:
        pass # Not pure JSON

    if strict:
        raise JSONExtractionError("Response contains extra prose or is malformed, rejected in strict mode")

    extra_prose = True

    # Try fenced code blocks
    fenced_blocks = re.findall(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fenced_blocks:
        valid_objects = []
        for block in fenced_blocks:
            try:
                parsed = json.loads(block.strip())
                if isinstance(parsed, dict):
                    valid_objects.append(parsed)
            except json.JSONDecodeError:
                continue

        if len(valid_objects) == 1:
            _check_depth(valid_objects[0], 1, max_depth)
            return valid_objects[0], extra_prose, "fenced_json"
        elif len(valid_objects) > 1:
            raise MultipleJSONObjectsError("Multiple competing JSON objects found in fenced blocks")

    # Try finding first matching brace (very naive fallback)
    start_idx = text.find("{")
    end_idx = text.rfind("}")
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        candidate = text[start_idx:end_idx+1]
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                _check_depth(parsed, 1, max_depth)
                return parsed, extra_prose, "brace_search"
        except json.JSONDecodeError:
            pass

    raise JSONExtractionError("Could not extract a valid JSON object from the response")
