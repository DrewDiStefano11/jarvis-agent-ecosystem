"""Bounded local-model qualification runner (thin wrapper).

Usage (from the repository root, with the apps/api environment installed):

    python scripts/model_qualify.py --discover
    python scripts/model_qualify.py --fixture
    python scripts/model_qualify.py --fixture --role planner
    python scripts/model_qualify.py --model <installed-model>
    python scripts/model_qualify.py --model <installed-model> --role reviewer
    python scripts/model_qualify.py --all-local

Evidence is written to an ignored directory (default ``.local/model-qualification``);
generated qualification output is never committed. See ``docs/model-qualification.md``.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_ROOT = ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

if __name__ == "__main__":
    from app.model_qualification.cli import main

    sys.exit(main(sys.argv[1:]))
