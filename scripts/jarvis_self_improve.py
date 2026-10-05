"""Thin operator wrapper for the evidence-only Improvement Lab."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))

if __name__ == "__main__":
    from app.self_improvement.cli import main

    sys.exit(main())
