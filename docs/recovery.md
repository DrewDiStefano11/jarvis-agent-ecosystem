# Recovery

Each deterministic run has a durable version and stable step identifiers. Every committed step stores a validated JSON-compatible checkpoint containing agent/task state, progress, pending approvals, retries/revisions, emergency state, event session, last committed sequence, and simulator variables. Pickle and arbitrary class serialization are prohibited.

Startup migrates, seeds idempotently, inspects active runs and pending outbox rows, then marks interrupted running work `recovery_required`. Automatic resume is off by default. The System page shows the run/checkpoint and offers the existing Resume demo action. Resume validates workflow version and checkpoint shape and continues at the next uncommitted step. Invalid/incompatible checkpoints return a structured error instead of executing uncertain work.

Graceful shutdown cancels further simulator steps, commits or rolls back the current boundary, records shutdown time, dispatches pending committed events, and closes database resources. Reset cancels the runner, records an audit, creates a new event session, restores deterministic demo fixtures, removes temporary demo agents, and preserves user-created tasks and historical audit rows.

Known limitation: the dispatcher is in-process and the database is intended for one local API process. Stable event IDs and frontend duplicate/gap handling make retries safe, but exactly-once delivery to a disconnected browser is not claimed.
