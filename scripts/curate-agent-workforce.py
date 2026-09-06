"""Print an exact coverage proposal; apply only with its approved hash."""

import argparse
import json

from app.catalog.curation import apply_proposal, proposal
from app.catalog.service import CatalogService
from app.db.session import create_database_engine, create_session_factory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument(
        "--approve-plan", help="SHA-256 of the reviewed coverage proposal"
    )
    args = parser.parse_args()
    engine = create_database_engine(args.database_url)
    try:
        service = CatalogService(create_session_factory(engine))
        result = (
            apply_proposal(service, args.approve_plan)
            if args.approve_plan
            else proposal(service)
        )
        print(json.dumps(result, indent=2))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
