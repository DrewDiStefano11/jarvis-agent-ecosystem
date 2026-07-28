# Agent runtime execution ledger foundation

## Purpose

The agent runtime package is a backend-only domain foundation for representing, validating, recording, replaying, and explaining the lifecycle of one Jarvis agent execution run.

It is intentionally isolated from:

- FastAPI routes
- SQLAlchemy persistence
- model providers
- identity and RBAC
- task schedulers and workers
- tool execution
- network calls
- office or frontend state

The package provides deterministic contracts, an append-only execution ledger, optimistic concurrency rules, idempotent command handling, a reference in-memory repository, and a pure recovery planner.

## Terminology

- **run**: one authoritative agent execution lifecycle.
- **attempt**: one bounded execution attempt inside a run.
- **ledger**: the ordered append-only event history for one run.
- **snapshot**: the current authoritative state derived from the ledger.
- **checkpoint**: an immutable recovery marker tied to one run, one attempt, one version, and one ledger position.
- **recovery plan**: a pure decision that says whether another attempt may start and from which checkpoint.
- **processed command**: an idempotency record for one run-scoped command ID.

## Run lifecycle

States:

- `created`
- `queued`
- `claimed`
- `starting`
- `running`
- `pause_requested`
- `paused`
- `blocked`
- `cancel_requested`
- `cancelling`
- `cancelled`
- `succeeded`
- `failed`
- `timed_out`
- `abandoned`

Categories:

- pre-execution: `created`, `queued`, `claimed`
- active: `starting`, `running`
- interrupted: `pause_requested`, `paused`, `blocked`
- cancellation: `cancel_requested`, `cancelling`
- terminal: `cancelled`, `succeeded`, `failed`, `timed_out`, `abandoned`

Terminal states are immutable.

## Transition rules

One authoritative transition table lives in `app/agent_runtime/transitions.py`.
Service methods and replay both rely on that single definition.

Each event rule declares:

- allowed source states
- allowed target states
- required payload metadata
- whether an attempt must exist
- whether an active attempt must exist
- whether checkpoint creation is allowed
- whether the event is terminal
- whether the event increments run version

Illegal transitions raise typed domain errors instead of silently mutating state.

## State diagram

```text
created -> queued -> claimed -> starting -> running -> claimed -> succeeded
                                      |          |
                                      |          +-> pause_requested -> paused -> running|claimed|queued
                                      |          +-> blocked -> claimed|running
                                      |          +-> cancel_requested -> cancelling -> cancelled
                                      |          +-> blocked(recovery_required) after attempt_failed|timed_out|abandoned
                                      |
                                      +-> cancel_requested -> cancelled   (pre-start immediate path)

created|queued|claimed|blocked -> failed|timed_out|abandoned
```

## Attempt lifecycle

Attempt states:

- `created`
- `starting`
- `running`
- `paused`
- `cancelled`
- `succeeded`
- `failed`
- `timed_out`
- `abandoned`

Rules:

- attempt numbers start at 1
- attempt numbers never repeat inside one run
- only one attempt may be active at a time
- a terminal attempt never becomes active again
- only failed, timed-out, and abandoned terminal outcomes are eligible for another attempt through the documented recovery flow
- a succeeded attempt is final for that run and must be followed by `complete_run`, not another attempt
- the runtime never auto-retries; a future orchestrator must request another attempt explicitly
- failed, timed-out, and abandoned attempt records preserve the resolved authoritative `attempt_id` in both history and snapshot failure state, even when the command omitted it

## Cancellation protocol

Two supported patterns exist.

### Pre-start immediate cancellation

`created|queued|claimed -> cancel_requested -> cancelled`

The cancellation request is still written to the ledger before terminal cancellation.

### Active cancellation handshake

`running|starting|paused|blocked(with active attempt) -> cancel_requested -> cancelling -> cancelled`

A cancellation request stores:

