"""Isolated API + worker + browser workspace execution, with explicit inference mode.

Default: deterministic HTTP fixture planning followed by real filesystem tools.
Use --provider ollama to test an already installed local model; no fixture fallback.
Service logs, screenshots, receipt and temporary workspace remain in the printed
evidence directory. No application imports occur in this orchestration process.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

SCRIPT_ROOT = Path(__file__).resolve().parent
BRIEF = "WORKSPACE_ACCEPTANCE_20260905: Three local projects need a concise status report.\n"
REPORT = "# Local project report\n\nWORKSPACE_ACCEPTANCE_20260905: Three local projects need review.\n"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def ready(url: str, process: subprocess.Popen, timeout: int = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Service exited with {process.returncode}: {url}")
        try:
            with urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
            pass
        time.sleep(0.1)
    raise RuntimeError(f"Service was not ready: {url}")


def stop(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        # Kill only this harness's live child tree, including venv redirectors.
        result = subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode and process.poll() is None:
            raise RuntimeError(f"Could not stop owned child {process.pid}: {result.stderr!r}")
    else:
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=SCRIPT_ROOT.parent)
    parser.add_argument("--provider", choices=["fixture", "ollama"], default="fixture")
    parser.add_argument("--ollama-base-url", default="http://127.0.0.1:11535")
    parser.add_argument("--model", default="qwen3.5:0.8b")
    args = parser.parse_args()
    root = args.repository_root.resolve()
    api = root / "apps/api"
    web = root / "apps/web"
    evidence = Path(tempfile.mkdtemp(prefix="jarvis-workspace-tools-"))
    workspace = evidence / "workspace"
    (workspace / "inputs").mkdir(parents=True)
    (workspace / "reports").mkdir()
    (workspace / "inputs/brief.txt").write_bytes(BRIEF.encode("utf-8"))
    (workspace / ".jarvis-workspace.json").write_text(
        json.dumps(
            {
                "schemaVersion": "1.0",
                "workspaceId": "lab",
                "allowedTools": [
                    "workspace.list",
                    "workspace.read",
                    "workspace.report",
                ],
                "readPrefixes": ["inputs"],
                "writePrefixes": ["reports"],
            }
        ),
        encoding="utf-8",
    )
    print(f"Evidence: {evidence}", flush=True)
    calls = []
    fixture = {
        "schemaVersion": "1.0",
        "summary": "Workspace transport fixture plan.",
        "analysis": "Fixed test proposal; the worker will run real tools after operator authorization.",
        "recommendations": [
            {
                "title": "Review",
                "description": "Inspect the proposed report.",
                "priority": "medium",
            }
        ],
        "risks": [
            {
                "title": "Fixture",
                "description": "This proposal is deterministic.",
                "severity": "low",
                "mitigation": "Use explicit Ollama mode to test real inference.",
            }
        ],
        "assumptions": ["The supplied objective already contains the report facts."],
        "missingInformation": [],
        "requiresHumanReview": False,
        "steps": [
            {"tool": "workspace.list", "path": "inputs"},
            {"tool": "workspace.read", "path": "inputs/brief.txt"},
            {"tool": "workspace.report", "path": "reports/plan.md", "content": REPORT},
        ],
    }

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, value):
            data = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self.reply({"models": [{"name": "workspace-transport-fixture"}]})

        def do_POST(self):
            calls.append(
                json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            )
            self.reply(
                {
                    "model": "workspace-transport-fixture",
                    "message": {"role": "assistant", "content": json.dumps(fixture)},
                    "done": True,
                    "done_reason": "stop",
                    "prompt_eval_count": 20,
                    "eval_count": 80,
                }
            )

    provider = None
    provider_thread = None
    processes = []
    handles = []
    try:
        if args.provider == "fixture":
            provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
            provider_thread = threading.Thread(
                target=provider.serve_forever, daemon=True
            )
            provider_thread.start()
            model_url = f"http://127.0.0.1:{provider.server_port}"
            model = "workspace-transport-fixture"
        else:
            model_url = args.ollama_base_url.rstrip("/")
            model = args.model
            # This only inspects the chosen service; it never installs or starts models.
            with urlopen(f"{model_url}/api/tags", timeout=5) as response:
                installed = json.load(response)
            if not any(
                item.get("name") == model for item in installed.get("models", [])
            ):
                raise RuntimeError(
                    f"Required model {model!r} is not installed at {model_url}; no fixture fallback."
                )
        port, web_port = free_port(), free_port()
        base, ui = f"http://127.0.0.1:{port}", f"http://127.0.0.1:{web_port}"
        # Set every isolation path before even the setup subprocess imports app.main.
        env = {
            **os.environ,
            "PYTHONPATH": str(api),
            "JARVIS_DATABASE_URL": f"sqlite:///{(evidence / 'hub.db').as_posix()}",
            "JARVIS_DATA_DIRECTORY": str(evidence / "data"),
            "JARVIS_AUTO_MIGRATE": "true",
            "JARVIS_SIMULATOR_AUTO_RESUME": "false",
            "WEB_ORIGIN": ui,
            "JARVIS_AUTONOMOUS_WORKER_ENABLED": "false",
            "JARVIS_AUTONOMOUS_WORKER_ACTOR_ID": "",
            "JARVIS_MODEL_EXECUTION_MODE": "disabled",
            "JARVIS_MODEL_OLLAMA_ENABLED": "false",
            "JARVIS_MODEL_OPENAI_COMPATIBLE_ENABLED": "false",
            "JARVIS_MODEL_ALLOW_REMOTE": "false",
            "JARVIS_TOOL_EXECUTION_ENABLED": "true",
            "JARVIS_TOOL_WORKSPACES_JSON": json.dumps({"lab": str(workspace)}),
        }
        setup = subprocess.run(
            [
                sys.executable,
                "-m",
                "app.autonomous_worker.setup",
                "--task-id",
                "task-demo",
            ],
            cwd=api,
            env=env,
            capture_output=True,
            text=True,
            check=True,
            timeout=45,
        )
        actor = json.loads(setup.stdout)["actorId"]
        env.update(
            {
                "JARVIS_AUTONOMOUS_WORKER_ENABLED": "true",
                "JARVIS_AUTONOMOUS_WORKER_ACTOR_ID": actor,
                "JARVIS_AUTONOMOUS_WORKER_INSTANCE_ID": "workspace-acceptance-worker",
                "JARVIS_AUTONOMOUS_WORKER_POLL_INTERVAL_MS": "100",
                "JARVIS_AUTONOMOUS_WORKER_MAX_EXECUTION_SECONDS": "300",
                "JARVIS_MODEL_EXECUTION_MODE": "local_only",
                "JARVIS_MODEL_OLLAMA_ENABLED": "true",
                "JARVIS_MODEL_OLLAMA_BASE_URL": model_url,
                "JARVIS_MODEL_OLLAMA_MODEL": model,
                "JARVIS_MODEL_OLLAMA_TIMEOUT_SECONDS": "180",
                "JARVIS_MODEL_PROVIDER_PRIORITY": "ollama",
            }
        )

        def start(name, command, cwd, environment):
            log = (evidence / f"{name}.log").open("a", encoding="utf-8")
            handles.append(log)
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=environment,
                stdout=log,
                stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            processes.append(process)
            return process

        def start_api():
            process = start(
                "api",
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                ],
                api,
                env,
            )
            ready(f"{base}/api/health", process)
            return process

        api_process = start_api()
        worker = start(
            "worker", [sys.executable, "-m", "app.autonomous_worker"], api, env
        )
        vite = start(
            "web",
            [
                "node",
                str(web / "node_modules/vite/bin/vite.js"),
                "--host",
                "127.0.0.1",
                "--port",
                str(web_port),
                "--strictPort",
            ],
            web,
            {
                **env,
                "VITE_API_BASE_URL": base,
                "VITE_WS_URL": f"ws://127.0.0.1:{port}/ws/events",
            },
        )
        ready(ui, vite)
        browser_env = {
            **env,
            "SMOKE_WEB": str(web),
            "SMOKE_BASE": base,
            "SMOKE_UI": ui,
            "SMOKE_ACTOR": actor,
            "SMOKE_ARTIFACT_DIR": str(evidence),
            "SMOKE_INFERENCE": args.provider,
            "SMOKE_MODEL": model,
            "SMOKE_TIMEOUT_MS": "240000" if args.provider == "ollama" else "60000",
        }

        def browser(phase):
            run = subprocess.run(
                ["node", str(SCRIPT_ROOT / "smoke-workspace-tools.cjs"), phase],
                env=browser_env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=300 if args.provider == "ollama" else 100,
                check=False,
            )
            (evidence / f"browser-{phase}.log").write_text(
                run.stdout + run.stderr, encoding="utf-8"
            )
            if run.returncode:
                raise RuntimeError(run.stdout + run.stderr)
            print(run.stdout.strip(), flush=True)

        browser("execute")
        receipt = json.loads((evidence / "receipt.json").read_text(encoding="utf-8"))
        actual = (workspace / "reports/plan.md").read_bytes()
        assert hashlib.sha256(actual).hexdigest() == receipt["artifact"]["contentHash"]
        assert actual.decode("utf-8") == receipt["artifact"]["content"]
        assert (workspace / "inputs/brief.txt").read_text(encoding="utf-8") == BRIEF
        assert sorted(
            p.relative_to(workspace).as_posix()
            for p in workspace.rglob("*")
            if p.is_file()
        ) == [
            ".jarvis-workspace.json",
            ".jarvis-workspace.lock",
            "inputs/brief.txt",
            "reports/plan.md",
        ]
        stop(worker)
        stop(api_process)
        start_api()
        browser("verify")
        assert (workspace / "reports/plan.md").read_bytes() == actual
        if args.provider == "fixture":
            assert len(calls) == 1, (
                f"Expected one fixture inference call, received {len(calls)}"
            )
        metadata = {
            "inference": "deterministic HTTP fixture"
            if args.provider == "fixture"
            else "actual local Ollama",
            "model": model,
            "fixtureCalls": len(calls) if provider else None,
            "repositoryRoot": str(root),
            "artifactContentHash": receipt["artifact"]["contentHash"],
            "status": "passed",
        }
        (evidence / "acceptance.json").write_text(
            json.dumps(metadata, indent=2), encoding="utf-8"
        )
        print(
            f"PASS: {metadata['inference']} planning + real authorized filesystem execution + browser artifact + API restart. {evidence}",
            flush=True,
        )
    finally:
        for process in reversed(processes):
            stop(process)
        for handle in handles:
            handle.close()
        if provider:
            provider.shutdown()
            provider.server_close()
        if provider_thread:
            provider_thread.join(timeout=5)


if __name__ == "__main__":
    main()
