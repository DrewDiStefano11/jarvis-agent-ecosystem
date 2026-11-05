from __future__ import annotations

import argparse
import asyncio
import contextlib
import ipaddress
import os
import signal
import sys
from collections.abc import Sequence
from typing import Any

import uvicorn

from app.runtime_supervisor.remote_listener import ListenerSurface, RemoteListener


class _OwnedServer(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self):
        # One process owns both listeners and installs one set of signal handlers.
        yield


async def _serve_shared(app, servers) -> None:
    async with app.router.lifespan_context(app):
        tasks = [asyncio.create_task(server.serve()) for server in servers]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            unexpected = any(not server.should_exit for server in servers)
            for task in done:
                task.result()
            if unexpected:
                raise RuntimeError("supervised API listener exited unexpectedly")
        finally:
            for server in servers:
                server.should_exit = True
            await asyncio.gather(*tasks, return_exceptions=True)


def _run_shared(host: str, port: int, listener: RemoteListener) -> None:
    # Resolve the existing singleton exactly once. Two factories would recover
    # workflows twice and split cached emergency-stop and outbox state.
    from app.main import app

    if host != "localhost" and not ipaddress.ip_address(host).is_loopback:
        raise ValueError("local supervised API listener must remain loopback")
    servers = [
        _OwnedServer(
            uvicorn.Config(
                ListenerSurface(app, loopback=True),
                host=host,
                port=port,
                lifespan="off",
                proxy_headers=False,
                timeout_graceful_shutdown=15,
            )
        ),
        _OwnedServer(
            uvicorn.Config(
                ListenerSurface(app, loopback=False),
                host=listener.host,
                port=listener.port,
                lifespan="off",
                proxy_headers=False,
                timeout_graceful_shutdown=15,
                ssl_certfile=str(listener.certificate),
                ssl_keyfile=str(listener.private_key),
                ssl_keyfile_password="",
            )
        ),
    ]

    def stop(_signum: int, _frame: object) -> None:
        for server in servers:
            server.should_exit = True

    registrations = {}
    try:
        for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            value = getattr(signal, name, None)
            if value is not None:
                registrations[value] = signal.signal(value, stop)
        asyncio.run(_serve_shared(app, servers))
    finally:
        for value, previous in registrations.items():
            signal.signal(value, previous)


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
    listener = RemoteListener.load(dict(os.environ), local_ports={args.port})
    if listener is not None:
        _run_shared(args.host, args.port, listener)
        return 0
    server = uvicorn.Server(uvicorn.Config("app.main:app", host=args.host, port=args.port))
    registration = _install_break_handler(server)
    try:
        server.run()
    finally:
        _restore_break_handler(registration)
    return 0


if __name__ == "__main__":
    sys.exit(main())
