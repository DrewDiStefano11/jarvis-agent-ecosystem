# Durable agent-runtime control plane

The runtime domain remains event-ledger authoritative. `InMemoryAgentRuntimeRepository` is retained for focused domain tests; `SqlAlchemyAgentRuntimeRepository` persists canonical JSON contracts plus normalized query projections in `agent_runtime_*` tables. The migration chain is authoritative and currently has the single head `20260728_08`; representable data may be rolled back one revision with `alembic downgrade -1`.

Each command writes events, a replay-equivalent snapshot, attempt/checkpoint projections, and its processed-command result in one SQL transaction. `(run_id, command_id)` is checked before optimistic version/sequence checks: an exact retry returns its stored result after restart while a changed payload conflicts. For a new command ID, an existing run returns `run_already_exists` before create-version validation; a genuinely new run with a nonzero expected version returns `version_conflict`. `integrity_check(run_id)` replays the durable ledger and fails closed on a projection mismatch.

Runtime correlation IDs are preserved byte-for-byte across the run snapshot, ledger event, shared outbox `EventEnvelope`, audit row, websocket publication, and restart/replay. The shared maximum is 120 characters; oversized values are rejected at contract validation rather than truncated during persistence.

The typed HTTP API is under `/api/agent-runtime`: list/get runs, events, attempts, checkpoints, lineage, and `/commands`. Handlers dispatch the existing runtime contracts; they do not implement transitions. Actor references are trusted local-control-plane input, **not authentication**. No model execution, worker launch, provider call, tool use, scheduling, or autonomous orchestration is implemented.
