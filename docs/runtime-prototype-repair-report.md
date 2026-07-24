# Runtime Prototype Repair Engineering Report

## Executive summary

PR #8 was not safe to merge as submitted. It was based on the pre–Phase 2A main commit, so GitHub presented 94 changed files and a non-mergeable branch even though the intended prototype itself was isolated under `prototypes/phase-2b-worker-supervisor`.

The original supervisor demonstrated useful process-launch and heartbeat ideas but had production-blocking defects: schema creation happened implicitly, state writes used replace semantics, every supervisor shared a fixed lease token, recovery was effectively empty, child exit detection was ambiguous, internal timeouts reused operator-shutdown state, health and metrics were incomplete, file parsing swallowed all exceptions, cleanup leaked SQLite handles on Windows, the CLI could leave an unmanaged worker, and several tests were placeholders or accepted multiple contradictory outcomes.

The repaired branch imports only the prototype, rebuilds its lifecycle and persistence boundaries, adds independent CI coverage, and documents that Phase 2A remains authoritative for tasks, checkpoints, audits, idempotency, API health, and outbox publication.

## Repository analysis

- Current main is Phase 2A commit `b2d2648`, containing the durable SQLAlchemy/SQLite control plane, Alembic revision `20260720_01`, transactional outbox, append-only audits, workflow runs, checkpoints, idempotency, recovery, and API health.
- PR #8 head is `51861b1` and was based on Phase 1 commit `0a2af9b`.
- The intended PR prototype added 56 files and approximately 1,791 lines under its prototype directory.
- The Phase 2A simulator and repository already own deterministic execution and recovery position. The supervisor must not duplicate those responsibilities.
- Existing backend CI covered Ruff, Alembic, and pytest; frontend CI covered typecheck, ESLint, Vitest, and build. PR #8 had no CI job for its own package.

## Runtime architecture review

### Ownership

The repaired prototype is an OS-process supervisor only. It owns:

- one supervisor lease and one fixed simulated worker process;
- worker PID, instance ID, process token, and creation-time verification;
- readiness, monotonic heartbeat observation, and shutdown signaling;
- restart windows, exponential backoff, crash-loop protection, and log retention;
- prototype-local lifecycle records and operational counters.

It does not own:

- task intake, validation, queue ordering, or public task status;
- domain execution plans or tool execution;
- workflow checkpoints or durable recovery position;
- domain audit records, idempotency, or event publication;
- FastAPI lifecycle or public health contracts.

### Startup

`init` creates directories and upgrades the prototype-local operational database through packaged Alembic revision `20260724_01`. Normal `run` startup verifies the revision, acquires a uniquely owned expiring lease, reconciles persisted worker identity, and launches only when durable state permits.

### Worker lifecycle

Transitions are explicit across starting, recovering, idle, launching, waiting for readiness, running, degraded, paused, emergency-stopped, stopping, crash-loop, stopped, and failed. Internal health failures are degraded cleanup conditions; only an operator or watchdog shutdown request moves the runtime to final stopped state.

### Recovery

Recovery verifies instance ID, process token, PID, and OS creation time:

- verified live workers resume observation without duplicate launch;
- missing workers become crashed and re-enter restart policy;
- mismatched workers become unknown and fail closed;
- inaccessible workers degrade health and are never killed speculatively.

The Alembic revision also upgrades databases created by the original PR prototype, adding missing lifecycle columns and replacing the unsafe legacy lease shape.

### Shutdown

Shutdown first writes the fixed stop file and waits for a bounded graceful interval. Forced termination is permitted only after identity is reverified. Identity mismatch or access denial fails closed. Final worker state, counters, lifecycle events, supervisor state, and lease release are persisted.

### Persistence and concurrency

- SQLite connections have a five-second busy timeout and are always closed explicitly.
- Writes use `BEGIN IMMEDIATE` transactions and rollback on failure.
- Supervisor updates modify named columns instead of replacing whole rows.
- Lease acquisition and renewal are serialized and compare unique owner IDs.
- Lifecycle events are append-only.
- Readiness and heartbeat files reject malformed, stale, wrong-token, and non-monotonic data.

### Health, logging, and metrics

Reports distinguish starting, recovering, healthy, degraded, stopped, and failed. Structured logging covers startup, exit, transitions, launch failure, retention failure, and unexpected exceptions. Durable metrics cover active workers, worker states, completed/failed workers, forced termination, restart, recovery, unexpected errors, lifecycle events, and uptime.

Queue and task metrics are intentionally not fabricated by this process supervisor; the control plane must expose them when a production adapter is implemented.

## Major implementation decisions

