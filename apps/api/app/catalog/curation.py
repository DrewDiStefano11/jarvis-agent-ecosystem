"""Operator-owned initial coverage proposal. Never applied at startup/import."""

import json
from collections import Counter

from sqlalchemy import select

from app.catalog.normalize import digest
from app.catalog.service import CatalogService
from app.catalog.sources import SOURCES
from app.catalog.taxonomy import CAPABILITIES
from app.core.errors import DomainError
from app.db.models import CatalogEntryRow, CatalogRevisionRow, CatalogSourceRow
from app.models.catalog import ActivateRequest, ReviewRequest

CURATED_ROLES = (
    "context-manager",
    "team-lead",
    "backend-architect",
    "fastapi-pro",
    "frontend-developer",
    "business-analyst",
    "code-reviewer",
    "docs-architect",
    "tutorial-engineer",
    "content-marketer",
    "search-specialist",
    "data-scientist",
    "python-pro",
    "quant-analyst",
    "risk-manager",
    "test-automator",
    "security-auditor",
    "seo-content-auditor",
    "seo-content-planner",
    "seo-content-writer",
    "startup-analyst",
    "threat-modeling-expert",
)


def proposal(service: CatalogService) -> dict:
    with service.repository.sessions() as session:
        rows = session.execute(
            select(
                CatalogEntryRow.id,
                CatalogRevisionRow.id,
                CatalogRevisionRow.normalized,
                CatalogRevisionRow.source_hash,
            )
            .join(CatalogRevisionRow, CatalogEntryRow.current_revision_id == CatalogRevisionRow.id)
            .join(CatalogSourceRow, CatalogRevisionRow.source_id == CatalogSourceRow.id)
            .where(
                CatalogEntryRow.kind == "agent",
                CatalogEntryRow.duplicate_of.is_(None),
                CatalogSourceRow.repository == SOURCES["wshobson-agents"][0],
                CatalogSourceRow.commit == SOURCES["wshobson-agents"][1],
                CatalogSourceRow.license == "MIT",
                CatalogRevisionRow.normalized["role"].as_string().in_(CURATED_ROLES),
            )
            .order_by(CatalogEntryRow.stable_key)
            .limit(100)
        ).all()
    by_role = {row[2]["role"]: row for row in rows}
    missing = sorted(set(CURATED_ROLES) - by_role.keys())
    if missing:
        raise DomainError(
            "CATALOG_CURATION_INCOMPLETE",
            "Import the pinned source before curation. Missing roles: " + ", ".join(missing),
            409,
        )
    selected = [
        dict(
            entry_id=row[0],
            revision_id=row[1],
            role=role,
            capabilities=row[2]["capabilities"],
            source_hash=row[3],
            warnings=row[2]["warnings"],
        )
        for role in CURATED_ROLES
        for row in [by_role[role]]
    ]
    coverage = Counter(key for row in selected for key in row["capabilities"])
    payload = dict(
        source_repository=SOURCES["wshobson-agents"][0],
        source_commit=SOURCES["wshobson-agents"][1],
        agents=selected,
        coverage=dict(sorted(coverage.items())),
        gaps=sorted(CAPABILITIES - coverage.keys()),
        permissions_granted=0,
    )
    payload["plan_hash"] = digest(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return payload


def apply_proposal(service: CatalogService, approved_hash: str) -> dict:
    plan = proposal(service)
    if plan["plan_hash"] != approved_hash:
        raise DomainError(
            "CATALOG_CURATION_CHANGED", "The exact coverage proposal hash must be approved.", 409
        )
    for agent in plan["agents"]:
        service.review(
            agent["entry_id"],
            ReviewRequest(
                revision_id=agent["revision_id"],
                approved=True,
                reason="Operator approved curated workforce proposal " + approved_hash,
            ),
        )
        service.activate(agent["entry_id"], ActivateRequest(revision_id=agent["revision_id"]))
    return plan
