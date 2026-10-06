"""Actual direct-TLS listener and HTTP client, using temporary fixture credentials."""

import json
import shutil
import socket
import ssl
import subprocess
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

from app.main import create_app
from app.models.agent_runtime import (
    ConfirmPauseCommand,
    CreateAgentRunCommand,
    QueueAgentRunCommand,
    RequestCancellationCommand,
    RequestPauseCommand,
    ResumeAgentRunCommand,
)
from app.remote_control.client import main as remote_client
from tests.agent_runtime_testkit import make_spec, ts
from tests.test_remote_goal_submission import grant_task_permission
from tests.test_remote_system_control import grant_system

pytest_plugins = ["tests.test_remote_http"]


def test_real_tls_remote_submission_transport_and_legacy_isolation(
    remote_http, monkeypatch, tmp_path, capsys
):
    configured, actor, _, headers = remote_http
    openssl = shutil.which("openssl")
    if openssl is None:
        bundled = Path("C:/Program Files/Git/usr/bin/openssl.exe")
        assert bundled.is_file(), "TLS acceptance requires the installed OpenSSL fixture tool"
        openssl = str(bundled)
    certificate, key = tmp_path / "fixture-cert.pem", tmp_path / "fixture-key.pem"
    subprocess.run(
        [
            openssl,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(certificate),
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=IP:127.0.0.1,DNS:localhost",
        ],
        check=True,
        capture_output=True,
        timeout=15,
    )
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    monkeypatch.setenv("JARVIS_REMOTE_ORIGIN", f"https://127.0.0.1:{port}")
    app = create_app(database_url=configured.state.settings.database_url)
    grant_system(app, actor)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            ssl_certfile=str(certificate),
            ssl_keyfile=str(key),
            proxy_headers=False,
            forwarded_allow_ips="",
            access_log=False,
            log_level="warning",
            limit_concurrency=16,
        )
    )
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started, "TLS fixture listener did not start"
        context = ssl.create_default_context(cafile=str(certificate))
        with httpx.Client(
            base_url=f"https://127.0.0.1:{port}",
            verify=context,
            trust_env=False,
            timeout=5,
        ) as client:
            assert client.get("/api/remote/goals").status_code == 401
            assert client.get("/api/identity/agents", headers=headers).status_code == 403
            assert (
                client.get(
                    "/api/remote/goals", headers=headers | {"X-Forwarded-Proto": "https"}
                ).status_code
                == 403
            )
            assert (
                client.get(
                    "/api/remote/goals", headers=headers | {"X-Jarvis-Actor-Id": "spoofed"}
                ).status_code
                == 403
            )
            created = client.post(
                "/api/remote/goals",
                json={
                    "title": "Actual TLS objective",
                    "description": "Persist through verified TLS transport",
                },
                headers=headers | {"Idempotency-Key": "tls-1"},
            )
            assert created.status_code == 201, created.text
            task = created.json()["data"]
            assert task["createdBy"] == actor.actor_id
            inspected = client.get("/api/remote/goals/" + task["id"], headers=headers)
            assert inspected.status_code == 200 and inspected.json()["data"] == task
            assert (
                remote_client(
                    [
                        "--origin",
                        f"https://127.0.0.1:{port}",
                        "--ca",
                        str(certificate),
                        "goal",
                        task["id"],
                    ]
                )
                == 0
            )
            output = capsys.readouterr().out
            assert "Actual TLS objective" in output and headers["Authorization"] not in output
            stopped = client.post("/api/remote/system/emergency-stop", headers=headers)
            assert stopped.status_code == 200 and stopped.json()["data"]["emergencyStop"]
            resumed = client.post("/api/remote/system/resume", headers=headers)
            assert resumed.status_code == 200 and not resumed.json()["data"]["emergencyStop"]
            for permission in ["runtime.create", "runtime.queue", "runtime.pause"]:
                grant_task_permission(app, actor, permission, task["id"])
            runtime = app.state.agent_runtime_service
            run_id = "run-tls-control"
            # Local admission/worker acknowledgement remain native, not remote powers.
            runtime.handle_authorized(
                CreateAgentRunCommand(
                    specification=make_spec(task_id=task["id"], run_id=run_id),
                    command_id="tls-local-create",
                    expected_run_version=0,
                    timestamp=ts(0),
                ),
                actor,
            )
            runtime.handle_authorized(
                QueueAgentRunCommand(
                    run_id=run_id,
                    command_id="tls-local-queue",
                    expected_run_version=1,
                    timestamp=ts(1),
                ),
                actor,
            )
            pause = RequestPauseCommand(
                run_id=run_id,
                command_id="tls-pause",
                expected_run_version=2,
                timestamp=ts(2),
                reason_code="operator_request",
                detail="Pause through verified TLS",
            ).model_dump(mode="json")
            command_file = tmp_path / "operator-pause.json"
            command_file.write_text(json.dumps(pause), encoding="utf-8")
            assert (
                remote_client(
                    [
                        "--origin",
                        f"https://127.0.0.1:{port}",
                        "--ca",
                        str(certificate),
                        "command",
                        "--file",
                        str(command_file),
                    ]
                )
                == 0
            )
            command_output = capsys.readouterr().out
            assert "pause_requested" in command_output
            assert headers["Authorization"] not in command_output
            requested = client.post("/api/remote/runtime/commands", json=pause, headers=headers)
            assert requested.status_code == 200, requested.text
            assert requested.json()["data"]["snapshot"]["state"] == "pause_requested"
            assert requested.json()["data"]["idempotent_replay"]
            replay = client.post("/api/remote/runtime/commands", json=pause, headers=headers)
            assert replay.status_code == 200 and replay.json()["data"]["idempotent_replay"]
            confirmation = ConfirmPauseCommand(
                run_id=run_id,
                command_id="tls-local-confirm",
                expected_run_version=3,
                timestamp=ts(3),
            )
            forbidden = client.post(
                "/api/remote/runtime/commands",
                json=confirmation.model_dump(mode="json"),
                headers=headers,
            )
            assert forbidden.status_code == 422
            runtime.handle_authorized(confirmation, actor)
            resume = ResumeAgentRunCommand(
                run_id=run_id,
                command_id="tls-resume",
                expected_run_version=4,
                timestamp=ts(4),
            )
            continued = client.post(
                "/api/remote/runtime/commands", json=resume.model_dump(mode="json"), headers=headers
            )
            assert continued.status_code == 200, continued.text
            assert continued.json()["data"]["snapshot"]["state"] == "queued"
            cancellation = RequestCancellationCommand(
                run_id=run_id,
                command_id="tls-cancel",
                expected_run_version=5,
                timestamp=ts(5),
                reason_code="operator_request",
                detail="Cancel through verified TLS",
                requester_reference=actor.actor_id,
            )
            cancelled_run = client.post(
                "/api/remote/runtime/commands",
                json=cancellation.model_dump(mode="json"),
                headers=headers,
            )
            assert cancelled_run.status_code == 200, cancelled_run.text
            assert cancelled_run.json()["data"]["snapshot"]["state"] == "cancelled"
            cancelled = client.post("/api/remote/goals/" + task["id"] + "/cancel", headers=headers)
            assert (
                cancelled.status_code == 200 and cancelled.json()["data"]["status"] == "cancelled"
            )
        with httpx.Client(trust_env=False, timeout=2) as plaintext:
            with pytest.raises(httpx.TransportError):
                plaintext.get(f"http://127.0.0.1:{port}/api/remote/goals", headers=headers)
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        assert not thread.is_alive(), "TLS fixture failed to shut down"
