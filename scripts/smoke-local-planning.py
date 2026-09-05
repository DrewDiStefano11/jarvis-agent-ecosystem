"""Real API + worker process + frontend submission contract, with fixture inference.

Run with the repository venv after installing backend and frontend dependencies.
Uses an isolated database and loopback ports; never calls an external model.
"""

from __future__ import annotations

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

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "apps/api"
WEB = ROOT / "apps/web"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    calls = []
    result = {
        "schemaVersion": "1.0",
        "summary": "Deterministic transport fixture completed.",
        "analysis": "This verifies HTTP, worker execution and persistence; it is not real inference.",
        "recommendations": [
            {
                "title": "Verify",
                "description": "Inspect durable history.",
                "priority": "high",
            }
        ],
        "risks": [
            {
                "title": "Fixture",
                "description": "No real inference.",
                "severity": "low",
                "mitigation": "Configure a real local provider separately.",
            }
        ],
        "assumptions": ["An explicit fixture is in use."],
        "missingInformation": [],
        "requiresHumanReview": False,
    }

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, value):
            payload = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            self.reply({"models": [{"name": "fixture-model"}]})

        def do_POST(self):
            calls.append(
                json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            )
            if os.environ.get("JARVIS_SMOKE_OFFICE") == "true":
                time.sleep(3)  # Make real fixture execution observable in the office.
            self.reply(
                {
                    "model": "fixture-model",
                    "message": {"role": "assistant", "content": json.dumps(result)},
                    "done": True,
                    "done_reason": "stop",
                    "prompt_eval_count": 20,
                    "eval_count": 40,
                }
            )

    provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    processes = []
    temporary = tempfile.TemporaryDirectory(
        prefix="jarvis-smoke-", ignore_cleanup_errors=True
    )
    try:
        with temporary as directory:
            temp = Path(directory)
            port = free_port()
            browser_mode = os.environ.get("JARVIS_SMOKE_BROWSER") == "true"
            web_port = free_port()
            ui_url = f"http://127.0.0.1:{web_port}"
            env = {
                **os.environ,
                "JARVIS_DATABASE_URL": f"sqlite:///{(temp / 'smoke.db').as_posix()}",
                "JARVIS_AUTONOMOUS_WORKER_ENABLED": "false",
                "JARVIS_MODEL_EXECUTION_MODE": "disabled",
                "JARVIS_MODEL_OLLAMA_ENABLED": "false",
                "JARVIS_MODEL_OPENAI_COMPATIBLE_ENABLED": "false",
                "JARVIS_MODEL_ALLOW_REMOTE": "false",
                "JARVIS_AUTO_MIGRATE": "true",
                "WEB_ORIGIN": ui_url,
            }
            setup = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "app.autonomous_worker.setup",
                    "--task-id",
                    "task-demo",
                ],
                cwd=API,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            actor = json.loads(setup.stdout)["actorId"]
            env.update(
                {
                    "JARVIS_AUTONOMOUS_WORKER_ENABLED": "true",
                    "JARVIS_AUTONOMOUS_WORKER_ACTOR_ID": actor,
                    "JARVIS_AUTONOMOUS_WORKER_INSTANCE_ID": "smoke-fixture-worker",
                    "JARVIS_AUTONOMOUS_WORKER_POLL_INTERVAL_MS": "100",
                    "JARVIS_MODEL_EXECUTION_MODE": "local_only",
                    "JARVIS_MODEL_OLLAMA_ENABLED": "true",
                    "JARVIS_MODEL_OLLAMA_BASE_URL": f"http://127.0.0.1:{provider.server_port}",
                    "JARVIS_MODEL_OLLAMA_MODEL": "fixture-model",
                    "JARVIS_MODEL_PROVIDER_PRIORITY": "ollama",
                }
            )
            with (temp / "runtime.log").open(
                "w+", encoding="utf-8", errors="replace"
            ) as logs:
                processes.append(
                    subprocess.Popen(
                        [
                            sys.executable,
                            "-m",
                            "uvicorn",
                            "app.main:app",
                            "--port",
                            str(port),
                        ],
                        cwd=API,
                        env=env,
                        stdout=logs,
                        stderr=logs,
                    )
                )
                deadline = time.monotonic() + 30
                while True:
                    try:
                        with urlopen(
                            f"http://127.0.0.1:{port}/api/health", timeout=1
                        ) as response:
                            assert response.status == 200
                        break
                    except OSError:
                        if (
                            time.monotonic() > deadline
                            or processes[0].poll() is not None
                        ):
                            logs.seek(0)
                            raise RuntimeError(logs.read())
                        time.sleep(0.05)
                processes.append(
                    subprocess.Popen(
                        [sys.executable, "-m", "app.autonomous_worker"],
                        cwd=API,
                        env=env,
                        stdout=logs,
                        stderr=logs,
                    )
                )
                # Transpile the actual UI submission function, preserving its request
                # construction. Only Vite's environment and relative import are bound.
                runner = temp / "submit.cjs"
                runner.write_text("""const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {pathToFileURL} = require('node:url');
const web = process.env.SMOKE_WEB;
const ts = require(path.join(web, 'node_modules/typescript'));
const base = process.env.SMOKE_BASE;
const out = __dirname;
for (const name of ['client', 'planning']) {
 let source = fs.readFileSync(path.join(web, 'src/api', name+'.ts'), 'utf8');
 source = source.replaceAll('import.meta.env.VITE_API_BASE_URL', JSON.stringify(base)).replaceAll('import.meta.env.VITE_WS_URL', 'undefined').replace("'./client'", "'./client.mjs'");
 fs.writeFileSync(path.join(out, name+'.mjs'), ts.transpileModule(source, {compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText);
}
(async()=>{
 const {request} = await import(pathToFileURL(path.join(out,'client.mjs')));
 const {newPlanningSubmission,submitPlanning} = await import(pathToFileURL(path.join(out,'planning.mjs')));
 const task = await request('/api/tasks',{method:'POST',body:JSON.stringify({title:'HTTP smoke planning',description:'Create a bounded plan for validating the local Hub.',priority:'medium'})});
 execFileSync(process.env.SMOKE_PYTHON,['-m','app.autonomous_worker.setup','--task-id',task.id],{cwd:process.env.SMOKE_API,env:process.env,stdio:['ignore','pipe','pipe']});
 const submission = newPlanningSubmission(task,process.env.SMOKE_ACTOR,process.env.SMOKE_ACTOR);
 const queued = await submitPlanning(submission);
 // Lost acknowledgement / repeated click must replay the same durable commands.
 const replay = await submitPlanning(submission);
 if(replay.specification.run_id!==queued.specification.run_id) throw Error('Submission replay created another run');
 const headers={'X-Jarvis-Actor-Id':process.env.SMOKE_ACTOR};
 const deadline=Date.now()+30000;
 let executions=[];
 while(Date.now()<deadline){
  executions=await request('/api/model-executions?taskId='+task.id,{headers});
  if(executions.some(e=>e.stage==='completed'))break;
  if(executions.some(e=>['failed','human_review_required'].includes(e.stage)))throw Error(JSON.stringify(executions));
  await new Promise(resolve=>setTimeout(resolve,100));
 }
 if(executions.length!==1||executions[0].stage!=='completed')throw Error('Execution did not complete once: '+JSON.stringify(executions));
 const final=await request('/api/tasks/'+task.id);
 if(final.status!=='completed'||final.result!=='model-execution:'+executions[0].executionId)throw Error('Task/result not committed');
 console.log(JSON.stringify({taskId:task.id,runId:queued.specification.run_id,executionId:executions[0].executionId,status:final.status,inference:'deterministic HTTP fixture',provider:executions[0].provider,model:executions[0].model}));
})().catch(error=>{console.error(error);process.exitCode=1});
""")
                if browser_mode:
                    processes.append(
                        subprocess.Popen(
                            [
                                "node",
                                str(WEB / "node_modules/vite/bin/vite.js"),
                                "--host",
                                "127.0.0.1",
                                "--port",
                                str(web_port),
                                "--strictPort",
                            ],
                            cwd=WEB,
                            env={
                                **env,
                                "VITE_API_BASE_URL": f"http://127.0.0.1:{port}",
                                "VITE_WS_URL": f"ws://127.0.0.1:{port}/ws/events",
                            },
                            stdout=logs,
                            stderr=logs,
                        )
                    )
                    deadline = time.monotonic() + 30
                    while True:
                        try:
                            with urlopen(ui_url, timeout=1) as response:
                                assert response.status == 200
                            break
                        except OSError:
                            if (
                                time.monotonic() > deadline
                                or processes[-1].poll() is not None
                            ):
                                logs.seek(0)
                                raise RuntimeError(logs.read())
                            time.sleep(0.05)
                    runner = ROOT / (
                        "scripts/smoke-office.cjs"
                        if os.environ.get("JARVIS_SMOKE_OFFICE") == "true"
                        else "scripts/smoke-browser.cjs"
                    )
                completed = subprocess.run(
                    ["node", str(runner)],
                    env={
                        **env,
                        "SMOKE_WEB": str(WEB),
                        "SMOKE_BASE": f"http://127.0.0.1:{port}",
                        "SMOKE_ACTOR": actor,
                        "SMOKE_API": str(API),
                        "SMOKE_PYTHON": sys.executable,
                        "SMOKE_UI": ui_url,
                        "SMOKE_ARTIFACT_DIR": os.environ.get(
                            "SMOKE_ARTIFACT_DIR", str(temp / "screenshots")
                        ),
                    },
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=180 if browser_mode else 60,
                    check=False,
                )
                if completed.returncode:
                    logs.seek(0)
                    raise RuntimeError(
                        (completed.stdout or "")
                        + (completed.stderr or "")
                        + logs.read()
                    )
                print(completed.stdout.strip())
                expected_calls = 2 if browser_mode else 1
                assert len(calls) == expected_calls, (
                    f"Expected {expected_calls} inference requests, got {len(calls)}"
                )
                print(
                    "PASS: API + separate worker + frontend command replay + durable reviewed result"
                )
                for process in reversed(processes):
                    process.terminate()
                for process in reversed(processes):
                    process.wait(timeout=10)
                processes.clear()
    finally:
        for process in reversed(processes):
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)
        # The context may have deferred cleanup while child processes still held
        # Windows file handles. Retry after every child has exited.
        temporary.cleanup()


if __name__ == "__main__":
    main()
