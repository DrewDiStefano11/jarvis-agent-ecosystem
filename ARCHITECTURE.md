# Architecture

Phase 2A keeps orchestration deterministic while moving authoritative control-plane state to SQLite. SQLAlchemy models are persistence-only; Pydantic models remain the HTTP and event contracts.

## Boundaries

SQLite is authoritative. Thin route handlers call repository/service boundaries and `SimulatorEngine`; mutations, append-only audit data, checkpoints, and outbox envelopes are committed before WebSocket publication. WAL, enforced foreign keys, a five-second busy timeout, and short-lived sessions support reliable local Windows development.

React is a separate client. Its controlled store loads snapshots over HTTP and treats WebSocket messages as ordered invalidation/state notifications. Duplicate events are ignored; gaps trigger an HTTP resynchronization. Disconnection activates polling and visibly marks state as last-known. Views contain presentation and interaction logic only.

HTTP handles queries, commands, resynchronization, and structured errors. WebSocket provides snapshots on connection plus low-latency, schema-versioned event envelopes. Sequence numbers are monotonic for one simulator session and restart on reset.

The CSS office is a projection of agent/task state, never a source of truth. Agent/task clicks reuse the same details components used elsewhere.

## Evolution seams

- LangGraph can replace deterministic decision steps behind the simulator/orchestrator interface.
- Prefect can replace in-process scheduling while commands and events remain stable.
- PostgreSQL can later implement the repository protocols without changing API/domain models. This phase needs no Redis or external broker.

Sequence numbers are durable and monotonic within an event-session ID. Reset creates a new session; clients reset duplicate detection when that ID changes.
- Tool adapters can connect approved email, calendar, files, browser, and Windows capabilities without granting route handlers direct access.
- Approval storage will gain durable transactions, idempotency keys, identities, and policy evaluation. Audit storage will become append-only and tamper-evident.

These are planned seams, not active features. Real adapters must remain unavailable until their own threat models, approval policies, fixtures, and integration tests exist.

## Runtime prototype boundary

The opt-in worker-supervisor prototype under `prototypes/phase-2b-worker-supervisor` is an OS-process lifecycle candidate, not a second task orchestrator. It owns a prototype-local, Alembic-migrated operational store for supervisor leases, worker process identity, lifecycle events, and counters. It does not read or write domain tasks, workflow checkpoints, audit records, idempotency claims, or outbox envelopes.

Production integration must inject the existing control-plane execution pipeline behind the fixed worker boundary and publish domain changes through the existing repository/unit-of-work/outbox path. The prototype must not be wired into FastAPI startup until that adapter and its migrations, contracts, health fields, and integration tests are reviewed together.
