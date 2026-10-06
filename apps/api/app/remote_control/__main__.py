"""Explicit direct-TLS launcher; no reverse-proxy authority or plaintext fallback."""

import argparse
from pathlib import Path

import uvicorn

from app.core.config import Settings


def main():
    parser = argparse.ArgumentParser(
        description="Serve the authenticated remote operator API over TLS"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8443)
    parser.add_argument("--cert", type=Path, required=True)
    parser.add_argument("--key", type=Path, required=True)
    args = parser.parse_args()
    settings = Settings()
    if not settings.remote_control_enabled:
        parser.error("Remote control must be explicitly enabled and configured")
    if not 1 <= args.port <= 65535 or not args.cert.is_file() or not args.key.is_file():
        parser.error("A valid port and existing TLS certificate/private key are required")
    uvicorn.run(
        "app.main:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        ssl_certfile=str(args.cert),
        ssl_keyfile=str(args.key),
        proxy_headers=False,
        forwarded_allow_ips="",
        access_log=False,
        server_header=False,
        limit_concurrency=16,
        timeout_keep_alive=5,
    )


if __name__ == "__main__":
    main()
