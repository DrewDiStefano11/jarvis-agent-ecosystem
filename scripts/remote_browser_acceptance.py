"""Explicit actual-process/TLS/Playwright acceptance; requires a built remote UI."""

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(ROOT / "apps" / "api"))

pytest_plugins = ["tests.test_remote_browser"]


@pytest.mark.parametrize("supervised", [False, True], ids=["standalone", "supervised"])
def test_real_tls_process_and_browser_operate_native_goals(
    browser_http, monkeypatch, tmp_path, supervised
):
    """Actual TLS, built React UI, native SQLite/outbox and a distinct API process."""

    import json
    import os
    import shutil
    import socket
    import ssl
    import subprocess
    import sys
    import time
    from pathlib import Path

    import httpx
    from tests.test_remote_control_access import TOKEN
    from tests.test_remote_system_control import grant_system

    configured, actor, fixture_client, fixture_bearer = browser_http

    grant_system(configured, actor)

    from app.models.identity import AssignPermissionRequest, CreatePermissionRequest

    permission = configured.state.identity_service.create_definition(
        "permission",
        CreatePermissionRequest(
            stable_key="remote.runtime",
            display_name="Fixture system monitoring",
            resource_type="administrative_function",
            action="remote_runtime",
        ),
    )

    configured.state.identity_service.assign_permission(
        actor.actor_id,
        AssignPermissionRequest(
            permission_id=permission.id,
            effect="allow",
            resource_type="administrative_function",
            resource_id="remote_control",
        ),
    )

    # A fixture owner prepares one paused native run; the browser cannot forge

    # worker acknowledgement. This exercises metadata approval, not source execution.

    from app.models.agent_runtime import (
        ConfirmPauseCommand,
        CreateAgentRunCommand,
        QueueAgentRunCommand,
        RequestPauseCommand,
    )
    from tests.agent_runtime_testkit import make_spec, ts
    from tests.test_agent_runtime_authorization import create_actor
    from tests.test_remote_goal_submission import grant_task_permission

    created = fixture_client.post(
        "/api/remote/goals",
        json={
            "title": "Runtime control fixture",
            "description": "Native paused run and exact reservation review",
        },
        headers=fixture_bearer | {"Idempotency-Key": "browser-runtime-fixture"},
    )

    assert created.status_code == 201, created.text

    task_id = created.json()["data"]["id"]

    owner_id = create_actor(configured, "browser-fixture-builder")

    runtime = configured.state.agent_runtime_service

    owner = runtime.authenticate_actor(owner_id)

    for permission_key in (
        "runtime.create",
        "runtime.queue",
        "runtime.pause",
        "runtime.execute",
        "runtime.read",
    ):
        grant_task_permission(configured, owner, permission_key, task_id)

    for permission_key in ("runtime.pause", "runtime.execute"):
        grant_task_permission(configured, actor, permission_key, task_id)

    run_id = "run-browser-control"

    runtime.handle_authorized(
        CreateAgentRunCommand(
            specification=make_spec(agent_id=owner_id, task_id=task_id, run_id=run_id),
            command_id="fixture-create",
            expected_run_version=0,
            timestamp=ts(0),
        ),
        owner,
    )

    runtime.handle_authorized(
        QueueAgentRunCommand(
            run_id=run_id,
            command_id="fixture-queue",
            expected_run_version=1,
            timestamp=ts(1),
        ),
        owner,
    )

    runtime.handle_authorized(
        RequestPauseCommand(
            run_id=run_id,
            command_id="fixture-pause",
            expected_run_version=2,
            timestamp=ts(2),
            reason_code="operator_request",
            detail="Fixture pause",
        ),
        owner,
    )

    runtime.handle_authorized(
        ConfirmPauseCommand(
            run_id=run_id,
            command_id="fixture-worker-confirm",
            expected_run_version=3,
            timestamp=ts(3),
        ),
        owner,
    )

    permission = configured.state.identity_service.create_definition(
        "permission",
        CreatePermissionRequest(
            stable_key="self_build.workspace",
            display_name="Fixture reservation approval",
            resource_type="task",
            action="workspace",
        ),
    )

    configured.state.identity_service.assign_permission(
        actor.actor_id,
        AssignPermissionRequest(
            permission_id=permission.id,
            effect="allow",
            resource_type="task",
            resource_id=task_id,
        ),
    )

    primary, worktrees = tmp_path / "primary", tmp_path / "worktrees"

    primary.mkdir()
    worktrees.mkdir()

    repository_policy = {
        "browser-fixture": {
            "repository_identity": "github.com/example/jarvis",
            "primary_root": str(primary),
            "worktree_root": str(worktrees),
            "base_branch": "main",
        }
    }

    root = Path(__file__).resolve().parents[1]

    web = root / "apps" / "web"

    assert (web / "dist" / "remote.html").is_file(), (
        "Build the web UI before browser acceptance"
    )

    openssl = shutil.which("openssl") or "C:/Program Files/Git/usr/bin/openssl.exe"

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

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))

        port = listener.getsockname()[1]

    origin = f"https://127.0.0.1:{port}"

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        local_port = listener.getsockname()[1]

    env = {
        **os.environ,
        "JARVIS_REMOTE_ORIGIN": origin,
        "JARVIS_REMOTE_BROWSER_ASSETS": str(web / "dist"),
        "JARVIS_DATABASE_URL": configured.state.settings.database_url,
        "JARVIS_REMOTE_REQUESTS_PER_MINUTE": "600",
        "JARVIS_MODEL_LOCAL_ENABLED": "false",
        "JARVIS_SELF_BUILD_ENABLED": "true",
        "JARVIS_SELF_BUILD_REPOSITORIES_JSON": json.dumps(repository_policy),
        "PYTHONPATH": str(root / "apps" / "api"),
        "JARVIS_SUPERVISOR_REMOTE_ENABLED": str(supervised).lower(),
        "JARVIS_SUPERVISOR_REMOTE_HOST": "127.0.0.1",
        "JARVIS_SUPERVISOR_REMOTE_CERTIFICATE": str(certificate),
        "JARVIS_SUPERVISOR_REMOTE_PRIVATE_KEY": str(key),
        "JARVIS_SUPERVISOR_REMOTE_CA_FILE": str(certificate),
    }

    script = tmp_path / "browser.cjs"

    script.write_text(
        """

const assert = require('node:assert/strict')

const { chromium } = require(process.env.JARVIS_BROWSER_MODULE)

;(async () => {

 const browser = await chromium.launch({headless:true})

 try {

  const context = await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1280,height:900}})

  const page = await context.newPage(), errors = [], routes = []

  page.on('pageerror', error => errors.push(error.message))

  page.on('request', request => { if (request.resourceType() === 'fetch') routes.push(new URL(request.url()).pathname) })

  page.on('dialog', dialog => dialog.accept())

  await page.goto(process.env.JARVIS_BROWSER_ORIGIN + '/remote')

  await page.getByLabel('Operator credential').fill(process.env.JARVIS_BROWSER_FIXTURE_TOKEN)

  await page.getByRole('button',{name:'Sign in',exact:true}).click()

  await page.getByRole('heading',{name:'Submit a goal'}).waitFor()

  assert.equal(await page.evaluate(() => Object.keys(localStorage).length + Object.keys(sessionStorage).length),0)

  assert.equal(await page.evaluate(() => document.cookie.includes('__Host-jarvis-session')),false)

  const cookie = (await context.cookies()).find(item => item.name === '__Host-jarvis-session')

  assert.ok(cookie && cookie.secure && cookie.httpOnly && cookie.sameSite === 'Strict')

  await page.getByText('Seeded demonstration',{exact:true}).first().waitFor()
  assert.equal(await page.getByText('Seeded demonstration',{exact:true}).count(),4)
  await page.getByRole('button',{name:/Organize reading list/}).click()
  await page.getByText('This built-in demonstration record is not evidence of a completed operator objective.',{exact:true}).waitFor()

  await page.getByLabel('Goal title',{exact:true}).fill('Useful TLS browser objective')

  await page.getByLabel('Objective',{exact:true}).fill('Record and inspect this authorized goal through the real native API.')

  await page.getByRole('button',{name:'Record goal',exact:true}).click()

  await page.getByText(/Goal recorded: task-/).waitFor()

  await page.getByRole('heading',{name:'Useful TLS browser objective',exact:true}).waitFor()

  await page.getByText('No completed result recorded.',{exact:true}).waitFor()
  assert.equal(await page.getByText('This built-in demonstration record is not evidence of a completed operator objective.',{exact:true}).count(),0)

  await page.reload()

  await page.getByRole('heading',{name:'Submit a goal'}).waitFor()

  await page.getByRole('button',{name:/Useful TLS browser objective/}).click()

  await page.getByRole('heading',{name:'Useful TLS browser objective',exact:true}).waitFor()

  await page.getByRole('button',{name:'Emergency stop',exact:true}).click()

  await page.getByText('Emergency stop active',{exact:true}).waitFor()

  await page.getByRole('button',{name:'Resume system',exact:true}).click()

  await page.getByText('Emergency stop clear',{exact:true}).waitFor()

  await page.getByRole('button',{name:'Cancel goal',exact:true}).click()

  await page.getByRole('button',{name:/Useful TLS browser objective.*cancelled/}).waitFor()

  await page.getByRole('button',{name:/Runtime control fixture/}).click()

  const run = page.getByRole('listitem',{name:'Runtime run run-browser-control',exact:true})

  await run.getByText('paused',{exact:true}).waitFor()

  await run.getByRole('button',{name:'Resume run',exact:true}).click()

  await run.getByText('queued',{exact:true}).waitFor()

  await run.getByRole('button',{name:'Request pause',exact:true}).click()

  await run.getByText('pause_requested',{exact:true}).waitFor()

  await run.getByRole('button',{name:'Inspect run output',exact:true}).click()

  await page.getByText('No model execution result recorded.',{exact:true}).waitFor()

  await page.getByLabel('Registered repository ID',{exact:true}).fill('browser-fixture')

  await page.getByLabel('Workspace runtime run ID',{exact:true}).fill('run-browser-control')

  await page.getByLabel('Base commit SHA',{exact:true}).fill('a'.repeat(40))

  const planResponse = page.waitForResponse(response => new URL(response.url()).pathname === '/api/remote/self-build/workspaces/preview')

  await page.getByRole('button',{name:'Preview workspace plan',exact:true}).click()

  const plan = (await (await planResponse).json()).data

  await page.getByRole('heading',{name:'Native plan · review every field',exact:true}).waitFor()

  await page.getByLabel('Retype the exact plan hash',{exact:true}).fill(plan.plan_hash)

  await page.getByRole('button',{name:'Approve workspace reservation',exact:true}).click()

  await page.getByText(/Workspace reservation approval recorded:/).waitFor()

  for (const width of [1280,390,320]) {

   await page.setViewportSize({width,height:900})

   assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'horizontal overflow at '+width)

   await page.screenshot({path:process.env.JARVIS_BROWSER_OUTPUT+'/'+width+'.png',fullPage:true})
   await page.getByRole('heading',{name:'Runtime and protected actions',exact:true}).locator('..').screenshot({path:process.env.JARVIS_BROWSER_OUTPUT+'/runtime-'+width+'.png'})

  }

  await page.getByRole('button',{name:'Sign out',exact:true}).click()

  await page.getByRole('button',{name:'Sign in',exact:true}).waitFor()

  await page.reload()

  await page.getByRole('button',{name:'Sign in',exact:true}).waitFor()

  assert.ok(routes.every(path=>path.startsWith('/api/remote/')),JSON.stringify(routes))

  assert.deepEqual(errors,[])

  console.log(JSON.stringify({status:'PASS',nativeGoal:true,restore:true,stop:true,resume:true,cancel:true,

    logout:true,runtimePause:true,runtimeResume:true,workspaceApproval:true,seededDemonstrationsLabeled:true,workerAcknowledgement:'native fixture',viewports:[1280,390,320],pageErrors:errors.length,requests:routes.length}))

 } finally { await browser.close() }

})().catch(error => { console.error(error); process.exitCode=1 })

""",
        encoding="utf-8",
    )

    report_directory = Path(
        os.environ.get("JARVIS_REMOTE_BROWSER_REPORT_DIR", str(tmp_path))
    ) / ("supervised" if supervised else "standalone")

    report_directory.mkdir(parents=True, exist_ok=True)

    log_path = tmp_path / "api.log"

    with log_path.open("w", encoding="utf-8") as log:
        command = (
            [
                sys.executable,
                "-m",
                "app.runtime_supervisor.api_child",
                "--host",
                "127.0.0.1",
                "--port",
                str(local_port),
            ]
            if supervised
            else [
                sys.executable,
                "-m",
                "app.remote_control",
                "--port",
                str(port),
                "--cert",
                str(certificate),
                "--key",
                str(key),
            ]
        )
        supervisor = None
        if supervised:
            from dataclasses import replace

            from app.runtime_supervisor.remote_listener import RemoteListener
            from app.runtime_supervisor.supervisor import RuntimeSupervisor
            from tests.test_runtime_supervisor import attach_logger, make_config

            config = make_config(
                tmp_path / "supervisor",
                **{
                    key: value
                    for key, value in env.items()
                    if key.startswith(("JARVIS_REMOTE_", "JARVIS_SUPERVISOR_REMOTE_"))
                },
            )
            config = replace(
                config,
                repository=root,
                environment=env,
                api_port=local_port,
                api_url=f"http://127.0.0.1:{local_port}",
                restart_initial_seconds=0.1,
                remote_listener=RemoteListener.load(env, local_ports={local_port}),
            )
            config.logs_directory.mkdir(parents=True)

            def factory(argv, **kwargs):
                kwargs["stdout"] = log
                kwargs["stderr"] = log
                return subprocess.Popen(argv, **kwargs)

            supervisor = RuntimeSupervisor(config, process_factory=factory)
            attach_logger(supervisor)
            for managed in supervisor.processes.values():
                if managed.definition.name != "api":
                    managed.definition = replace(
                        managed.definition, enabled=False, required=False
                    )
            supervisor._start_process(supervisor.processes["api"])
            process = supervisor.processes["api"].process
            assert process is not None
        else:
            process = subprocess.Popen(
                command,
                cwd=root / "apps" / "api",
                env=env,
                stdout=log,
                stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )

        try:
            context = ssl.create_default_context(cafile=str(certificate))

            with httpx.Client(
                base_url=origin, verify=context, trust_env=False, timeout=1
            ) as client:
                deadline = time.monotonic() + 15

                while time.monotonic() < deadline:
                    assert process.poll() is None, log_path.read_text()

                    try:
                        if client.get("/remote").status_code == 200:
                            break

                    except httpx.HTTPError:
                        pass

                    time.sleep(0.05)

                else:
                    pytest.fail("TLS API did not start: " + log_path.read_text())

                assert client.get("/api/tasks").status_code == 403

                if supervised:
                    from app.runtime_supervisor.remote_listener import (
                        RemoteListener,
                        probe_remote,
                    )
                    from app.runtime_supervisor.supervisor import process_owns_port

                    listener_config = RemoteListener.load(env, local_ports={local_port})
                    assert probe_remote(listener_config).available
                    assert process_owns_port(process.pid, port)
                    assert process_owns_port(process.pid, local_port)
                    with httpx.Client(trust_env=False) as local:
                        health = local.get(f"http://127.0.0.1:{local_port}/api/health")
                        assert health.status_code == 200, health.text
                        blocked = local.get(
                            f"http://127.0.0.1:{local_port}/api/remote/goals"
                        )
                        assert blocked.status_code == 403, blocked.text

            browser_env = {
                **env,
                "JARVIS_BROWSER_MODULE": str(web / "node_modules" / "playwright"),
                "JARVIS_BROWSER_ORIGIN": origin,
                "JARVIS_BROWSER_FIXTURE_TOKEN": TOKEN,
                "JARVIS_BROWSER_OUTPUT": str(report_directory),
            }

            completed = subprocess.run(
                ["node", str(script)],
                cwd=web,
                env=browser_env,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )

            assert completed.returncode == 0, completed.stdout + completed.stderr

            result = json.loads(completed.stdout.strip())
            result["supervisedSharedApplication"] = supervised

            assert result["status"] == "PASS" and result["requests"] > 10

            assert (
                result["runtimePause"]
                and result["runtimeResume"]
                and result["workspaceApproval"]
            )

            assert list(worktrees.iterdir()) == []

            if supervised:
                managed = supervisor.processes["api"]
                assert supervisor._probe_managed(managed).available
                old_pid = process.pid
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(old_pid), "/T", "/F"],
                        capture_output=True,
                        check=True,
                        timeout=10,
                    )
                else:
                    process.kill()
                process.wait(timeout=10)
                supervisor._monitor_once()
                assert managed.restart_count == 1
                time.sleep(0.12)
                supervisor._monitor_once()
                process = managed.process
                assert process is not None and process.pid != old_pid
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    assert process.poll() is None, log_path.read_text(encoding="utf-8")
                    if supervisor._probe_managed(managed).available:
                        break
                    time.sleep(0.05)
                else:
                    pytest.fail("Owned API/TLS restart failed")
                with httpx.Client(
                    base_url=origin, verify=context, trust_env=False
                ) as restored:
                    goals = restored.get("/api/remote/goals", headers=fixture_bearer)
                    assert goals.status_code == 200, goals.text
                    assert "Useful TLS browser objective" in goals.text
                    assert restored.get("/api/tasks").status_code == 403
                result["supervisorRestart"] = True

            (report_directory / "browser-report.json").write_text(
                json.dumps(result, indent=2)
            )

        finally:
            if process.poll() is None:
                # Stop only this test-owned redirector/process tree on Windows.

                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        capture_output=True,
                        timeout=10,
                        check=False,
                    )

                else:
                    process.terminate()

                process.wait(timeout=10)


if __name__ == "__main__":
    os.chdir(ROOT / "apps" / "api")

    raise SystemExit(pytest.main([str(Path(__file__).resolve()), *sys.argv[1:]]))