1. Import only the PR's prototype directory instead of replaying its obsolete Phase 2A file changes.
2. Keep the prototype opt-in and disconnected from FastAPI startup.
3. Preserve the fixed simulated-worker launch boundary with `sys.executable` and `shell=False`.
4. Use a separate operational database while explicitly excluding all domain state.
5. Make Alembic migration explicit during `init`; startup only verifies schema.
6. Package migrations inside the wheel and validate installation outside the repository.
7. Treat process identity as instance ID + token + PID + creation time; PID existence alone never authorizes termination.
8. Separate internal degraded cleanup from explicit final shutdown.
9. Replace permissive tests with deterministic assertions and isolated temporary databases.
10. Add a dedicated Windows CI job rather than coupling prototype dependencies to the API package.

## Files modified

- `.github/workflows/ci.yml`: independent runtime-prototype validation job.
- `ARCHITECTURE.md` and `README.md`: repository and ownership boundary.
- `prototypes/phase-2b-worker-supervisor/README.md`: lifecycle, recovery, health, configuration, operations, and limitations.
- `prototypes/phase-2b-worker-supervisor/pyproject.toml`: runtime, migration, test, lint, entry-point, and package-data configuration.
- `prototypes/phase-2b-worker-supervisor/src/jarvis_worker_supervisor/`: repaired CLI, state machine, database, migration, identity, reporting, shutdown, retention, and watchdog modules.
- `prototypes/phase-2b-worker-supervisor/src/jarvis_simulated_worker/`: faster deterministic polling and formatted support code.
- `prototypes/phase-2b-worker-supervisor/tests/`: 45 lifecycle, failure, migration, identity, reporting, and CLI tests.
- `prototypes/phase-2b-worker-supervisor/examples/`: current configuration and sanitized Task Scheduler example; stale report fixtures removed.

No application model, route, repository, migration, event contract, frontend state, or API response was changed.

## Validation performed

### Runtime prototype

- Ruff format check: passed.
- Ruff lint/static checks: passed.
- Pytest: 45 passed.
- Fresh database migration: passed.
- Legacy PR database upgrade: passed.
- Built wheel: passed.
- Installed-wheel initialization from outside the repository: passed.
- Installed-wheel status report: passed.

### Phase 2A backend

- Ruff format check: 23 files already formatted.
- Ruff lint: passed.
- Pytest: 47 passed.
- Existing warnings: 345 deprecation warnings related to FastAPI `on_event` and the current TestClient/httpx compatibility layer.

### Frontend

- TypeScript typecheck: passed.
- ESLint: passed.
- Vitest: 14 passed.
- Production build: passed.

### Coverage scenarios

Tests cover explicit initialization, repeated migration, legacy migration, transaction rollback, configuration validation, lease exclusion/renewal/expiration, readiness, heartbeat sequence, log retention, PID reuse, missing and inaccessible processes, all health classes, clean completion, worker crash, crash loop, readiness timeout, heartbeat timeout, graceful shutdown, forced termination, emergency stop, duplicate supervisor rejection, launch failure, live-process restart recovery, missing-process recovery, unexpected watchdog exceptions, multiple start/stop cycles, CLI rejection, and cleanup safety.

## Risks and limitations

- This is not yet the autonomous task runtime. It does not claim tasks or execute models/tools.
- FastAPI does not start it, and public API health does not yet include its state.
- Queue depth and task success/failure metrics remain control-plane responsibilities.
- Only one local supervisor and one worker are supported.
- A restarted supervisor cannot always recover a historical child exit code after the OS has reaped the process.
- Windows may temporarily lock an active log file; retention logs the failure and retries on a later tick.
- Task Scheduler behavior is documented but not exercised in CI.
- The existing backend deprecation warnings should be addressed separately; they were present on main and are unrelated to this repair.

## Recommended follow-up work

1. Define and review a narrow adapter from this supervisor boundary to the existing control-plane execution service.
2. Add application migrations and repository methods only when real worker lifecycle state becomes authoritative application data.
3. Extend existing API health and system contracts in one versioned contract change, with frontend state-store updates and tests.
4. Publish runtime domain events only through the existing unit-of-work and transactional outbox.
5. Add Windows Task Scheduler smoke testing in a dedicated deployment workflow.
6. Replace deprecated FastAPI event handlers with a lifespan context in a separate control-plane maintenance change.
7. Run soak and disk-pressure testing before enabling continuous local operation.

## Confidence

Confidence is high that the repaired OS-process supervisor prototype is reliable, maintainable, compatible with current main, and ready for code review.

Confidence is not yet sufficient to call Jarvis's complete autonomous runtime production-ready, because task claiming, execution-pipeline integration, public health integration, and domain event publication are intentionally not implemented in this prototype. Enabling those capabilities should be a separate reviewed integration phase rather than an implicit expansion of PR #8.
