# Durable agent-runtime control plane

The runtime domain remains event-ledger authoritative. `InMemoryAgentRuntimeRepository` is retained for focused domain tests; `SqlAlchemyAgentRuntimeRepository` persists canonical JSON contracts plus normalized query projections in `agent_runtime_*` tables. Migration `20260727_04` is the authoritative revision and may be rolled back one revision with `alembic downgrade -1`.

Each command writes events, a replay-equivalent snapshot, attempt/checkpoint projections, and its processed-command result in one SQL transaction. `(run_id, command_id)` is checked before optimistic version/sequence checks: an exact retry returns its stored result after restart while a changed payload conflicts. `integrity_check(run_id)` replays the durable ledger and fails closed on a projection mismatch.

The typed HTTP API is under `/api/agent-runtime`: list/get runs, events, attempts, checkpoints, lineage, and `/commands`. Handlers dispatch the existing runtime contracts; they do not implement transitions. Actor references are trusted local-control-plane input, **not authentication**. No model execution, worker launch, provider call, tool use, scheduling, or autonomous orchestration is implemented.