- command ID
- reason code
- human-safe detail
- requester reference
- timestamp

Policy:

- exact duplicate command replay returns the stored result
- the runtime compares the stored command hash before replaying a processed cancellation result
- same `(run_id, command_id)` with different contents raises `command_conflict`
- a different command ID cannot replace an accepted cancellation reason once cancellation has started
- entering `cancel_requested` or `cancelling` clears incompatible `pause_reason` and `blocking_reason` snapshot fields
- active-attempt cancellation must progress through `cancel_requested -> cancelling -> cancelled`
- `run_cancelled` may be applied directly from `cancel_requested` only when no active attempt exists
- pause and block history remains preserved in prior ledger events rather than stale current-state fields
- cancellation of terminal runs returns a typed conflict
- no external signal or worker kill is performed here

## Pause versus blocked semantics

`paused` means deliberate suspension.
Examples: operator pause, policy pause, scheduling pause.

`blocked` means progress cannot continue without an explicit dependency or recovery action.
Examples: waiting for approval, missing dependency, unavailable resource, recovery required.

Both are nonterminal.
Both require explicit commands to leave them.
The ledger keeps the full pause and block history.

When an attempt terminalizes into recovery-required `blocked`, the runtime clears any stale pause metadata from the snapshot and replaces it with a `blocking_reason`. That block must use code `recovery_required`, resume toward `claimed`, and the nested failure record must reference the authoritative active attempt. Prior pause history remains visible through earlier ledger events.

## Checkpoint model

Each checkpoint is immutable and includes:

- checkpoint ID
- run ID
- attempt ID
- checkpoint sequence
- run version
- event sequence
- schema version
- timestamp
- opaque state reference
- integrity digest
- optional resume cursor
- safe metadata

Rules:

- checkpoint sequences begin at 1 and increase by 1
- checkpoint lineage must match the active run and active attempt
- checkpoint run/event positions must match the checkpoint event position
- checkpoint timestamps must exactly match the enclosing checkpoint event timestamp
- terminal runs reject new checkpoints
- checkpoint IDs are globally unique within a run
- reusing a checkpoint ID on the same attempt with the same stored content, including the same timestamp, is a deterministic no-op
- reusing a checkpoint ID with different content, a different timestamp, or from another attempt raises a conflict
- the runtime compares the stored command hash before replaying a processed checkpoint result

## Recovery-plan rules

Recovery planning is pure and fail-closed.

Allowed only when:

- the snapshot matches ledger replay
- the run is blocked with `recovery_required`
- no attempt is active
- the latest attempt is `failed`, `timed_out`, or `abandoned`
- maximum attempts have not been exhausted
- checkpoint lineage is valid

Denied when:

- the run is terminal
- the attempt limit is exhausted
- the ledger is inconsistent
- the snapshot disagrees with replay
- an active attempt still exists
- checkpoint lineage is invalid

Checkpoint selection is deterministic:

1. latest valid checkpoint from the latest terminal attempt
2. otherwise latest valid checkpoint from earlier attempts
3. otherwise no checkpoint, with a warning that the next attempt restarts from the attempt boundary

When recovery is active, beginning the next attempt is bound to the currently derived recovery plan:

- a run with `recovery_status=required` must remain blocked until recovery is explicitly planned
- if the plan selected a checkpoint, the next attempt must use that exact checkpoint ID
- omitting the checkpoint or supplying an older/different checkpoint is rejected
- if the plan selected no checkpoint, supplying an arbitrary checkpoint is rejected
- recovery state is only cleared after the validated attempt-creation events commit successfully

## Ledger sequence rules

Each runtime event envelope includes:

- `event_id`
- `event_type`
- `event_schema_version`
- `run_id`
- optional `attempt_id`
- `sequence_number`
- `run_version`
- `timestamp`
- optional `actor_reference`
- optional `command_id`
- optional `correlation_id`
- optional `causation_id`
- safe payload
- safe metadata

