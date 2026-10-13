"""Read-only local discovery. Never probes inference or activates an account."""

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.core.config import Settings
from app.model_providers.free_capacity import PAID_INFERENCE_CEILING_USD


def main() -> None:
    settings = Settings()
    grants = settings.parsed_free_allowances()
    print(
        json.dumps(
            {
                "policy": "campaign_d_zero_cost",
                "paid_inference_ceiling_usd": PAID_INFERENCE_CEILING_USD,
                "execution_mode": settings.model_execution_mode,
                "allowance_records": len(grants),
                "live_account_verification_performed": False,
                "installed_tools": {
                    name: shutil.which(name) is not None
                    for name in ("codex", "gemini", "ollama")
                },
                "integrations": {
                    name: "requires_current_account_specific_verification"
                    for name in (
                        "codex",
                        "chatgpt",
                        "gemini",
                        "groq",
                        "openrouter",
                        "cloudflare",
                    )
                },
                "other_tools": {
                    name: "discovery_only_no_execution_adapter"
                    for name in ("gemini_cli", "antigravity", "jules")
                },
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
