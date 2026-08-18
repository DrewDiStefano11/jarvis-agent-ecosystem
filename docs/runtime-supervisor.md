# Windows-first runtime supervisor

The runtime supervisor keeps the current local Jarvis control plane available without putting
process-management logic inside FastAPI. It is an infrastructure process with explicit ownership
of the API, the built web UI, and—only when existing validated configuration enables it—the
autonomous planning/review worker.

It does not add a provider router, cloud models, remote access, coding tools, automatic Git updates,
deployment rollback, a development scheduler, or any other later-roadmap capability.

## Architecture and ownership

```text
Windows Task Scheduler (optional, current-user logon)
  -> Jarvis runtime supervisor
       -> FastAPI / Uvicorn on 127.0.0.1
       -> Vite preview for the already-built web UI on 127.0.0.1
       -> autonomous planning/review worker (explicit opt-in only)
       -> configured Ollama availability probe (never owns Ollama)
```

The supervisor is a separate Python process under `app.runtime_supervisor`; FastAPI does not restart
itself. Process definitions are a small registry, so a future infrastructure PR can add another
owned process without changing the monitoring loop. This PR registers no future component.

One lifetime-held operating-system file lock owns an installation. The state file supplements that
lock with a random instance ID, PID, and operating-system process creation identity. Status therefore
distinguishes running, stale, and not-running state without treating a PID file as authority. A stop
request is addressed to the current random instance ID; stale state never causes an unrelated reused
PID to be terminated.

On Windows, the detached supervisor allocates a hidden console for process-group shutdown signals,
and children enter a kill-on-close Job Object. Normal stop is still graceful: the supervisor signals
worker, web, and API in reverse order, waits the configured grace period, and forces only a child it
directly created if the grace period expires. Closing the Job Object is a final orphan safety net if
the supervisor itself crashes.

## Prerequisites and stable setup

Complete the repository's **Fresh Windows setup** first. Supervised operation also requires a current
frontend build:

```powershell
Set-Location apps\web
pnpm install --frozen-lockfile
pnpm build
Set-Location ..\..
```

The supervisor uses the repository virtual environment at
`apps\api\.venv\Scripts\python.exe`, the installed Vite JavaScript entry point, and the system Node
executable. It runs Uvicorn without `--reload` and serves the built `dist` tree with Vite's local-only
preview server. Vite preview is the smallest supported fit for the current frontend toolchain; it is
not presented as an internet-facing production server.

Run doctor before the first start:

```powershell
.\scripts\jarvis.ps1 doctor
```

Doctor is read-only. It checks the OS, repository layout, Python virtual environment, Node, frontend
dependencies/build, database location, runtime-home ownership/writability, configured ports,
loopback bindings, worker configuration, relevant Ollama availability, Git SHA/worktree state, disk
space, and Task Scheduler availability/registration. It does not install packages, build the web UI,
create a database, change ports, or register startup.

## Operator commands

```powershell
.\scripts\jarvis.ps1 start
.\scripts\jarvis.ps1 stop
.\scripts\jarvis.ps1 restart
.\scripts\jarvis.ps1 status
.\scripts\jarvis.ps1 doctor
.\scripts\jarvis.ps1 backup
```

Append `--json` to `status` or `doctor` for machine-readable output. Start and stop are idempotent.
Restart refuses to start a replacement if the prior supervisor has not confirmed shutdown.

Status includes the supervisor identity/state, repository, supervised Git SHA, runtime home, uptime,
process PID/state/health/restart history, API application health, web availability, worker
enablement/health, relevant Ollama dependency health, emergency-stop state, database backup metadata,
disk state, application clean-shutdown metadata, supervisor clean shutdown, and known-good metadata.

## Deterministic startup and health

Startup order is:

1. inspect configured local dependencies, including Ollama only when its existing provider setting is
   enabled;
2. start API and wait a bounded time for `/api/health`;
3. start the web preview and wait for its loopback HTTP endpoint;
4. start the autonomous worker only if `JARVIS_AUTONOMOUS_WORKER_ENABLED=true` and the existing
   local-only actor/instance configuration is complete.

The supervisor distinguishes an alive process, an available endpoint, the application's authoritative
`healthy`/`degraded` response, and failure. Application-level degraded state does not by itself restart
FastAPI: recovery-required state, emergency stop, a stale worker, or another authoritative application
condition must remain inspectable. A crashed child is restarted. An unavailable required endpoint is
restarted after three consecutive failed probes by default.

Each failure increments a bounded 20-entry history and schedules exponential backoff. Defaults begin
at one second and cap at five minutes. A process that remains alive for five minutes resets its
consecutive-failure backoff. Repeated failures remain visible as degraded/failed and retries never
become a hot loop.

The known-good record contains the repository, startup timestamp, current SHA, successful health
timestamp, and last-known-healthy SHA. It is metadata only: the supervisor never changes branches,
pulls code, deploys an update, or performs rollback.

## Emergency stop and autonomous worker

