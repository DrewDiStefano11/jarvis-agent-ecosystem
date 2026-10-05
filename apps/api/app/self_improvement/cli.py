"""Operator-controlled evidence import. Never runs evaluations or changes config."""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings
from app.db.session import create_database_engine, create_session_factory
from app.models.self_improvement import CandidateAttestation
from app.self_improvement.adapters import ArtifactSource
from app.self_improvement.runtime import RuntimeHistorySource
from app.self_improvement.service import ImprovementService


def read_json(path):
    file = Path(path)
    if file.stat().st_size > 2_000_000:
        raise ValueError("evidence input exceeds 2 MB")
    return json.loads(file.read_text(encoding="utf-8"))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Evidence-only Improvement Lab; no changes are applied or approved."
    )
    parser.add_argument(
        "--database-url", help="Existing migrated database; defaults to Jarvis Settings"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    analyze_parser = sub.add_parser(
        "analyze", help="Persist a bounded immutable analysis of recorded evidence"
    )
    analyze_parser.add_argument(
        "--evidence", action="append", default=[], metavar="KIND:ALIAS:PATH"
    )
    analyze_parser.add_argument("--repo-sha", required=True)
    analyze_parser.add_argument("--configuration-fingerprint", required=True)
    analyze_parser.add_argument("--safety-fingerprint", required=True)
    analyze_parser.add_argument("--runtime-start", type=datetime.fromisoformat)
    analyze_parser.add_argument("--runtime-end", type=datetime.fromisoformat)
    analyze_parser.add_argument("--json", action="store_true")
    proposals = sub.add_parser("proposals")
    proposals.add_argument("--baseline")
    proposals.add_argument("--json", action="store_true")
    compare = sub.add_parser("compare")
    compare.add_argument("before")
    compare.add_argument("after")
    compare.add_argument("--proposal", required=True)
    compare.add_argument(
        "--attestation",
        required=True,
        help="Bounded operator safety/test evidence JSON; never model generated",
    )
    compare.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    engine = create_database_engine(args.database_url or Settings().database_url)
    service = ImprovementService(create_session_factory(engine))
    try:
        if args.command == "analyze":
            if len(args.evidence) > 64:
                raise ValueError("too many evidence sources")
            sources = []
            for spec in args.evidence:
                kind, alias, path = spec.split(":", 2)
                sources.append(ArtifactSource(kind, alias, read_json(path)))
            if args.runtime_start or args.runtime_end:
                if not args.runtime_start or not args.runtime_end:
                    raise ValueError("supply both runtime window bounds")
                sources.append(
                    RuntimeHistorySource(
                        service.repository.sessions, args.runtime_start, args.runtime_end
                    )
                )
            result = service.analyze(
                sources,
                repo_sha=args.repo_sha,
                configuration_fingerprint=args.configuration_fingerprint,
                safety_fingerprint=args.safety_fingerprint,
            )
            if args.json:
                print(result.model_dump_json(indent=2))
            else:
                print(
                    f"Measured facts: baseline {result.baseline.id}; {len(result.baseline.observations)} observations; missing metrics: {', '.join(result.baseline.missing_metrics)}"
                )
                print(
                    f"Deterministic findings: {len(result.weaknesses)}; model hypotheses: {len(result.hypotheses)}"
                )
                for proposal in result.proposals:
                    print(
                        f"Proposed only: {proposal.id} [{proposal.priority}/{proposal.status}] {proposal.change_description}"
                    )
                print(
                    "Approved changes: none. No configuration, source, permissions or routing changed."
                )
        elif args.command == "proposals":
            analyses = (
                [service.repository.analysis(args.baseline)]
                if args.baseline
                else service.repository.list_analyses()
            )
            result = [p.model_dump(mode="json") for a in analyses for p in a.proposals]
            print(
                json.dumps(result, indent=2)
                if args.json
                else "Proposed only; no approvals/execution:\n"
                + "\n".join(f"{p['id']} {p['status']} {p['change_description']}" for p in result)
            )
        else:
            attestation = CandidateAttestation.model_validate(read_json(args.attestation))
            result = service.compare(args.before, args.after, args.proposal, attestation)
            print(
                result.model_dump_json(indent=2)
                if args.json
                else f"Decision: {result.decision}\n" + "\n".join(result.reasons)
            )
        return 0
    except (ValueError, ValidationError, OSError, KeyError, TypeError, SQLAlchemyError):
        # Never echo an untrusted artifact, provider output or validation input.
        print(
            "Invalid or insufficient evidence; check bounded input and provenance contracts.",
            file=sys.stderr,
        )
        return 2
    finally:
        engine.dispose()
