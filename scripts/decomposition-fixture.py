"""Explicit acceptance-only workforce setup in the caller's isolated migrated DB."""

import argparse

from app.db.models import IdentityAgentRow
from app.main import create_app
from app.models.catalog import (
    ActivateRequest,
    RawDefinition,
    ReviewRequest,
    SourceSnapshot,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    args = parser.parse_args()
    app = create_app(database_url=args.database_url)
    try:
        # Match the existing Jarvis compatibility identity. This test setup grants
        # no tools, roles or permissions. Task-scoped planning setup is separate.
        with app.state.repository.session_factory() as session, session.begin():
            session.add(
                IdentityAgentRow(
                    id="jarvis",
                    stable_key="jarvis",
                    display_name="Jarvis",
                    agent_type="coordinator",
                    lifecycle_state="active",
                    is_system_agent=True,
                    is_enabled=True,
                )
            )
        catalog = app.state.catalog_service
        definitions = []
        for name, tag in [
            ("market-researcher", "research.market"),
            ("financial-analyst", "business.financial-analysis"),
            ("backend-engineer", "software.backend"),
        ]:
            definitions.append(
                RawDefinition(
                    kind="agent",
                    path=f"agents/{name}.md",
                    text=f"---\nname: {name}\ndescription: Acceptance specialist\ntags: [{tag}]\n---\nEXTERNAL_PROMPT_NOT_FOR_DECOMPOSITION",
                )
            )
        catalog.import_snapshot(
            SourceSnapshot(
                provider="wshobson-agents",
                repository="acceptance/fixture",
                commit="a" * 40,
                license="MIT",
                license_text="Acceptance fixture",
                definitions=definitions,
            ),
            False,
        )
        for item in catalog.repository.page("agent").items:
            catalog.review(
                item.id,
                ReviewRequest(
                    revision_id=item.revision_id,
                    approved=True,
                    reason="Explicit isolated acceptance review",
                ),
            )
            catalog.activate(item.id, ActivateRequest(revision_id=item.revision_id))
    finally:
        app.state.engine.dispose()


if __name__ == "__main__":
    main()
