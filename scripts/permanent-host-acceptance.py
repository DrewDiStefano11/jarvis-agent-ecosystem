"""Permanent-host acceptance orchestration; no inference without explicit flags."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
CRITERIA = {
    "A": "Recoverable 24/7 runtime",
    "B": "Autonomous task execution",
    "C": "Authenticated remote operation",
    "D": "Evidence-driven self-debugging",
    "E": "Verified sandbox development",
    "F": "Scheduled controlled self-improvement",
}


def run_check(name: str, arguments: list[str], out: Path, timeout: float) -> dict:
    """Run a fixed repository entry point; exit status never proves broad readiness."""
    log = out / f"{name}.log"
    with log.open("w", encoding="utf-8") as stream:
        try:
            result = subprocess.run(
                [sys.executable, *arguments],
                cwd=ROOT,
                stdout=stream,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return {
                "name": name,
                "status": "FAIL",
                "reason": "deadline_exceeded",
                "log": log.name,
            }
        except OSError:
            return {
                "name": name,
                "status": "BLOCKED",
                "reason": "process_unavailable",
                "log": log.name,
            }
    status = (
        "PASS"
        if result.returncode == 0
        else "BLOCKED"
        if result.returncode == 2
        else "FAIL"
    )
    return {
        "name": name,
        "status": status,
        "exit_code": result.returncode,
        "log": log.name,
    }


def validate_model_evidence(
    check: dict, path: Path, *, provider: str, model: str
) -> dict:
    """Require fresh real-inference identity and all case verdicts, not just exit 0."""
    sys.path.insert(0, str(ROOT / "apps/api"))
    from app.model_evaluation.cases import all_cases

    if check["status"] != "PASS":
        return check
    try:
        evidence = json.loads(path.read_text(encoding="utf-8"))
        inference = evidence["inference"]
        case_ids = [case["case_id"] for case in evidence["cases"]]
        expected_ids = {case.case_id for case in all_cases()}
        valid = (
            inference
            == {"mode": "installed_local", "provider": provider, "model": model}
            and len(case_ids) == len(expected_ids)
            and set(case_ids) == expected_ids
            and all(case["passed"] is True for case in evidence["cases"])
        )
    except (OSError, ValueError, KeyError, TypeError):
        valid = False
    if not valid:
        return {**check, "status": "FAIL", "reason": "missing_or_invalid_live_evidence"}
    return {**check, "evidence": str(path.relative_to(path.parents[1]))}


def validate_production_evidence(
    check: dict, path: Path, *, provider: str, model: str, task_id: str
) -> dict:
    """Translate only fresh, identity-bound native evidence, never a timeout or crash."""
    sys.path.insert(0, str(ROOT / "apps/api"))
    from app.autonomy.evidence import read_evidence_json

    if check.get("exit_code") not in {0, 1}:
        return check
    try:
        evidence = read_evidence_json(path)
        if (
            evidence.scenario != "production_golden_path"
            or evidence.inference.mode != "installed_local"
            or evidence.inference.provider != provider
            or evidence.inference.model != model
            or evidence.ids.task_ids != (task_id,)
        ):
            raise ValueError("unexpected production evidence identity")
    except (OSError, ValueError, KeyError, TypeError):
        return {
            **check,
            "status": "FAIL",
            "reason": "missing_or_invalid_production_evidence",
        }
    status = "FAIL"
    if (
        check["exit_code"] == 0
        and evidence.verdict == "pass"
        and evidence.terminal_state == "completed"
        and evidence.final_result
        and evidence.checks
        and all(item.passed for item in evidence.checks)
    ):
        status = "PASS"
    elif (
        check["exit_code"] == 1
        and evidence.verdict == "fail"
        and (
            evidence.terminal_state
            in {"blocked", "paused", "awaiting_approval", "cancelled"}
            or (
                evidence.terminal_state in {"queued", "in_progress"}
                and evidence.failure_reason
                in {"planning_pending", "active", "synthesizing", "completing"}
            )
        )
    ):
        status = "BLOCKED"
    return {
        **check,
        "status": status,
        "evidence": str(path.relative_to(path.parents[1])),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, default=ROOT / ".local/permanent-host-acceptance"
    )
    parser.add_argument(
        "--fixture-controls",
        action="store_true",
        help="Run labelled deterministic autonomy controls",
    )
    parser.add_argument(
        "--local-model",
        action="store_true",
        help="Opt in to installed-local model evaluation; never downloads/starts models",
    )
    parser.add_argument(
        "--discover",
        action="store_true",
        help="Read installed local model inventory without inference",
    )
    parser.add_argument(
        "--runtime-diagnostics",
        action="store_true",
        help="Read current runtime diagnostics",
    )
    parser.add_argument("--provider", default="ollama")
    parser.add_argument("--model")
    parser.add_argument(
        "--task-id",
        help="Execute existing explicitly queued authorized production task",
    )
    parser.add_argument("--worker-id")
    parser.add_argument("--timeout-seconds", type=float, default=120)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not 1 <= args.timeout_seconds <= 600:
        parser.error("timeout must be between 1 and 600 seconds")
    if args.local_model and not args.model:
        parser.error("--local-model requires an explicit --model")
    if args.task_id and not (args.local_model and args.worker_id):
        parser.error("--task-id requires --local-model and --worker-id")
    if args.worker_id and not args.task_id:
        parser.error("--worker-id requires --task-id")
    # A new directory prevents a previous run's green artifact from satisfying this run.
    out = args.out / (
        datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:12]
    )
    out.mkdir(parents=True, exist_ok=False)
    checks = []
    if args.discover:
        checks.append(
            run_check(
                "installed-model-discovery",
                [
                    str(ROOT / "scripts/model_qualify.py"),
                    "--discover",
                    "--provider",
                    args.provider,
                    "--json",
                ],
                out,
                120,
            )
        )
    if args.runtime_diagnostics:
        checks.append(
            run_check(
                "runtime-doctor",
                [str(ROOT / "scripts/jarvis_doctor.py"), "--deep", "--json"],
                out,
                120,
            )
        )
    if args.fixture_controls:
        checks.append(
            run_check(
                "fixture-autonomy",
                [
                    str(ROOT / "scripts/autonomy-acceptance.py"),
                    "acceptance",
                    "--out",
                    str(out / "fixtures"),
                ],
                out,
                300,
            )
        )
    if args.local_model:
        check = run_check(
            "installed-local-evaluation",
            [
                str(ROOT / "scripts/autonomy-acceptance.py"),
                "evaluate-local",
                "--provider",
                args.provider,
                "--model",
                args.model,
                "--timeout-seconds",
                str(args.timeout_seconds),
                "--out",
                str(out / "model"),
            ],
            out,
            args.timeout_seconds * 16 + 60,
        )
        checks.append(
            validate_model_evidence(
                check,
                out / "model/model-evaluation-local.json",
                provider=args.provider,
                model=args.model,
            )
        )
        if args.task_id and checks[-1]["status"] == "PASS":
            checks.append(
                run_check(
                    "authorized-production-task",
                    [
                        str(ROOT / "scripts/autonomy-acceptance.py"),
                        "production",
                        "--task-id",
                        args.task_id,
                        "--worker-id",
                        args.worker_id,
                        "--provider",
                        args.provider,
                        "--model",
                        args.model,
                        "--out",
                        str(out / "production"),
                    ],
                    out,
                    args.timeout_seconds * 16 + 60,
                )
            )
            checks[-1] = validate_production_evidence(
                checks[-1],
                out / "production/production-autonomy.json",
                provider=args.provider,
                model=args.model,
                task_id=args.task_id,
            )
        elif args.task_id:
            checks.append(
                {
                    "name": "authorized-production-task",
                    "status": "BLOCKED",
                    "reason": "model_preflight_not_passing",
                }
            )
    overall = (
        "FAIL"
        if any(c["status"] == "FAIL" for c in checks)
        else "BLOCKED"
        if any(c["status"] == "BLOCKED" for c in checks)
        else "NOT TESTED"
    )
    report = {
        "schema_version": "1.0",
        "captured_at": datetime.now(UTC).isoformat(),
        "overall": overall,
        "production_ready": False,
        "checks": checks,
        "criteria": [
            {"id": key, "name": name, "status": "NOT TESTED"}
            for key, name in CRITERIA.items()
        ],
        "limitations": [
            "Fixture controls cannot establish production readiness.",
            "Reboot recovery, separate-device TLS, isolation, sustained workload and controlled repair/improvement require separate host evidence.",
        ],
    }
    (out / "acceptance.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "# Permanent-host acceptance",
        "",
        f"Overall: {overall}. Production readiness is not established.",
        "",
    ]
    lines += [f"- {check['name']}: {check['status']}" for check in checks]
    lines += ["", *[f"- {key}: {name} — NOT TESTED" for key, name in CRITERIA.items()]]
    (out / "acceptance.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Acceptance evidence: {out}")
    if any(check["status"] == "FAIL" for check in checks):
        return 1
    # 2 denotes incomplete acceptance, even when individual controls pass.
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
