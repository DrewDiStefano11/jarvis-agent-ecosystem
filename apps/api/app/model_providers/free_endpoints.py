import re

from app.models.ai_capacity import FreeAllowance


def compatible_endpoint(grant: FreeAllowance) -> str:
    endpoints = {
        "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "groq": "https://api.groq.com/openai/v1/",
        "openrouter": "https://openrouter.ai/api/v1/",
    }
    if grant.integration == "cloudflare":
        if not re.fullmatch(r"[a-fA-F0-9]{32}", grant.account_id):
            raise ValueError("Cloudflare requires the verified account identifier")
        return f"https://api.cloudflare.com/client/v4/accounts/{grant.account_id}/ai/v1/"
    if grant.integration not in endpoints:
        raise ValueError("integration does not support chat completions")
    return endpoints[grant.integration]
