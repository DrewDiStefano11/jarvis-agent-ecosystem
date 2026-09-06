"""Explicit, pinned, offline-capable import; never starts or activates workers."""

import argparse
import json
from pathlib import Path

from app.catalog.service import CatalogService
from app.catalog.sources import SOURCES, acquire
from app.db.session import create_database_engine, create_session_factory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=SOURCES, required=True)
    parser.add_argument(
        "--ref", required=True, help="Full immutable 40-character commit SHA"
    )
    parser.add_argument("--local-tree", type=Path)
    parser.add_argument(
        "--database-url", required=True, help="Existing migrated Jarvis database"
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    snapshot = acquire(args.source, args.ref, args.local_tree)
    engine = create_database_engine(args.database_url)
    try:
        result = CatalogService(create_session_factory(engine)).import_snapshot(
            snapshot, args.dry_run
        )
        print(json.dumps(result.model_dump(), indent=2))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
