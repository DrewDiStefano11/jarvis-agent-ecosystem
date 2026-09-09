"""Jarvis runtime doctor: one read-only command to answer "is Jarvis ready to run?".

Usage (from the repository root, with the apps/api environment installed):

    python scripts/jarvis_doctor.py            # fast operator checks
    python scripts/jarvis_doctor.py --deep     # bounded deep checks
    python scripts/jarvis_doctor.py --json     # machine-readable report
    python scripts/jarvis_doctor.py --report   # write jarvis-diagnostic.json/.md

The doctor is read-mostly diagnostics: it never migrates the database, never
clears emergency stop, never starts or kills processes, never downloads models,
and never prints secrets. See docs/runtime-doctor.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_ROOT = ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))


if __name__ == "__main__":
    from app.diagnostics.cli import main

    sys.exit(main(["--repository", str(ROOT), *sys.argv[1:]]))
