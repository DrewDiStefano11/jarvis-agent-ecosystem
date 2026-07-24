# Phase 2B Worker Supervisor Prototype

This opt-in prototype validates reliable local Windows process supervision for a future Jarvis worker manager. It starts, observes, stops, restarts, and recovers one bundled deterministic worker process.

It is ready for engineering review as a supervisor candidate, but it is not enabled by the application and is not the Phase 2B task runtime.

## Ownership boundary

The Phase 2A control plane remains authoritative for:

- task intake, validation, queueing, and status;
- workflow runs and validated checkpoints;
- append-only domain audit records;
- idempotency claims and command transactions;
- transactional outbox envelopes and event publication;
- API contracts and health endpoints.

The prototype owns only:

- OS-process launch and verified termination;
- supervisor lease ownership;
- readiness and heartbeat observation;
- restart, backoff, and crash-loop policy;
- prototype-local lifecycle events, logs, and counters.

Its separate SQLite database must never contain domain tasks or replace the application repository. Integrating a future worker requires an explicit adapter to the existing execution pipeline and unit-of-work/outbox boundary.

The launcher accepts no executable, command string, shell fragment, working directory, model output, or task payload. It always invokes `sys.executable -m jarvis_simulated_worker` with `shell=False`.

## Lifecycle

### Startup

1. `init` creates the runtime directories and upgrades the operational database to Alembic revision `20260724_01`.
2. `run` verifies the existing revision; normal startup never creates schema.
3. A unique owner ID, process token, PID, and expiring lease prevent duplicate supervisors.
4. Recovery reconciles the persisted worker using its instance ID, token, PID, and OS creation time.
5. If no valid worker exists and launch is allowed, the supervisor starts the fixed simulated worker.
6. Readiness must arrive before its deadline. Monotonic heartbeats then determine health.

### Normal operation

The watchdog renews its lease, enforces log limits, observes process identity, consumes only current readiness and heartbeat files, and persists each lifecycle transition. Stable runtime resets the restart window. Failures use bounded exponential backoff; repeated failures enter `crash_loop` until explicitly reset.

Lifecycle events in the prototype database are operational records only. They are not domain audits and are not published as domain events.

### Shutdown

1. An operator stop, emergency stop, or watchdog interruption records the request.
2. The supervisor creates the worker's fixed stop file and waits for the configured graceful interval.
3. Before forced termination, the supervisor re-verifies instance ID, token, PID, and process creation time.
4. If identity is missing, reused, or inaccessible, termination fails closed.
5. A verified unresponsive process is killed within the forced-shutdown bound.
6. Final worker and supervisor state is persisted, and the supervisor lease is released.

### Restart recovery

- A verified live process resumes observation without launching a duplicate.
- A missing process is marked crashed and becomes eligible for restart policy.
- A mismatched process is marked unknown and the supervisor fails closed.
- An inaccessible process makes health degraded; it is never killed speculatively.
- Pause, emergency stop, crash-loop state, counters, and restart windows survive restart.

The supervisor does not restore domain checkpoints. The Phase 2A durable workflow and checkpoint subsystem owns recovery position.

## Health and metrics

`status` and `report` distinguish `starting`, `recovering`, `healthy`, `degraded`, `stopped`, and `failed`. Degraded includes pause, emergency stop, internal timeouts, stopping, and crash-loop conditions.

Reported operational metrics include active worker processes, worker-state counts, completed and failed processes, forced terminations, restart count, recovery count, unexpected errors, lifecycle event count, and supervisor uptime. Queue depth and task completion metrics are intentionally absent because they belong to the control plane.

## Configuration

JSON configuration uses the fields in `examples/example-config.json`. CLI `--runtime-dir`, `--scenario`, and `--maximum-restarts` override their JSON equivalents.

| Field | Default | Rule |
| --- | ---: | --- |
| `readiness_timeout_seconds` | 10 | Positive |
| `heartbeat_timeout_seconds` | 15 | Positive |
| `graceful_shutdown_seconds` | 10 | Positive |
| `forced_shutdown_seconds` | 5 | Positive |
| `watchdog_interval_seconds` | 1 | Positive |
| `lease_ttl_seconds` | 3 × watchdog interval | Must exceed watchdog interval |
| `maximum_restarts` | 5 | Non-negative |
| `restart_window_seconds` | 60 | Positive |
| `initial_backoff_seconds` | 1 | Positive |
| `maximum_backoff_seconds` | 30 | At least initial backoff |
| `stable_runtime_seconds` | 30 | Positive |
| `max_log_bytes` | 1 MiB | Positive |
| `jitter_enabled` | false | Reserved; the prototype remains deterministic |

Unknown keys, invalid scenarios, contradictory bounds, malformed JSON, missing runtime directories, and stale database revisions are rejected visibly.

## Install and run

From this directory in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m jarvis_worker_supervisor init --runtime-dir runtime
.\.venv\Scripts\python.exe -m jarvis_worker_supervisor run --runtime-dir runtime --scenario healthy
```

Use another terminal for controls:

```powershell
.\.venv\Scripts\python.exe -m jarvis_worker_supervisor status --runtime-dir runtime
.\.venv\Scripts\python.exe -m jarvis_worker_supervisor pause --runtime-dir runtime
.\.venv\Scripts\python.exe -m jarvis_worker_supervisor resume --runtime-dir runtime
.\.venv\Scripts\python.exe -m jarvis_worker_supervisor emergency-stop --runtime-dir runtime
.\.venv\Scripts\python.exe -m jarvis_worker_supervisor emergency-resume --runtime-dir runtime
.\.venv\Scripts\python.exe -m jarvis_worker_supervisor stop --runtime-dir runtime
```

`start` is a compatibility alias for the attached `run` watchdog. `simulate` runs one bounded scenario. `clean` refuses active workers and requires `--yes`.

Exit code `0` means success, `1` means an unexpected/internal or final failed-runtime condition, and `2` means rejected input, configuration, state, or initialization.

## Verification

```powershell
.\.venv\Scripts\python.exe -m ruff format . --check
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest -q
```

Tests use isolated temporary databases and deterministic worker scenarios. They cover migration, configuration, transaction rollback, exclusive leases, PID reuse, inaccessible identity, startup, readiness, heartbeat timeout, crash loops, graceful and forced shutdown, emergency stop, multiple cycles, restart recovery, health mapping, log retention, and CLI safety. CI runs this suite independently from the backend and frontend jobs.

## Operational recommendations

- Keep the runtime directory on a local disk with sufficient free space.
- Use one Task Scheduler entry with `MultipleInstancesPolicy=IgnoreNew`.
- Keep the watchdog attached; do not launch an unmanaged worker.
- Treat `failed`, `crash_loop`, identity errors, and forced termination as operator-visible incidents.
- Back up the Phase 2A database separately; the prototype database is not a domain-state backup.
- Run `init` after updating to apply reviewed migrations before `run`.

## Known limitations

- Only one local supervisor and one simulated worker are supported.
- No real model, tool, network integration, shell tool, task claiming, or autonomous execution exists.
- There is no production adapter to the Phase 2A execution pipeline or API health response.
- Process exit codes cannot always be recovered after the original supervisor process is lost.
- Log truncation can be delayed when Windows temporarily locks an active log file; failures are logged and retried on the next tick.
- Task Scheduler XML is a sanitized example and is never installed automatically.
