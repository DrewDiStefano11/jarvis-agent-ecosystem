"""Command-line interface for bounded local-model qualification.

Usage (from the repository root, with the ``apps/api`` environment installed)::

    python scripts/model_qualify.py --discover
    python scripts/model_qualify.py --fixture
    python scripts/model_qualify.py --fixture --role planner --role reviewer
    python scripts/model_qualify.py --model qwen3:14b
    python scripts/model_qualify.py --model qwen3:14b --role reviewer
    python scripts/model_qualify.py --all-local

Exit codes are stable and machine-readable:

- ``0`` the qualification run completed (models may still be unqualified — that
  is a result, not a tool failure);
- ``2`` the run could not start: bad usage, unknown role/persona, or the local
  provider/model is unavailable (never reported as a failed model);
- ``3`` the qualification tool itself failed.

The CLI only observes. It never changes production routing, agent models,
manager authority, permissions, or coordinator state.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
from pathlib import Path

from app.core.config import Settings
from app.model_qualification.discovery import discover_local_models
from app.model_qualification.fixtures import persona_names
from app.model_qualification.profile import (
    QualificationRun,
    write_markdown_summary,
    write_profile_json,
    write_run_json,
)
from app.model_qualification.roles import parse_roles, role_names
from app.model_qualification.runner import (
    DEFAULT_BOUNDS,
    build_run,
    qualify_installed_models,
    run_fixture_qualification,
)

EXIT_OK = 0
EXIT_UNAVAILABLE = 2
EXIT_ERROR = 3

DEFAULT_OUT = Path(".local") / "model-qualification"


def repository_root() -> Path:
    return Path(__file__).resolve().parents[4]


def repo_sha(root: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
    except (subprocess.SubprocessError, OSError):
        return "unknown"
    return completed.stdout.strip() or "unknown"


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", value)[:80] or "model"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="model-qualify",
        description=(
            "Bounded local-model qualification: qualify installed local models for "
            "Jarvis roles and emit evidence-backed role profiles. Observation only; "
            "production routing is never modified."
        ),
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--fixture",
        action="store_true",
        help="run deterministic fixture personas (CI-safe, never real inference)",
    )
    mode.add_argument(
        "--model",
        metavar="NAME",
        help="qualify one exact installed-local model (no download, no fallback)",
    )
    mode.add_argument(
        "--all-local",
        action="store_true",
        help="discover and qualify every installed local model (bounded)",
    )
    mode.add_argument(
        "--discover",
        action="store_true",
        help="list installed local providers and models, then exit",
    )
    parser.add_argument(
        "--persona",
        action="append",
        default=None,
        metavar="NAME",
        help=f"fixture persona for --fixture (available: {', '.join(persona_names())})",
    )
    parser.add_argument(
        "--role",
        action="append",
        default=None,
        metavar="ROLE",
        help=f"limit to a role (available: {', '.join(role_names())})",
    )
    parser.add_argument("--provider", default=None, help="restrict to one local provider")
    parser.add_argument(
        "--out",
        default=str(DEFAULT_OUT),
        help=f"evidence output directory (default: {DEFAULT_OUT})",
    )
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--allow-repair", action="store_true")
    parser.add_argument("--repo-sha", default=None)
    parser.add_argument("--json", dest="json_output", action="store_true")
    return parser


def _resolve_roles(values: list[str] | None) -> tuple:
    try:
        return parse_roles(values)
    except ValueError as exc:
        raise SystemExit(f"error: {exc}") from exc


def _print_run(profiles: tuple, run: QualificationRun) -> None:
    for profile in profiles:
        print(
            f"{profile.model}: status={profile.status} "
            f"inference_mode={profile.inference_mode} provider={profile.provider}"
        )
        for role in sorted(profile.roles):
            record = profile.roles[role]
            score = "n/a" if record.score is None else f"{record.score:.3f}"
            print(f"  {role}: {record.qualification} score={score}")
    print("")
    print("role ranking:")
    for comparison in run.comparisons:
        ranked = ", ".join(
            f"{item.rank}. {item.model} ({item.qualification})" for item in comparison.candidates
        )
        print(f"  {comparison.role}: {ranked or 'no candidates'}")
    print("")
    print("recommended role map (evidence only):")
    print(
        json.dumps(
            {item.role: item.model for item in run.recommendations}, indent=2, sort_keys=True
        )
    )


def _write_evidence(out: Path, profiles: tuple, run: QualificationRun) -> tuple[Path, Path]:
    out.mkdir(parents=True, exist_ok=True)
    run_path = write_run_json(out / "model-qualification.json", run)
    summary_path = write_markdown_summary(out / "model-qualification-summary.md", profiles, run)
    for profile in profiles:
        write_profile_json(out / f"profile-{_safe_name(profile.model)}.json", profile)
    return run_path, summary_path


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = repository_root()
    out = Path(args.out)
    if not out.is_absolute():
        out = root / out
    roles = _resolve_roles(args.role)
    sha = args.repo_sha or repo_sha(root)

    if args.discover:
        discovery = asyncio.run(discover_local_models(Settings(), provider_name=args.provider))
        if args.json_output:
            print(json.dumps(discovery.document(), indent=2, sort_keys=True))
        else:
            print(f"execution_mode: {discovery.execution_mode}")
            print(f"status: {discovery.status}")
            print(f"detail: {discovery.detail}")
            for provider in discovery.providers:
                print(
                    f"- provider {provider.provider}: local={provider.is_local} "
                    f"status={provider.status} models={list(provider.models)}"
                )
            print(
                "installed local models: "
                + (", ".join(item.identity for item in discovery.models) or "(none)")
            )
        return EXIT_OK if discovery.available else EXIT_UNAVAILABLE

    bounds = DEFAULT_BOUNDS
    if args.fixture:
        try:
            profiles = asyncio.run(
                run_fixture_qualification(
                    personas=tuple(args.persona) if args.persona else None,
                    roles=roles,
                    bounds=bounds,
                    repo_sha=sha,
                )
            )
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_UNAVAILABLE
        run = build_run(profiles, roles=roles, repo_sha=sha)
        run_path, summary_path = _write_evidence(out, profiles, run)
        if args.json_output:
            print(json.dumps(run.model_dump(mode="json"), indent=2, sort_keys=True))
        else:
            print("inference_mode = fixture")
            _print_run(profiles, run)
        print(f"evidence written: {run_path}", file=sys.stderr)
        print(f"summary written: {summary_path}", file=sys.stderr)
        return EXIT_OK

    settings = Settings()
    if args.model:
        profiles, warnings = asyncio.run(
            qualify_installed_models(
                settings,
                models=(args.model,),
                provider_name=args.provider,
                roles=roles,
                bounds=bounds,
                repetitions=args.repetitions,
                allow_repair=args.allow_repair,
                repo_sha=sha,
            )
        )
    else:
        discovery = asyncio.run(discover_local_models(settings, provider_name=args.provider))
        if not discovery.available:
            print(
                f"installed-local qualification unavailable: {discovery.status}: "
                f"{discovery.detail}",
                file=sys.stderr,
            )
            if args.json_output:
                print(json.dumps(discovery.document(), indent=2, sort_keys=True))
            return EXIT_UNAVAILABLE
        models = tuple(
            item.model
            for item in discovery.models
            if args.provider is None or item.provider == args.provider
        )
        print(
            f"discovered {len(models)} installed local model(s): {list(models)}",
            file=sys.stderr,
        )
        profiles, warnings = asyncio.run(
            qualify_installed_models(
                settings,
                models=models,
                provider_name=args.provider,
                roles=roles,
                bounds=bounds,
                repetitions=args.repetitions,
                allow_repair=args.allow_repair,
                repo_sha=sha,
            )
        )

    run = build_run(profiles, roles=roles, repo_sha=sha, extra_warnings=warnings)
    run_path, summary_path = _write_evidence(out, profiles, run)
    if args.json_output:
        print(json.dumps(run.model_dump(mode="json"), indent=2, sort_keys=True))
    else:
        print("inference_mode = installed_local")
        _print_run(profiles, run)
    print(f"evidence written: {run_path}", file=sys.stderr)
    print(f"summary written: {summary_path}", file=sys.stderr)
    unavailable = [item for item in profiles if item.status == "unavailable"]
    if unavailable and len(unavailable) == len(profiles):
        print(
            "installed-local qualification unavailable for every requested model "
            "(no model quality was inferred)",
            file=sys.stderr,
        )
        return EXIT_UNAVAILABLE
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - script entry point
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"model-qualify failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(EXIT_ERROR) from exc