Correlation IDs are preserved exactly. The shared maximum is 120 characters; values of 1 through 120 characters are accepted and longer values are rejected during contract validation. No layer truncates, hashes, replaces, or normalizes a valid correlation ID, so the runtime event, run projection, durable event row, outbox row, outer dispatcher `EventEnvelope`, audit row, audit API response, websocket publication, restart reload, and exact replay all carry the identical value.

When an event payload section corresponds to a required command field such as `executor_reference`, replay validates it with the same required-identifier rules rather than treating it as optional.

Rules:

- sequence numbers begin at 1
- sequences increase exactly by 1
- run version increases exactly by 1 for every appended event
- version and sequence are equal by policy in this foundation
- event timestamps never move backward
- snapshot timestamp validation is causal rather than one fixed field order
- `paused_at`, `resumed_at`, and `last_heartbeat_at` are latest-occurrence fields and remain valid across repeated pause/resume cycles
- events are immutable
- incompatible schema versions are rejected

## Replay behavior

Deterministic replay is:

`ordered runtime events -> authoritative snapshot + attempt history + checkpoint history`

Replay rejects:

- sequence gaps
- duplicate sequences
- out-of-order events
- mismatched run IDs
- mismatched active attempt IDs
- malformed event payload values, including invalid `detail`, `target_state`, or structured payload sections
- invalid attempt numbering or attempt-limit overflows
- attempt creation while another attempt is active or while a prior attempt is nonterminal
- invalid resumed-checkpoint lineage or recovery-checkpoint mismatches
- backward timestamps
- incompatible event schema versions
- transitions after terminal state
- checkpoint lineage mismatches
- inconsistent run versions

## Timestamp semantics

Snapshot timestamp fields are validated as the latest known occurrence of their event type, not as one permanently increasing left-to-right tuple.

This means repeated legal cycles such as:

- pause -> resume -> pause again
- pause -> resume -> heartbeat
- block -> unblock -> block again

remain valid.

The runtime still enforces causal relationships such as:

- timestamps must be timezone-aware UTC
- no stored timestamp may precede `created_at`
- `claimed_at` cannot precede `queued_at`
- `started_at` cannot precede `claimed_at`
- `last_heartbeat_at` cannot precede `started_at`
- `completed_at` cannot precede `started_at` or `cancellation_requested_at`
- event timestamps remain monotonic in ledger order

## Idempotency

Every state-changing command is scoped by `(run_id, command_id)`.

Rules:

- exact command replay returns the stored result
- exact replay appends no new event and causes no mutation
- same command ID with different contents raises `command_conflict`
- failed commands are not stored as processed
- duplicate processed commands do not append duplicate events
- repository commit is authoritative for concurrent idempotency; exact concurrent duplicates return the stored result rather than a stale version conflict
- command handlers build candidate state from the aggregate they originally loaded instead of re-reading a newer ledger before commit
- checkpoint duplicate handling also supports a stable no-op when the same checkpoint ID and content are submitted again on the same attempt

### Create-command precedence

`create_run` resolves duplicate creates in one fixed order:

1. a processed command with identical canonical contents returns the stored result with `idempotent_replay=true` and writes no event, projection, audit, or outbox row
2. a processed command whose contents changed returns `command_conflict`, including when the changed command also carries a nonzero `expected_run_version`
3. no processed command but an existing run returns `run_already_exists`, regardless of whether `expected_run_version` is zero or nonzero
4. neither a processed command nor a run, with a nonzero `expected_run_version`, returns `version_conflict`
5. neither exists and `expected_run_version` is zero continues into the atomic durable create

Lineage validation stays inside the durable transaction, concurrent creates of the same run commit exactly once, and rejected duplicates leave no partial run, event, processed-command, projection, audit, or outbox rows.

## Optimistic concurrency

Every state-changing command carries `expected_run_version`.

Rules:

