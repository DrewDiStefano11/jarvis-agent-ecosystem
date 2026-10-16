"""Operator-invoked OAuth only; this command never makes inference requests."""

import argparse
import asyncio
import json
import os
import sys
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.model_providers.chatgpt_auth import (
    PendingSignIn,
    load_session,
    refresh_session,
    save_session,
)
from app.model_providers.errors import AuthenticationError


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Continue with ChatGPT; no inference or credit operations"
    )
    parser.add_argument("--session", default=".local/chatgpt/session.bin")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    target = (ROOT / args.session).resolve()
    if not target.is_relative_to(ROOT / ".local"):
        parser.error(
            "protected sessions must be stored under the ignored .local directory"
        )
    host_path = ROOT / ".local" / "chatgpt-host-id"
    host_path.parent.mkdir(parents=True, exist_ok=True)
    if not host_path.exists():
        with host_path.open("x", encoding="utf-8") as output:
            output.write("urn:uuid:" + str(uuid.uuid4()))
    host = host_path.read_text(encoding="utf-8").strip()

    async def authenticate():
        async with httpx.AsyncClient(
            trust_env=False, follow_redirects=False, timeout=30
        ) as client:
            existing = load_session(target) if target.exists() else None
            if args.refresh:
                if existing is None:
                    raise AuthenticationError("no saved session to renew")
                return await refresh_session(existing, client)
            callback = {}

            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass  # Never log callback codes, state, or tokens.

                def do_GET(self):
                    parts = urlsplit(self.path)
                    if parts.path != "/auth/callback" or len(self.path) > 8192:
                        self.send_error(404)
                        return
                    values = parse_qs(parts.query, max_num_fields=20)
                    if any(len(v) != 1 for v in values.values()):
                        self.send_error(400)
                        return
                    callback.update({key: value[0] for key, value in values.items()})
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain")
                    self.end_headers()
                    self.wfile.write(b"Return to Jarvis to finish sign-in.")

            with HTTPServer(("127.0.0.1", 0), Handler) as server:
                server.timeout = 600
                pending = PendingSignIn(
                    host,
                    f"http://127.0.0.1:{server.server_port}/auth/callback",
                    client_id=existing.client_id
                    if existing
                    else "dynamic_agent_client",
                    expected_subject=existing.subject if existing else None,
                )
                print(
                    "Continue with ChatGPT in your browser. This grants no authority to spend."
                )
                webbrowser.open(pending.authorization_url())
                await asyncio.to_thread(server.handle_request)
                return await pending.complete(callback, client)

    # Serialize updates, including rotating refresh tokens, across local processes.
    lock = target.with_suffix(".lock")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = None
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        session = asyncio.run(authenticate())
        save_session(target, session)
        print(
            json.dumps(
                {
                    "session_file": str(target),
                    "client_id": session.client_id,
                    "inference_enabled": False,
                    "next_step": "Verify account nonbillable boundary before enabling.",
                }
            )
        )
    except (AuthenticationError, OSError):
        parser.exit(
            1,
            "ChatGPT sign-in unavailable or session is locked. No inference was enabled.\n",
        )
    finally:
        if descriptor is not None:
            os.close(descriptor)
            lock.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
