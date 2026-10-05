"""Explicit local backlog admission into native tasks; never execution approval."""

import argparse
import asyncio
import json
import sys

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.agent_runtime.authorization import IdentityRuntimeAuthorizer
from app.core.config import Settings
from app.core.errors import DomainError
from app.db.session import create_database_engine, create_session_factory
from app.identity.service import IdentityService
from app.models.improvement_backlog import SelectImprovementRequest
from app.repositories.sqlalchemy import SqlAlchemyRepository
from app.self_improvement.backlog_service import ImprovementBacklogService
from app.services.events import EventBroker


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Select evidence-backed work without approving execution"
    )
    parser.add_argument(
        "--database-url", help="Existing migrated database; otherwise native Settings"
    )
    parser.add_argument("--actor-id", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    select = commands.add_parser("select")
    select.add_argument("--baseline", action="append", required=True)
    select.add_argument("--idempotency-key", required=True)
    view = commands.add_parser("list")
    view.add_argument("--offset", type=int, default=0)
    view.add_argument("--limit", type=int, default=20)
    args = parser.parse_args(argv)
    engine = None
    try:
        engine = create_database_engine(args.database_url or Settings().database_url)
        sessions = create_session_factory(engine)
        identity = IdentityService(sessions)
        actor = IdentityRuntimeAuthorizer(identity).authenticate(args.actor_id)
        repository = SqlAlchemyRepository(sessions)
        service = ImprovementBacklogService(repository, EventBroker(repository), identity)
        if args.command == "select":
            result = asyncio.run(
                service.select(
                    actor,
                    SelectImprovementRequest(baseline_ids=tuple(args.baseline)),
                    args.idempotency_key,
                )
            )
            print(result.model_dump_json(indent=2))
        else:
            print(
                json.dumps(
                    [
                        item.model_dump(mode="json")
                        for item in service.list_items(actor, offset=args.offset, limit=args.limit)
                    ],
                    indent=2,
                )
            )
        return 0
    except (DomainError, ValidationError, ValueError, SQLAlchemyError):
        print(
            "Backlog operation denied or invalid; check identity, permission, evidence and state.",
            file=sys.stderr,
        )
        return 2
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
