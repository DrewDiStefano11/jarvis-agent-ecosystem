"""Operator CLI with verified TLS and environment-only bearer credentials."""

import argparse
import json
import os
import ssl
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx

from app.remote_control.access import TOKEN_PATTERN


def read_json(path: Path):
    with path.open("rb") as stream:
        payload = stream.read(65_537)
    if len(payload) > 65_536:
        raise ValueError("Input JSON exceeds the remote request bound")
    return json.loads(payload)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Operate the authenticated remote API over verified TLS"
    )
    parser.add_argument("--origin", default=os.environ.get("JARVIS_REMOTE_ORIGIN", ""))
    parser.add_argument("--ca", type=Path, help="Trusted operator CA/certificate file")
    sub = parser.add_subparsers(dest="operation", required=True)
    for name in ["goals", "agents", "runs"]:
        command = sub.add_parser(name)
        command.add_argument("--offset", type=int, default=0)
        command.add_argument("--limit", type=int, default=20)
    for name in ["goal", "graph", "audit", "run", "result", "cancel-goal"]:
        sub.add_parser(name).add_argument("id")
    for name in ["status", "emergency-stop", "system-resume"]:
        sub.add_parser(name)
    submit = sub.add_parser("submit")
    submit.add_argument("--file", type=Path, required=True)
    submit.add_argument("--idempotency-key", required=True)
    command = sub.add_parser("command")
    command.add_argument("--file", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        origin = urlsplit(args.origin)
        if (
            origin.scheme != "https"
            or not origin.hostname
            or origin.username is not None
            or origin.password is not None
            or origin.path not in {"", "/"}
            or origin.query
            or origin.fragment
        ):
            raise ValueError("A credential-free HTTPS origin is required")
        credential = os.environ.get("JARVIS_REMOTE_OPERATOR_TOKEN", "")
        if TOKEN_PATTERN.fullmatch(credential) is None:
            raise ValueError("A valid environment-only operator credential is required")
        headers = {"Authorization": "Bearer " + credential}
        method, body, params = "GET", None, None
        operation = args.operation
        if operation in {"goals", "agents", "runs"}:
            if not 0 <= args.offset <= 100_000 or not 1 <= args.limit <= 100:
                raise ValueError("Pagination exceeds remote bounds")
            path = "/" + ("runtime/runs" if operation == "runs" else operation)
            params = {"offset": args.offset, "limit": args.limit}
        elif operation == "status":
            path = "/status"
        elif operation in {"emergency-stop", "system-resume"}:
            method = "POST"
            path = "/system/" + ("emergency-stop" if operation == "emergency-stop" else "resume")
        elif operation in {"submit", "command"}:
            method, body = "POST", read_json(args.file)
            path = "/goals" if operation == "submit" else "/runtime/commands"
            if operation == "submit":
                headers["Idempotency-Key"] = args.idempotency_key
        else:
            item_id = quote(args.id, safe="")
            if operation in {"run", "result"}:
                path = "/runtime/runs/" + item_id + ("/result" if operation == "result" else "")
            else:
                suffix = {
                    "goal": "",
                    "graph": "/graph",
                    "audit": "/audit",
                    "cancel-goal": "/cancel",
                }[operation]
                path = "/goals/" + item_id + suffix
                if operation == "cancel-goal":
                    method = "POST"
        context = ssl.create_default_context(cafile=str(args.ca) if args.ca else None)
        with httpx.Client(
            base_url=args.origin.rstrip("/") + "/api/remote",
            verify=context,
            trust_env=False,
            timeout=15,
            follow_redirects=False,
        ) as client:
            response = client.request(method, path, json=body, params=params, headers=headers)
            response.raise_for_status()
            print(json.dumps(response.json(), indent=2))
        return 0
    except (ValueError, OSError, httpx.HTTPError):
        # Never echo credential-bearing request objects, URLs, headers or files.
        print("Remote operation failed; verify configuration, permission and server status.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
