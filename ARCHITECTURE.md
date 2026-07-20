# Architecture

Phase 1 deliberately simulates orchestration so the team can validate human control, information architecture, contracts, and failure behavior before introducing nondeterministic or privileged systems.

## Boundaries

The FastAPI process is authoritative. Pydantic models define domain and wire shapes; OpenAPI is the HTTP contract. Thin route handlers call `InMemoryRepository`, `SimulatorEngine`, and `EventBroker`. The repository abstraction is the replacement seam for PostgreSQL. The simulator owns ordering, checkpoints, transitions, and timers. Approval and audit changes are recorded beside domain mutations.

React is a separate client. Its controlled store loads snapshots over HTTP and treats WebSocket messages as ordered invalidation/state notifications. Duplicate events are ignored; gaps trigger an HTTP resynchronization. Disconnection activates polling and visibly marks state as last-known. Views contain presentation and interaction logic only.

HTTP handles queries, commands, resynchronization, and structured errors. WebSocket provides snapshots on connection plus low-latency, schema-versioned event envelopes. Sequence numbers are monotonic for one simulator session and restart on reset.

The CSS office is a projection of agent/task state, never a source of truth. Agent/task clicks reuse the same details components used elsewhere.

## Evolution seams

- LangGraph can replace deterministic decision steps behind the simulator/orchestrator interface.
- Prefect can replace in-process scheduling while commands and events remain stable.
- PostgreSQL/pgvector can implement repository and memory ports; Redis can later support ephemeral coordination.
- Tool adapters can connect approved email, calendar, files, browser, and Windows capabilities without granting route handlers direct access.
- Approval storage will gain durable transactions, idempotency keys, identities, and policy evaluation. Audit storage will become append-only and tamper-evident.

These are planned seams, not active features. Real adapters must remain unavailable until their own threat models, approval policies, fixtures, and integration tests exist.
