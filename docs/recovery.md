# Recovery

Each deterministic run has a durable version and stable step identifiers. Every committed step stores a validated JSON-compatible checkpoint containing agent/task state, progress, pending approvals, retries/revisions, emergency state, event session, last committed sequence, and simulator variables. Pickle and arbitrary class serialization are prohibited.

The workflow runner serializes each step's durable commit, publication handoff, and in-memory step advancement with pause and emergency-stop checkpointing. An operator action that arrives during publication waits for the committed step to advance before writing its paused checkpoint, so durable and in-memory indices cannot regress.

Startup migrates, seeds idempotently, inspects active runs and pending outbox rows, then marks interrupted running work `recovery_required`. Automatic resume is off by default. The System page shows the run/checkpoint and offers the existing Resume demo action. Resume validates workflow version and checkpoint shape and continues at the next uncommitted step. Invalid/incompatible checkpoints return a structured error instead of executing uncertain work.

Durably paused and already recovery-required runs are restored on every startup. They remain at the existing checkpoint and can use the same resume command without repeating committed steps, including when the API restarts repeatedly before an operator resumes recovery. Intentional pauses keep `recovery_status=none` and health remains healthy; only a run interrupted while `running` is promoted to `recovery_required`. Completed or failed simulator control state is also restored from durable system state and its last checkpoint, so a completed run still requires an explicit reset after a clean restart.

The health endpoint performs a live database query and compares the stored Alembic revision with the application revision. It reports degraded health when storage is unreachable or the schema is not current instead of relying only on cached startup state.

Graceful shutdown cancels further simulator steps, commits or rolls back the current boundary, records shutdown time, dispatches pending committed events, and closes database resources. Reset cancels the runner, records an audit, creates a new event session, restores deterministic demo fixtures, removes temporary demo agents, and preserves user-created tasks and historical audit rows.

The `20260720_01` migration is frozen as explicit table, index, unique-constraint, and foreign-key operations and does not import live application metadata. Blank installations persist the deterministic seeded audit fixture before serving requests; audit endpoints and snapshots retain it across application recreation.

Known limitation: the dispatcher is in-process and the database is intended for one local API process. Stable event IDs and frontend duplicate/gap handling make retries safe, but exactly-once delivery to a disconnected browser is not claimed.
