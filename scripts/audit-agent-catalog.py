"""Read-only catalog quality, provenance, and security audit CLI."""

import argparse
import json

from app.catalog.audit import CatalogAuditService, format_human_summary
from app.db.session import create_database_engine, create_session_factory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url", required=True, help="Existing migrated Jarvis database"
    )
    parser.add_argument(
        "--json", action="store_true", help="Output audit report as formatted JSON"
    )
    args = parser.parse_args()

    engine = create_database_engine(args.database_url)
    try:
        session_factory = create_session_factory(engine)
        service = CatalogAuditService(session_factory)
        report = service.audit()
        if args.json:
            print(json.dumps(report.model_dump(mode="json"), indent=2))
        else:
            print(format_human_summary(report))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
