import json
from .contracts import ContextPolicy, TaskRequest, ContextSource, ContextSourceMetadata

def load_policy(path: str) -> ContextPolicy:
    with open(path, 'r') as f:
        data = json.load(f)
    return ContextPolicy(**data)

def load_task(path: str) -> TaskRequest:
    with open(path, 'r') as f:
        data = json.load(f)
    return TaskRequest(**data)

def load_sources(path: str) -> list[ContextSource]:
    with open(path, 'r') as f:
        data = json.load(f)
    sources = []
    if isinstance(data, dict):
        data = [data]
    for item in data:
        meta_data = item.pop("metadata", {})
        meta = ContextSourceMetadata(**meta_data)
        sources.append(ContextSource(metadata=meta, **item))
    return sources
