"""Deterministic autonomy acceptance and local-model evaluation runner.

Subcommands:

- ``acceptance``: run the twelve autonomy acceptance scenarios (fixture
  inference; DB-backed scenarios use isolated temporary databases) and
  write ``autonomy-acceptance.json`` plus a Markdown summary.
- ``evaluate``: run the fixture positive control for local-model
  evaluation (CI-safe, never real inference) and write
  ``model-evaluation.json`` plus a Markdown summary.
- ``evaluate-local``: run installed-local-model evaluation through the
  existing loopback provider architecture. Requires
  ``JARVIS_MODEL_EXECUTION_MODE=local_only``, an enabled local provider,
  and an installed model. Never downloads models or starts services.

All output goes to an ignored directory (default ``.local/``); evidence
files are never committed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.autonomy.evidence import write_evidence_json, write_markdown_summary
from app.autonomy.scenarios import SCENARIO_NAMES, run_scenario
from app.core.config import Settings
from app.model_evaluation.cases import (
    all_cases,
    reference_scripts,
)
from app.model_evaluation.providers import (
    EvaluationUnavailableError,
    ScriptedFixtureProvider,
    build_local_provider,
)
from app.model_evaluation.report import (
    render_evaluation_summary,
    to_evidence,
    write_evaluation_json,
    write_evaluation_summary,
)
from app.model_evaluation.runner import EvaluationBounds, run_evaluation


def repo_sha() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
    except (subprocess.SubprocessError, OSError):
        return "unknown"
    return completed.stdout.strip() or "unknown"


def cmd_acceptance(args: argparse.Namespace) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    names = (
        [name for name in args.scenarios if name in SCENARIO_NAMES]
        if args.scenarios
        else list(SCENARIO_NAMES)
    )
    if args.scenarios and len(names) != len(args.scenarios):
        unknown = sorted(set(args.scenarios) - set(SCENARIO_NAMES))
        print(f"unknown scenarios: {unknown}", file=sys.stderr)
        return 2
    sha = args.repo_sha or repo_sha()
    started = datetime.now(UTC)
    evidences = []
    with tempfile.TemporaryDirectory(dir=out) as work:
        for name in names:
            outcome = run_scenario(name, sha, tmp_path=Path(work) / name)
            evidences.append(outcome.evidence)
            write_evidence_json(out / f"{name}.json", outcome.evidence)
            write_markdown_summary(out / f"{name}.md", outcome.evidence)
            print(
                f"{name}: {outcome.result.terminal_state}/"
                f"{outcome.result.reason_code} "
                f"verdict={outcome.evidence.verdict}"
            )
    ended = datetime.now(UTC)
    verdict = "pass" if all(item.verdict == "pass" for item in evidences) else "fail"
    aggregate = {
        "schema_version": "1.0",
        "repo_sha": sha,
        "started_at": started.isoformat(),
        "ended_at": ended.isoformat(),
        "verdict": verdict,
        "scenarios": [item.model_dump(mode="json") for item in evidences],
    }
    (out / "autonomy-acceptance.json").write_text(
        json.dumps(aggregate, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    lines = [
        "# Autonomy acceptance",
        "",
        f"- verdict: **{verdict}**",
        f"- repo_sha: `{sha}`",
        f"- window: `{started.isoformat()}` -> `{ended.isoformat()}`",
        "",
        "## Scenarios",
        "",
    ]
    for item in evidences:
        lines.append(
            f"- [{item.verdict}] `{item.scenario}` terminal=`{item.terminal_state}`"
        )
    lines += ["", "Per-scenario evidence: `<scenario>.json` and `<scenario>.md`.", ""]
    (out / "autonomy-acceptance-summary.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )
    print(f"aggregate verdict: {verdict} (evidence in {out})")
    return 0 if verdict == "pass" else 1


def cmd_evaluate(args: argparse.Namespace) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    sha = args.repo_sha or repo_sha()
    started = datetime.now(UTC)
    provider = ScriptedFixtureProvider(reference_scripts(repetitions=args.repetitions))
    report = asyncio.run(
        run_evaluation(
            provider,
            all_cases(),
            repetitions=args.repetitions,
            allow_repair=args.allow_repair,
            bounds=EvaluationBounds(),
        )
    )
    ended = datetime.now(UTC)
    evidence = to_evidence(report, repo_sha=sha, started_at=started, ended_at=ended)
    write_evaluation_json(out / "model-evaluation.json", evidence)
    write_evaluation_summary(out / "model-evaluation-summary.md", evidence)
    print(render_evaluation_summary(evidence))
    passing = report.metrics.get("case_pass_rate") == 1.0
    print(f"fixture positive control passing: {passing}")
    return 0 if passing else 1


def cmd_evaluate_local(args: argparse.Namespace) -> int:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    sha = args.repo_sha or repo_sha()
    settings = Settings()
    started = datetime.now(UTC)
    try:
        provider = asyncio.run(
            build_local_provider(
                settings, provider_name=args.provider, model=args.model
            )
        )
    except EvaluationUnavailableError as exc:
        print(f"local evaluation unavailable: {exc}", file=sys.stderr)
        return 2
    report = asyncio.run(
        run_evaluation(
            provider,
            all_cases(),
            repetitions=args.repetitions,
            allow_repair=args.allow_repair,
            bounds=EvaluationBounds(per_call_timeout_seconds=args.timeout_seconds),
        )
    )
    ended = datetime.now(UTC)
    evidence = to_evidence(report, repo_sha=sha, started_at=started, ended_at=ended)
    write_evaluation_json(out / "model-evaluation-local.json", evidence)
    write_evaluation_summary(out / "model-evaluation-local-summary.md", evidence)
    print(render_evaluation_summary(evidence))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    acceptance = sub.add_parser("acceptance", help="run autonomy acceptance scenarios")
    acceptance.add_argument(
        "--out", default=str(ROOT / ".local" / "autonomy-acceptance")
    )
    acceptance.add_argument("--repo-sha", default=None)
    acceptance.add_argument("--scenarios", nargs="*", default=None)
    acceptance.set_defaults(func=cmd_acceptance)
    evaluate = sub.add_parser("evaluate", help="run fixture model evaluation")
    evaluate.add_argument("--out", default=str(ROOT / ".local" / "model-evaluation"))
    evaluate.add_argument("--repo-sha", default=None)
    evaluate.add_argument("--repetitions", type=int, default=1)
    evaluate.add_argument("--allow-repair", action="store_true")
    evaluate.set_defaults(func=cmd_evaluate)
    local = sub.add_parser(
        "evaluate-local", help="run installed-local model evaluation"
    )
    local.add_argument("--out", default=str(ROOT / ".local" / "model-evaluation"))
    local.add_argument("--repo-sha", default=None)
    local.add_argument("--provider", default=None)
    local.add_argument("--model", default=None)
    local.add_argument("--repetitions", type=int, default=1)
    local.add_argument("--allow-repair", action="store_true")
    local.add_argument("--timeout-seconds", type=float, default=120.0)
    local.set_defaults(func=cmd_evaluate_local)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