- the current snapshot version must match before commit
- repository commit also verifies the expected ledger sequence
- failed version checks must not partially mutate snapshot, events, or processed-command state

## Error codes

The runtime package defines stable typed errors including:

- `run_not_found`
- `run_already_exists`
- `invalid_transition`
- `terminal_run_immutable`
- `version_conflict`
- `command_conflict`
- `attempt_not_found`
- `active_attempt_exists`
- `attempt_limit_exceeded`
- `invalid_attempt_state`
- `checkpoint_not_allowed`
- `checkpoint_sequence_conflict`
- `checkpoint_lineage_error`
- `ledger_sequence_error`
- `ledger_replay_error`
- `recovery_not_allowed`
- `invalid_runtime_metadata`
- `invalid_runtime_identifier`

Each typed error carries a stable code plus safe run/attempt/command references and safe metadata.

## Parent and child lineage

Runs may optionally reference a parent run ID.

Policy:

- a run cannot parent itself
- lineage traversal is bounded to avoid infinite graphs
- cycles are rejected during validated run creation
- lineage order is deterministic: immediate parent first, then older ancestors
- missing parents are allowed as unresolved opaque references
- missing ancestors are surfaced explicitly during lineage resolution
- lineage does not imply authorization, cancellation propagation, or failure propagation
- this package does not autonomously create child runs

## Query behavior

The in-memory reference repository supports deterministic filtering by:

- run ID
- task ID
- agent ID
- state
- terminal versus nonterminal
- correlation ID
- parent run ID
- creation time range

Ordering is stable by `(created_at, run_id)` and pagination uses bounded offset semantics.

## Repository protocol

The repository contract supports:

- create run
- load run
- save run with expected version
- append events
- list events
- load attempt history
- list checkpoints
- look up processed commands
- store processed-command results
- query runs deterministically
- atomically commit a command result in the reference implementation

Repository invariants:

- direct `create_run` requires a non-empty authoritative ledger whose replay exactly matches the supplied snapshot
- direct `save_run` is only allowed when the supplied snapshot exactly matches deterministic ledger replay
- atomic command commits recheck `(run_id, command_id)` under the repository lock so exact concurrent duplicates return the stored result instead of surfacing a stale version conflict
- safe metadata normalization rejects colliding keys that canonicalize to the same normalized name instead of silently overwriting one value with another

## In-memory repository limitations

The in-memory repository is a real reference implementation for tests and future adapter development, but it is not durable.

Limitations:

- process memory only
- no crash persistence
- no transactional outbox rows
- no multi-process coordination
- no production durability guarantees

It still enforces:

- optimistic version checks
- append-only events
- deterministic ordering
- deep-copy safety
- run-scoped command idempotency

## Integration boundaries

Future integrations may attach this package to:

- durable task and lease ownership
- identity references and authorization decisions
- model routing
- context assembly
- tool invocation
- checkpoint storage adapters
- audit and observability pipelines
- a transactional outbox publisher

Those integrations are deferred on purpose. This package uses opaque IDs and safe metadata only.

## Examples

### Create and queue a run

1. `create_run`
2. `queue_run`
3. `claim_run`
4. `begin_attempt`
5. `start_attempt`

### Fail an attempt and plan recovery

1. `attempt_failed`
2. snapshot becomes `blocked` with `recovery_required`
3. `request_recovery_plan`
4. `unblock_run`
5. `begin_attempt` with a selected checkpoint reference

### Cancel before execution starts

1. `request_cancellation`
2. ledger records `cancellation_requested`
3. same command appends `run_cancelled`

## Explicit non-goals

This package does **not** implement:

- real agent execution
- model selection or provider execution
- tool execution
- network calls
- shell commands
- browser automation
- RBAC or identity evaluation
- approval workflows
- SQLAlchemy rows or migrations
- FastAPI routes
- application startup wiring
- frontend controls or office visuals
- message-broker publication
- autonomous orchestration
