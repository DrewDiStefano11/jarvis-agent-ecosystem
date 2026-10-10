"""Standalone CLI for comparing existing Jarvis model qualification artifacts.

Usage (from the repository root, with the apps/api environment installed):

    python scripts/compare_model_qualification.py baseline.json target.json
    python scripts/compare_model_qualification.py baseline.json target.json --json
    python scripts/compare_model_qualification.py baseline.json target.json --role planner --role reviewer
    python scripts/compare_model_qualification.py baseline.json target.json --out .local/comparison-results

Exit codes:
    0: Comparison successfully completed (even if models regressed or are non-comparable)
    2: Bad usage or missing/invalid input files
    3: Unexpected execution failure
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_ROOT = ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.model_qualification.comparison import (
    compare_qualification_artifacts,
    write_comparison_json,
    write_comparison_markdown,
)
from app.model_qualification.roles import parse_roles, role_names

EXIT_OK = 0
EXIT_INVALID_INPUT = 2
EXIT_ERROR = 3

DEFAULT_OUT = Path(".local") / "model-qualification-comparison"


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="compare-model-qualification",
        description=(
            "Compare two previously generated Jarvis model qualification reports "
            "and determine whether performance improved, regressed, remained unchanged, "
            "or is non-comparable."
        ),
    )
    parser.add_argument(
        "baseline_artifact",
        metavar="BASELINE_JSON",
        help="Path to baseline model qualification report or profile JSON file",
    )
    parser.add_argument(
        "target_artifact",
        metavar="TARGET_JSON",
        help="Path to target model qualification report or profile JSON file",
    )
    parser.add_argument(
        "--model",
        action="append",
        default=None,
        metavar="NAME",
        help="Filter comparison to specific model name(s)",
    )
    parser.add_argument(
        "--role",
        action="append",
        default=None,
        metavar="ROLE",
        help=f"Filter comparison to specific role(s) (available: {', '.join(role_names())})",
    )
    parser.add_argument(
        "--out",
        default=str(DEFAULT_OUT),
        help=f"Output directory for comparison evidence (default: {DEFAULT_OUT})",
    )
    parser.add_argument("--repo-sha", default=None)
    parser.add_argument("--json", dest="json_output", action="store_true", help="Print JSON report to stdout")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    baseline_path = Path(args.baseline_artifact)
    target_path = Path(args.target_artifact)

    if not baseline_path.exists():
        print(f"error: baseline artifact file does not exist: {baseline_path}", file=sys.stderr)
        return EXIT_INVALID_INPUT

    if not target_path.exists():
        print(f"error: target artifact file does not exist: {target_path}", file=sys.stderr)
        return EXIT_INVALID_INPUT

    try:
        resolved_roles = parse_roles(args.role) if args.role else None
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_INVALID_INPUT

    role_filter = tuple(r.value for r in resolved_roles) if resolved_roles else None
    model_filter = tuple(args.model) if args.model else None

    try:
        report = compare_qualification_artifacts(
            baseline_source=baseline_path,
            target_source=target_path,
            roles=role_filter,
            model_names=model_filter,
            baseline_source_name=str(baseline_path),
            target_source_name=str(target_path),
        )
    except ValueError as exc:
        print(f"error loading or parsing qualification artifact: {exc}", file=sys.stderr)
        return EXIT_INVALID_INPUT
    except Exception as exc:  # noqa: BLE001
        print(f"unexpected error during comparison: {exc}", file=sys.stderr)
        return EXIT_ERROR

    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir

    json_path = write_comparison_json(out_dir / "qualification-comparison.json", report)
    md_path = write_comparison_markdown(out_dir / "qualification-comparison-summary.md", report)

    if args.json_output:
        print(json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True))
    else:
        print(f"baseline: {report.baseline_source}")
        print(f"target:   {report.target_source}")
        print("summary:")
        for verdict, count in sorted(report.summary.items()):
            print(f"  {verdict}: {count}")
        print()
        print("comparisons:")
        for c in report.comparisons:
            b_score = f"{c.baseline_score:.3f}" if c.baseline_score is not None else "n/a"
            t_score = f"{c.target_score:.3f}" if c.target_score is not None else "n/a"
            delta = f"{c.score_delta:+.3f}" if c.score_delta is not None else "n/a"
            print(f"  {c.model} ({c.provider}) [{c.role}]: {c.verdict} (scores: {b_score} -> {t_score}, delta: {delta})")
            print(f"    reason: {c.reason}")

    print(f"evidence written to {json_path}", file=sys.stderr)
    print(f"summary written to {md_path}", file=sys.stderr)
    return EXIT_OK


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"compare-model-qualification failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(EXIT_ERROR)
