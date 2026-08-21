# Architecture

Phase 2A keeps orchestration deterministic while moving authoritative control-plane state to SQLite. SQLAlchemy models are persistence-only; Pydantic models remain the HTTP and event contracts.

## Boundaries

SQLite is authoritative. Thin route handlers call repository/service boundaries and `SimulatorEngine`; mutations, append-only audit data, checkpoints, and outbox envelopes are committed before WebSocket publication. WAL, enforced foreign keys, a five-second busy timeout, and short-lived sessions support reliable local Windows development.

`TaskLeaseRepository` extends that persistence boundary for Phase 2B. Worker registration and fenced task ownership use the existing SQLAlchemy session factory, system sequence, append-only audit, and transactional outbox. Lease recovery runs within the API lifecycle; validated workflow checkpoints remain the recovery position. The standalone PR #5 SQLite database, CLI, audit schema, and worker loop are intentionally not duplicated in production.

React is a separate client. Its controlled store loads snapshots over HTTP and treats WebSocket messages as ordered invalidation/state notifications. Duplicate events are ignored; gaps trigger an HTTP resynchronization. Disconnection activates polling and visibly marks state as last-known. Views contain presentation and interaction logic only.

HTTP handles queries, commands, resynchronization, and structured errors. WebSocket provides snapshots on connection plus low-latency, schema-versioned event envelopes. Sequence numbers are monotonic for one simulator session and restart on reset.

The CSS office is a projection of agent/task state, never a source of truth. Agent/task clicks reuse the same details components used elsewhere.

## Evolution seams

- LangGraph can replace deterministic decision steps behind the simulator/orchestrator interface.
- Prefect can replace in-process scheduling while commands and events remain stable.
- PostgreSQL can later implement the repository protocols without changing API/domain models. This phase needs no Redis or external broker.
- Tool adapters can connect approved email, calendar, files, browser, and Windows capabilities without granting route handlers direct access.
- Approval storage will gain identities and policy evaluation. Audit storage can later become tamper-evident.

Sequence numbers are durable and monotonic within an event-session ID. Reset creates a new session; clients reset duplicate detection when that ID changes.

## Context assembly

The Phase 2B Context Assembler is an integrated deterministic service, not a runtime or provider. Pydantic context contracts validate the public boundary. The assembler has no database, filesystem, network, model, or tool access; it accepts bounded content and returns a sanitized provider-neutral request, manifest, and report. Every supplied source remains structurally untrusted regardless of its ordering trust level.

The repository persists assemblies in the same command transaction as their append-only audit, idempotent response, and outbox event. This makes recovery binary: no row exists before commit; after commit the assembly is complete and replayable. Context metrics are projections of persisted assemblies. See [docs/context-assembler.md](docs/context-assembler.md).

These are planned seams, not active features. Real adapters must remain unavailable until their own threat models, approval policies, fixtures, and integration tests exist.

## Runtime supervision

The Windows-first runtime supervisor is infrastructure outside FastAPI. It owns explicit child
process definitions for the stable loopback API, built local web UI, and the already opt-in autonomous
worker. Existing HTTP health and system-status contracts remain authoritative; the supervisor does
not create a second emergency-stop or application-health source of truth.

A lifetime OS lock plus PID creation identity establishes singleton ownership. Windows Job Object
membership provides orphan containment, while normal shutdown remains bounded and graceful. Logs,
backup artifacts, ownership state, and known-good SHA metadata live in a marker-validated per-user
runtime home outside the repository. SQLite backups use the online backup API and atomic publication.
Task Scheduler can start the supervisor at current-user logon without a stored password. See
[runtime supervisor](docs/runtime-supervisor.md).