The application's durable emergency-stop flag remains the only source of truth. The supervisor
reports it and keeps API/web infrastructure alive so the operator can inspect and resume Jarvis. It
does not clear the flag or dispatch work. The autonomous worker already refuses acquisition, model
calls, result commit, and terminal completion while emergency stop is active.

The supervisor cannot enable the worker. With the default
`JARVIS_AUTONOMOUS_WORKER_ENABLED=false`, the worker process definition is intentionally disabled.
When explicitly enabled, existing application validation still requires local-only model mode, a
worker actor, a stable instance ID, safe lease/heartbeat values, and a structurally loopback provider.

Ollama remains independently managed by its Windows application or operator. The supervisor probes
the configured loopback URL only when Ollama is enabled, reports unavailable state, and never starts,
kills, reconfigures, or competes with an Ollama process.

## Runtime home, logs, and disk safety

By default, supervisor-owned state lives at:

```text
%LOCALAPPDATA%\Jarvis\Supervisor\<installation-hash>
```

`JARVIS_SUPERVISOR_RUNTIME_HOME` may set another absolute local directory. A runtime home cannot be a
filesystem root, the repository, or a directory inside the repository. A repository-identity marker
prevents cleanup from operating on another installation's directory.

Files include:

- `state.json`, `known-good.json`, and the lifetime lock;
- `logs\supervisor.log` for supervisor events;
- `logs\api.log`, `logs\web.log`, and `logs\autonomous_worker.log` for child output;
- `backups\jarvis-<UTC timestamp>.sqlite3` and matching JSON manifests;
- `last-backup.json`.

Logs are UTF-8 and rotate by size. Defaults are 5 MiB per file plus five retained rotations. Child
lines are separated from supervisor events and configured values whose names look like credentials,
tokens, passwords, API keys, or secrets are redacted if they appear in child output. The supervisor
never prints or serializes its environment.

Status and doctor report disk-free bytes. Backup creation fails before opening a destination if free
space is below the critical threshold or too small for a conservative source-size allowance. Cleanup
is non-recursive and can remove only filename-validated supervisor backup files inside a
marker-validated runtime home. It never cleans repository files, worktrees, or general user data.

## SQLite backup behavior

`backup` uses Python SQLite's online backup API, so it produces a consistent database while FastAPI
uses WAL. The database is written to a `.partial` file, checked with `PRAGMA quick_check`, and only
then atomically published. An interruption cannot create a valid-looking final backup.

The manifest records timestamp, authoritative source path, repository/SHA, Alembic revision when
available, size, and SHA-256. The newest seven backups are retained by default. The supervisor also
runs this same operation at a conservative 24-hour default interval; set the interval to `0` to keep
only the explicit command. Failures are logged and exposed by the last successful backup metadata.

Restore is deliberately manual and is not performed by this supervisor. Stop Jarvis, preserve the
current database and WAL/SHM sidecars, verify the chosen backup hash and integrity, and follow the
repository migration/recovery guidance before replacing any data.

## Current-user logon auto-start

Install, inspect, or remove the optional scheduled task:

```powershell
.\scripts\jarvis.ps1 autostart install
.\scripts\jarvis.ps1 autostart status
.\scripts\jarvis.ps1 autostart uninstall
```

The idempotent task runs with the current interactive user's token and least privilege. It stores no
Windows password. Its executable, working directory, and quoted repository path point to the actual
installation. Multiple task instances are ignored, and the supervisor lock remains the final
singleton authority.

This is a **user-logon** trigger. It does not start before login and does not claim machine-service
availability. Installation normally needs no administrator rights. Organization policy can disable
Task Scheduler registration; doctor/status report that condition without changing policy.

## Configuration

The safe defaults in `.env.example` cover health cadence, health-failure threshold, graceful timeout,
restart/backoff bounds, log size/retention, backup interval/retention, disk thresholds, and the web
loopback port. Keep overrides in an untracked repository/app `.env` or the process environment.

The supervisor rejects non-loopback API, web, and Ollama URLs before launching children. Its web
origin and Vite API/WebSocket endpoints must also match the exact host and port of the processes it
owns; omitted frontend endpoints are derived from those bind settings. Commands are fixed argument
lists with `shell=False`; configuration is never concatenated into a shell command. Do not place
credentials in supervisor settings or its runtime home.

## Recovery and limitations

If a child crashes, use `status` to see its exit code, last failure, restart count/history, next retry,
and health. If the supervisor itself fails, Task Scheduler starts it at the next user logon; otherwise
run `start` manually. A stale state is recoverable because a new daemon must first acquire the OS lock.
The operator command will not kill a stale PID. Inspect `logs\supervisor.log` and Task Manager before
manually stopping any process whose ownership cannot be proven.

Limitations:

- Windows auto-start is user-logon, not a machine boot service.
- Vite preview serves the already-built local UI; source changes require an explicit new `pnpm build`.
- The supervisor monitors but does not own Ollama.
- It does not automatically restore a database, update Git, change branches, or roll back code.
- It preserves the current one-local-API/SQLite deployment boundary and adds no authentication or
  network exposure.
