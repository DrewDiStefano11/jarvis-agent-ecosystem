# ADR: agent runtime execution ledger foundation

## Context

Jarvis needs an authoritative backend state layer for one agent execution lifecycle that can later connect durable tasks, leases, identity references, model routing, context assembly, tools, checkpoints, office visualization, audit, and observability.

At the time of this ADR:

- model-provider routing work is active elsewhere
- identity and RBAC work is active elsewhere
- frontend and office prototype work must remain untouched
- no new database tables or migrations are allowed for this task
- no real agent, tool, model, or network execution is allowed

The repository already has durable-control-plane concepts such as workflow checkpoints, task leases, append-only audit records, event envelopes, repository protocols, and unit-of-work boundaries. It does not yet have an isolated run-scoped domain that can become the authoritative execution ledger for future agent execution.

## Decision

Create a pure Python runtime-domain package that provides:

- validated run, attempt, checkpoint, event, and command contracts
- a centralized explicit state-transition model
- an append-only per-run execution ledger
- deterministic replay from events to snapshot
- optimistic concurrency via expected run version and ledger sequence checks
- run-scoped idempotent command handling
- a pure recovery planner
- repository protocols plus a deterministic in-memory reference implementation

## Why event-ledger plus snapshot was selected

A snapshot alone is insufficient because future integrations need durable history, explainability, replay, and checkpoint lineage validation.

An event ledger alone is possible, but repeated reads benefit from a stored authoritative snapshot for query and optimistic concurrency.

The chosen combination gives:

- append-only auditability
- deterministic replay validation
- efficient current-state access
- a future bridge to transactional outbox publication
- clear recovery-point reasoning tied to event sequence and run version

## Why persistence is deferred

Database schema and migrations are intentionally deferred because:

- this task must not modify shared SQLAlchemy models or migrations
- active parallel work would make premature schema coupling risky
- the goal here is to stabilize domain rules before freezing storage shape

The in-memory repository is honest about its limits and is meant for tests plus adapter development, not production durability claims.

## Why model, tool, and identity integrations are deferred

Those domains are active elsewhere and have different merge boundaries.

This runtime package therefore uses:

- opaque validated `agent_id`
- opaque executor and requester references
- opaque capability keys
- opaque checkpoint state references

That keeps the execution-state layer independent while preserving future integration points.

## Alternatives considered

### Reuse or extend task-lease rows directly

Rejected for now.

Task leases represent worker ownership for durable tasks, not the full lifecycle of one authoritative agent execution run. Coupling them now would mix concerns and cross forbidden file boundaries.

### Add SQLAlchemy persistence immediately

Rejected.

It would require shared model and migration changes that are explicitly out of scope for this task.

### Store only snapshots without events

Rejected.

That would weaken replay, recovery validation, and historical explainability.

### Build a live worker or orchestration loop

Rejected.

The mission is a deterministic domain foundation, not a background runtime.

## Consequences

Positive:

- future workers can depend on one authoritative state model
- event replay and snapshot equivalence are heavily testable
- optimistic concurrency and idempotency rules are explicit before persistence work starts
- future adapters can implement the repository contract without changing domain semantics

Trade-offs:

- the reference repository is nonpersistent
- no API routes or startup wiring exist yet
- future database/storage work still needs mapping decisions
- some helper payloads are duplicated intentionally to keep replay self-sufficient

## Future migration path

1. keep the domain contracts and transition matrix stable
2. add a persistence adapter that implements the repository protocol
3. map ledger append plus snapshot save into one transaction boundary
4. add a transactional outbox bridge without bypassing append-only rules
5. attach task-lease ownership as an orchestrator input, not as a replacement for run state
6. attach identity and model routing only through opaque references and narrow protocols

## Compatibility expectations

This foundation preserves current repository expectations by aligning with existing repository/documentation principles:

- append-only event history
- stable event schema versioning
- deterministic sequence semantics
- explicit unit-of-work boundaries
- checkpoint ownership of recovery position
- future transactional-outbox publication boundary

It does not replace existing task, lease, workflow, simulator, checkpoint, or outbox abstractions.
