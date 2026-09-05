from __future__ import annotations

import argparse
import signal
import sys
from collections.abc import Sequence
from typing import Any

import uvicorn


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the supervised Jarvis API")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    return parser


def _install_break_handler(server: uvicorn.Server) -> tuple[int, Any] | None:
    sigbreak = getattr(signal, "SIGBREAK", None)
    if sigbreak is None:
        return None

    def request_graceful_exit(_signum: int, _frame: object) -> None:
        server.should_exit = True

    previous = signal.signal(sigbreak, request_graceful_exit)
    return sigbreak, previous


def _restore_break_handler(registration: tuple[int, Any] | None) -> None:
    if registration is not None:
        signal.signal(*registration)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    server = uvicorn.Server(uvicorn.Config("app.main:app", host=args.host, port=args.port))
    registration = _install_break_handler(server)
    try:
        server.run()
    finally:
        _restore_break_handler(registration)
    return 0


if __name__ == "__main__":
    sys.exit(main())
