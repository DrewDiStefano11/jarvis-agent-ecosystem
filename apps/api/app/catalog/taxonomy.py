"""Versioned, deterministic metadata mapping, never inferred from prompt bodies."""

TAXONOMY_VERSION = "1"
ALIASES = {
    "management.coordination": ("coordination", "context-manager", "team-lead"),
    "management.planning": ("planning", "plan", "team-lead"),
    "research.general": ("research", "search-specialist"),
    "research.market": ("market", "market-research", "startup-analyst", "business-analyst"),
    "research.competitive": ("competitive", "competitive-landscape", "startup-analyst"),
    "business.strategy": ("business", "business-analyst", "startup-analyst"),
    "business.financial-analysis": ("financial", "quant-analyst", "startup-financial-modeling"),
    "business.opportunity-analysis": ("opportunity", "market-sizing-analysis", "startup-analyst"),
    "business.risk": ("risk", "risk-manager"),
    "business.marketing": ("marketing", "seo", "seo-content-planner"),
    "software.architecture": ("architecture", "architect", "backend-architect", "c4-context"),
    "software.backend": ("backend", "backend-development"),
    "software.backend.api": ("api", "fastapi", "fastapi-pro"),
    "software.frontend": ("frontend", "frontend-developer", "react"),
    "software.python": ("python", "python-pro", "python-development"),
    "software.testing": ("testing", "test", "test-automator", "pytest", "tdd"),
    "software.security": ("security", "security-auditor", "security-scanning"),
    "software.code-review": ("code-review", "code-reviewer"),
    "data.analysis": ("data", "data-scientist", "data-analysis"),
    "data.visualization": (
        "visualization",
        "data-visualization",
        "data-scientist",
        "business-analyst",
    ),
    "content.writing": ("writing", "writer", "content-marketer"),
    "content.editing": ("editing", "editor", "seo-content-auditor"),
    "content.strategy": ("content-strategy", "seo-content-writer"),
    "operations.documentation": ("documentation", "docs-architect", "tutorial-engineer"),
    "operations.knowledge-management": ("knowledge", "knowledge-management", "context-manager"),
}
CAPABILITIES = frozenset(ALIASES)
NODES = frozenset(part for key in CAPABILITIES for part in (key, key.split(".")[0]))


def map_tags(tags: list[str]) -> tuple[list[str], list[str]]:
    mapped, unknown = set(), set()
    for tag in tags:
        normalized = tag.lower().strip().replace("_", "-")
        matches = {key for key, aliases in ALIASES.items() if normalized in (key, *aliases)}
        mapped.update(matches)
        if not matches:
            unknown.add(tag)
    return sorted(mapped), sorted(unknown)


def satisfies(offered: str, required: str) -> bool:
    """Only declared descendants satisfy their ancestor; no sibling implication."""
    return (
        offered in NODES
        and required in NODES
        and (offered == required or offered.startswith(required + "."))
    )
