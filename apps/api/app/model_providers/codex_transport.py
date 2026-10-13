from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import tomllib
from pathlib import Path

from app.model_providers.contracts import ModelExecutionRequest
from app.model_providers.errors import (
    AuthenticationError,
    MalformedProviderResponseError,
    ProviderExecutionDisabledError,
    ProviderUnavailableError,
    QuotaExhaustedError,
)
from app.models.ai_capacity import FreeAllowance


def require_safe_config(config: dict) -> None:
    """Reject inherited integrations instead of granting their authority to Jarvis."""
    features = config.get("features") or {}
    if any(
        features.get(name) is not False
        for name in ("shell_snapshot", "view_image", "code_mode_host", "hooks")
    ):
        raise ProviderExecutionDisabledError(
            "Codex snapshot, filesystem, code host and hook tools must be disabled"
        )
    if features.get("shell_tool") is not False or features.get("unified_exec") is not False:
        raise ProviderExecutionDisabledError("Codex command tools must be disabled")
    if features.get("code_mode") is not False or features.get("apply_patch_freeform") is not False:
        raise ProviderExecutionDisabledError("Codex code and patch tools must be disabled")
    if any(item.get("enabled", True) for item in (config.get("mcp_servers") or {}).values()):
        raise ProviderExecutionDisabledError("Codex MCP integrations must be disabled")
    if any(item.get("enabled", True) for item in (config.get("plugins") or {}).values()):
        raise ProviderExecutionDisabledError("Codex plugins must be disabled")
    apps = config.get("apps") or {}
    if (
        apps.get("_default", {}).get("enabled") is not False
        or any(item.get("enabled", True) for key, item in apps.items() if key != "_default")
        or config.get("hooks")
        or config.get("notify")
    ):
        raise ProviderExecutionDisabledError(
            "Codex inherited integrations or hooks are unavailable"
        )
    if config.get("model_provider", "openai") != "openai" or config.get("model_providers"):
        raise ProviderExecutionDisabledError("Codex paid/custom providers are forbidden")


def effective_config(result: dict) -> dict:
    def merge(destination, source):
        for key, value in source.items():
            if isinstance(value, dict) and isinstance(destination.get(key), dict):
                merge(destination[key], value)
            else:
                destination[key] = value

    config = {}
    for layer in result.get("layers") or []:
        if layer.get("disabledReason") is None:
            merge(config, layer["config"])
    merge(config, result["config"])
    return config


