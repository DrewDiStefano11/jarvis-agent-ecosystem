"""Read-only catalog quality, provenance, and security audit CLI."""

import argparse
import json
import sqlite3
from pathlib import Path

from app.catalog.audit import CatalogAuditService, format_human_summary
from app.db.session import create_session_factory
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url", required=True, help="Existing migrated Jarvis database"
    )
    parser.add_argument(
        "--json", action="store_true", help="Output audit report as formatted JSON"
    )
    args = parser.parse_args()

    url = make_url(args.database_url)
    if (
        url.drivername != "sqlite"
        or not url.database
        or url.database == ":memory:"
        or url.query
    ):
        parser.error("Audit requires an existing SQLite file with no URL query options")
    database = Path(url.database).resolve()
    if not database.is_file():
        parser.error("Audit database must already exist")
    # No startup migrations, writable connections or journal-mode changes.
    engine = create_engine(
        "sqlite://",
        creator=lambda: sqlite3.connect(database.as_uri() + "?mode=ro", uri=True),
    )
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
