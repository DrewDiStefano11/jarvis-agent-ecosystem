import hashlib
import json
import re
from pathlib import PurePosixPath

import yaml

from app.catalog.taxonomy import TAXONOMY_VERSION, map_tags
from app.models.catalog import NormalizedDefinition, RawDefinition

PARSER_VERSION = "1.taxonomy-" + TAXONOMY_VERSION


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class StrictLoader(yaml.SafeLoader):
    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise ValueError("YAML aliases are unsupported")
        return super().compose_node(parent, index)

    def construct_mapping(self, node, deep=False):
        keys = [self.construct_object(key, deep=deep) for key, _ in node.value]
        if len(set(keys)) != len(keys):
            raise ValueError("Duplicate frontmatter keys")
        return super().construct_mapping(node, deep=deep)


def normalize(raw: RawDefinition, provider: str) -> NormalizedDefinition:
    path = PurePosixPath(raw.path)
    if path.is_absolute() or ".." in path.parts or "\\" in raw.path:
        raise ValueError("Unsafe definition path")
    if raw.kind == "discovery":
        meta = {"name": "skill-discovery", "description": "Unreviewed external skill links"}
        body = raw.text
    else:
        parts = raw.text.replace("\r\n", "\n").split("---\n", 2)
        if len(parts) != 3 or parts[0] or len(parts[1]) > 16_000:
            raise ValueError("Expected bounded YAML frontmatter")
        meta = yaml.load(parts[1], Loader=StrictLoader)
        body = parts[2]
    if not isinstance(meta, dict):
        raise ValueError("Frontmatter must be a mapping")
    name, description = meta.get("name"), meta.get("description")
    if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]{1,100}", name):
        raise ValueError("Expected normalized source name")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("Description is required")
    tags = meta.get("tags", [])
    if (
        not isinstance(tags, list)
        or len(tags) > 100
        or any(not isinstance(t, str) or len(t) > 120 for t in tags)
    ):
        raise ValueError("Tags must be bounded strings")
    plugin = path.parts[1] if len(path.parts) > 2 and path.parts[0] == "plugins" else ""
    role = path.stem if raw.kind == "agent" and name == plugin + "-" + path.stem else name
    capabilities, unmapped = map_tags([role, plugin, *tags] if plugin else [role, *tags])
    warnings = []
    if any(key in meta for key in ("permissions", "enabled", "trustStatus", "system", "hooks")):
        warnings.append("authority_fields_ignored")
    if re.search(
        r"ignore.{0,40}(rules|instructions)|system\s*prompt|bypass|secret|api.key", body, re.I
    ):
        warnings.append("suspicious_instruction_text")
    if not capabilities:
        warnings.append("capability_review_required")
    tool_rules = {
        "shell.execute": r"\b(bash|shell|powershell|subprocess)\b",
        "filesystem.read": r"\b(read|glob|grep)\b",
        "filesystem.write": r"\b(write|edit)\b",
        "browser.search": r"\b(websearch|webfetch|browse|browser)\b",
        "github.repository.read": r"\bgithub\b",
    }
    requested = sorted(k for k, pattern in tool_rules.items() if re.search(pattern, raw.text, re.I))
    if requested:
        warnings.append("tool_requests_are_not_permissions")
    references = sorted(set(re.findall(r"\[[^\]\n]*\]\(([^\s)]+)\)", body)))[:100]
    return NormalizedDefinition(
        kind=raw.kind,
        stable_key=f"{provider}.{digest(raw.path)[:32]}",
        display_name=role.replace("-", " ").title(),
        description=description[:2000],
        role=role,
        capabilities=capabilities,
        unmapped_tags=unmapped,
        specialties=tags,
        skill_references=[],
        requested_tool_classes=requested,
        preferred_model_classes=[str(meta["model"])[:80]]
        if isinstance(meta.get("model"), str)
        else [],
        references=references,
        applicable_agent_classes=["specialist"] if raw.kind == "skill" else [],
        normalized_instructions="Specialist reference only. Jarvis policy, operator approval and RBAC govern all actions. Declared capabilities: "
        + ", ".join(capabilities),
        warnings=warnings,
        duplicate_key=digest(json.dumps([raw.kind, role], sort_keys=True)),
    )