class CodexStdioTransport:
    """One inference conversation with supported app-server JSON-RPC.

    Launches an executable without a shell, never accepts workspace authority,
    rejects inherited integrations, and never calls any billing or credit RPC.
    """

    async def infer(self, request: ModelExecutionRequest, grant: FreeAllowance) -> str:
        executable = shutil.which("codex")
        if executable is None:
            raise ProviderUnavailableError("Codex CLI is not installed")
        environment = {
            key: value
            for key, value in os.environ.items()
            if key
            not in {
                "OPENAI_API_KEY",
                "OPENAI_BASE_URL",
                "OPENAI_ORG_ID",
                "OPENAI_PROJECT_ID",
                "AZURE_OPENAI_API_KEY",
            }
        }
        # Check inherited hooks/integrations before starting the process. The
        # remaining layer check below runs before a thread can initialize tools.
        config_path = (
            Path(environment.get("CODEX_HOME", str(Path.home() / ".codex"))) / "config.toml"
        )
        inherited = (
            tomllib.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
        )
        inherited["features"] = {
            "shell_tool": False,
            "unified_exec": False,
            "code_mode": False,
            "apply_patch_freeform": False,
            "shell_snapshot": False,
            "view_image": False,
            "code_mode_host": False,
            "hooks": False,
        }
        apps = inherited.setdefault("apps", {})
        apps.setdefault("_default", {})["enabled"] = False
        inherited["model_provider"] = "openai"
        require_safe_config(inherited)
        overrides = [
            'model_provider="openai"',
            'forced_login_method="chatgpt"',
            "features.shell_tool=false",
            "features.unified_exec=false",
            "features.apply_patch_freeform=false",
            "features.code_mode=false",
            "features.code_mode_host=false",
            "features.shell_snapshot=false",
            "features.view_image=false",
            "features.hooks=false",
            "features.multi_agent=false",
            "features.apps=false",
            "features.plugins=false",
            'web_search="disabled"',
            "apps._default.enabled=false",
            'approval_policy="never"',
            'sandbox_mode="read-only"',
        ]
        arguments = [executable, "app-server", "--listen", "stdio://", "--strict-config"]
        for override in overrides:
            arguments.extend(["-c", override])
        with tempfile.TemporaryDirectory(prefix="jarvis-codex-") as directory:
            process = await asyncio.create_subprocess_exec(
                *arguments,
                cwd=directory,
                env=environment,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                **({"creationflags": 0x08000000} if os.name == "nt" else {}),
            )
            try:
                async with asyncio.timeout(request.timeout_seconds or 30):
                    return await self._conversation(process, request, grant, directory)
            finally:
                if process.returncode is None:
                    process.kill()
                await process.wait()

    async def _conversation(self, process, request, grant, directory):
        serial = 0
        queued = []
        consumed = 0

        async def send(message):
            process.stdin.write((json.dumps(message) + "\n").encode())
            await process.stdin.drain()

        async def read():
            nonlocal consumed
            line = await process.stdout.readline()
            consumed += len(line)
            if not line or consumed > 4_000_000:
                raise MalformedProviderResponseError("Codex stream ended or exceeded limit")
            try:
                message = json.loads(line)
            except ValueError:
                raise MalformedProviderResponseError("Codex emitted invalid JSON-RPC") from None
            if not isinstance(message, dict) or ("method" in message and "id" in message):
                raise ProviderExecutionDisabledError(
                    "Codex requested unsupported client/tool authority"
                )
            return message

        async def rpc(method, params=None):
            nonlocal serial
            serial += 1
            await send({"id": serial, "method": method, "params": params or {}})
            for _ in range(1000):
                message = await read()
                if message.get("id") == serial:
                    if "error" in message:
                        raise ProviderUnavailableError("Codex RPC failed")
                    return message["result"]
                queued.append(message)
            raise MalformedProviderResponseError("Codex RPC exceeded event limit")

        await rpc(
            "initialize", {"clientInfo": {"name": "jarvis", "title": "Jarvis", "version": "0.1.0"}}
        )
        await send({"method": "initialized"})
        configuration = await rpc("config/read", {"includeLayers": True})
        require_safe_config(effective_config(configuration))
        identity = await rpc("account/read", {"refreshToken": False})
        account = identity.get("account") or {}
        if account.get("type") != "chatgpt":
            raise AuthenticationError("Codex must use its existing ChatGPT session")
        if account.get("planType") not in {
            "free",
            "go",
            "plus",
            "pro",
            "prolite",
            "promax",
            "team",
            "business",
            "edu",
            "edu_plus",
            "edu_pro",
        }:
            raise ProviderExecutionDisabledError(
                "Codex usage-based or unknown plans are unavailable"
            )
        # Account identity in native Codex may be email rather than sub. Evidence
        # must identify exactly what the official account/read returns.
        if account.get("email") != (grant.session_identity or grant.account_id):
            raise AuthenticationError("Codex session does not match the verified account")
        limits = await rpc("account/rateLimits/read")
        buckets = limits.get("rateLimitsByLimitId")
        bucket = (
            buckets.get(grant.shared_bucket)
            if isinstance(buckets, dict)
            else limits.get("rateLimits")
        )
        if not isinstance(bucket, dict) or bucket.get("limitId") != grant.shared_bucket:
            raise QuotaExhaustedError("Codex included allowance is unknown")
        credits = bucket.get("credits") or {}
        if credits.get("hasCredits") is not False or credits.get("unlimited") is not False:
            raise ProviderExecutionDisabledError(
                "Codex credit status is unknown or credit consumption is possible"
            )
        windows = [
            bucket.get(key) for key in ("primary", "secondary") if bucket.get(key) is not None
        ]
        if (
            not windows
            or bucket.get("rateLimitReachedType")
            or any(
                not isinstance(w.get("usedPercent"), (int, float))
                or isinstance(w.get("usedPercent"), bool)
                or not 0 <= w["usedPercent"] < 100
                for w in windows
            )
        ):
            raise QuotaExhaustedError("Codex included allowance is exhausted or unknown")
        thread = await rpc(
            "thread/start",
            {
                "model": request.model or sorted(grant.models)[0],
                "cwd": str(Path(directory)),
                "approvalPolicy": "never",
                "sandbox": "read-only",
                "ephemeral": True,
            },
        )
        thread_id = thread["thread"]["id"]
        turn = await rpc(
            "turn/start",
            {
                "threadId": thread_id,
                "input": [
                    {
                        "type": "text",
                        "text": "\n\n".join(
                            f"{m.role.value}: {m.content}" for m in request.messages
                        ),
                    }
                ],
            },
        )
        turn_id = turn["turn"]["id"]
        text = []
        final_content = None
        usage = {}
        for _ in range(10_000):
            message = queued.pop(0) if queued else await read()
            params = message.get("params") or {}
            if params.get("threadId") != thread_id or params.get("turnId", turn_id) != turn_id:
                continue
            if message.get("method") == "item/agentMessage/delta":
                text.append(params["delta"])
            elif message.get("method") == "thread/tokenUsage/updated":
                usage = (params.get("tokenUsage") or {}).get("last") or {}
            elif message.get("method") == "error":
                info = (params.get("error") or {}).get("codexErrorInfo")
                if info == "UsageLimitExceeded":
                    raise QuotaExhaustedError("Codex included allowance exhausted")
            elif message.get("method") == "item/started":
                if params.get("item", {}).get("type") not in {
                    "userMessage",
                    "agentMessage",
                    "reasoning",
                }:
                    raise ProviderExecutionDisabledError("Codex attempted an unavailable tool")
            elif message.get("method") == "item/completed":
                item = params.get("item") or {}
                if item.get("type") == "agentMessage" and item.get("phase") != "commentary":
                    final_content = item.get("text")
            elif message.get("method") == "model/rerouted":
                if params.get("toModel") not in grant.models:
                    raise ProviderExecutionDisabledError("Codex rerouted to an unverified model")
            elif message.get("method") == "turn/completed":
                if params["turn"]["status"] != "completed" or not text:
                    raise ProviderUnavailableError("Codex inference did not complete")
                return {
                    "content": final_content or "".join(text),
                    "input_tokens": usage.get("inputTokens"),
                    "output_tokens": usage.get("outputTokens"),
                }
        raise MalformedProviderResponseError("Codex event limit exceeded")
