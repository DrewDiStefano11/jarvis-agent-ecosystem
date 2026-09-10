"""Command-line interface for the Jarvis runtime doctor.

Exit codes are stable and machine-readable:

- ``0`` all checks healthy;
- ``1`` degraded (or unknown) conditions found;
- ``2`` at least one blocking condition prevents normal operation;
- ``3`` the doctor itself failed (never masked as a healthy system).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from app.diagnostics.models import CheckStatus
from app.diagnostics.runner import (
    render_json,
    render_text,
    run_diagnostics,
    write_report_files,
)

EXIT_HEALTHY = 0
EXIT_DEGRADED = 1
EXIT_BLOCKED = 2
EXIT_DOCTOR_ERROR = 3


def default_repository() -> Path:
    return Path(__file__).resolve().parents[4]


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="jarvis-doctor",
        description=(
            "Diagnose whether the local Jarvis runtime is ready to run: API, database, "
            "supervisor, worker, model provider, readiness, emergency stop, and more. "
            "Read-only; never migrates, never clears emergency stop, never kills processes."
        ),
    )
    result.add_argument(
        "--repository",
        type=Path,
        default=default_repository(),
        help="repository root (default: the checkout containing this package)",
    )
    result.add_argument(
        "--deep",
        action="store_true",
        help=(
            "additionally exercise API contracts, database integrity, and cross-source "
            "state consistency (still read-only and bounded)"
        ),
    )
    result.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="print the full machine-readable report instead of the concise text output",
    )
    result.add_argument(
        "--report",
        dest="report_directory",
        nargs="?",
        const=".",
        default=None,
        metavar="DIRECTORY",
        help=(
            "write a diagnostic bundle (jarvis-diagnostic.json and jarvis-diagnostic.md) "
            "into DIRECTORY (default: the repository root when the flag is given)"
        ),
    )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    repository = args.repository.resolve()
    try:
        run = run_diagnostics(repository, deep=args.deep)
    except Exception as exc:  # noqa: BLE001 - doctor failures must stay diagnosable
        print(
            f"jarvis-doctor failed unexpectedly: {exc.__class__.__name__}: {exc}",
            file=sys.stderr,
        )
        return EXIT_DOCTOR_ERROR
    report = run.report
    if args.report_directory is not None:
        directory = Path(args.report_directory)
        if not directory.is_absolute():
            directory = repository / directory
        json_path, markdown_path = write_report_files(report, directory, run.context.secret_values)
        print(f"diagnostic report written: {json_path}", file=sys.stderr)
        print(f"diagnostic report written: {markdown_path}", file=sys.stderr)
    if args.json_output:
        print(render_json(report, run.context.secret_values))
    else:
        print(render_text(report, run.context.secret_values))
    if report.overall == CheckStatus.BLOCKED:
        return EXIT_BLOCKED
    if report.overall == CheckStatus.DEGRADED:
        return EXIT_DEGRADED
    return EXIT_HEALTHY


if __name__ == "__main__":
    sys.exit(main())
