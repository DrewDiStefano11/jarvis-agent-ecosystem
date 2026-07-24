# Recovery

Each deterministic run has a durable version and stable step identifiers. Every committed step stores a validated JSON-compatible checkpoint containing agent/task state, progress, pending approvals, retries/revisions, emergency state, event session, last committed sequence, and simulator variables. Pickle and arbitrary class serialization are prohibited.

The workflow runner serializes each step's durable commit, publication handoff, and in-memory step advancement with pause and emergency-stop checkpointing. An operator action that arrives during publication waits for the committed step to advance before writing its paused checkpoint, so durable and in-memory indices cannot regress.

Controlled failure uses that same serialized boundary. It stops and wakes the runner, stages a failed checkpoint at the last committed step, and commits task, workflow-run, checkpoint, system, audit, and outbox state in one unit of work. Failed runs are terminal and ineligible for resume. If the transaction fails, cached state reloads and the runner returns to its prior running or paused behavior. Terminal states exit the runner; paused, recovery-required, and emergency-stopped states clear the wake signal and block instead of spinning.

Startup migrates, seeds idempotently, inspects active runs and pending outbox rows, then marks interrupted running work `recovery_required`. Automatic resume is off by default. The System page shows the run/checkpoint and offers the existing Resume demo action. Resume validates workflow version and checkpoint shape and continues at the next uncommitted step. Invalid/incompatible checkpoints return a structured error instead of executing uncertain work.

Durably paused and already recovery-required runs are restored on every startup. They remain at the existing checkpoint and can use the same resume command without repeating committed steps, including when the API restarts repeatedly before an operator resumes recovery. Intentional pauses keep `recovery_status=none` and health remains healthy; only a run interrupted while `running` is promoted to `recovery_required`. Completed or failed simulator control state is also restored from durable system state and its last checkpoint. A new run is allowed only from idle with emergency stop inactive; paused and recovery-required runs require resume or reset, while completed and failed runs require reset.

The health endpoint performs a live database query and compares the stored Alembic revision with the application revision. It reports degraded health when storage is unreachable or the schema is not current instead of relying only on cached startup state.

Graceful shutdown cancels further simulator steps, commits or rolls back the current boundary, records shutdown time, dispatches pending committed events, and closes database resources. Reset cancels the runner, records an audit, creates a new event session, restores deterministic demo fixtures, removes temporary demo agents, and preserves user-created tasks and historical audit rows.

Emergency stop checkpoints only active or recoverable workflow state. Invoking it after completion or failure preserves the terminal run, terminal checkpoint, and non-resumable status while still persisting the system-wide emergency flag.

Emergency stop is idempotent while already active. Repeated calls do not create another checkpoint, audit event, or outbox envelope.

Task leases are independently recoverable ownership records. Startup and a bounded background sweep reclaim every expired active lease. The attempt is closed as `expired`; the task is requeued while retry budget remains or marked failed after exhaustion; audit and outbox evidence commit in the same transaction. A successor receives a new fencing token, so every later renew/release/complete/fail call from the old worker is rejected. Unexpired leases survive process restart unchanged.

Lease renewal may attach an existing validated workflow checkpoint belonging to the leased root task. The checkpoint ID is retained on the attempt and returned to a successor after expiration, keeping the durable workflow checkpoint—not worker memory—as recovery position. A worker must stop processing immediately when renewal reports `TASK_LEASE_LOST`.

The `20260720_01` migration is frozen as explicit table, index, unique-constraint, and foreign-key operations and does not import live application metadata. Blank installations persist the deterministic seeded audit fixture before serving requests; audit endpoints and snapshots retain it across application recreation.

Known limitation: the dispatcher is in-process and the database is intended for one local API process. Stable event IDs and frontend duplicate/gap handling make retries safe, but exactly-once delivery to a disconnected browser is not claimed. Lease fencing prevents concurrent ownership and stale commits inside the control plane; future external side-effect adapters must also enforce the fencing token or an idempotency key, because a process can crash after an external effect but before recording completion.
