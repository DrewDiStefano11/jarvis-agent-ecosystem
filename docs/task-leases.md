# Phase 2B task leases

Task leases give one registered local worker exclusive, time-bounded ownership of one durable task. They extend the Phase 2A SQLite control plane; there is no second database, queue, audit log, event bus, health subsystem, or recovery position.

## Lifecycle

1. A worker registers a stable instance ID and heartbeat duration.
2. Acquisition first reclaims expired leases, then selects the highest-priority eligible task using urgent-to-low priority, FIFO creation time, and task ID as a deterministic tie-breaker. Tasks with incomplete `requires` dependencies are skipped. An optional exact task selector applies the same eligibility and locking rules and is used by an explicitly queued autonomous runtime run.
3. The acquisition transaction moves the task to `in_progress`, creates an immutable attempt, creates the unique active lease, appends an audit record, advances the event-session sequence, and inserts an outbox envelope.
4. The worker renews before `expiresAt`. Renewal may attach a validated workflow checkpoint belonging to the leased root task.
5. The matching worker ID and lease token may complete, fail, release, or pause the attempt for human review. Review pause moves the task to `under_review`, closes the attempt, and revokes the active lease.
6. Draining or stopping a worker releases its leases. Task cancellation revokes its lease. Simulator reset revokes leases on deterministic demo tasks inside the reset transaction.

Lease tokens are bearer capabilities. API responses return the token to the owning worker, but events and audits contain only a short SHA-256 fingerprint. A mismatched, expired, released, cancelled, reset, or superseded token receives `TASK_LEASE_LOST` and must stop processing.

Emergency stop blocks new acquisitions and terminal worker commits. Renewal and release remain available so a paused owner can retain or relinquish ownership safely.

## Recovery and retry behavior

Startup, the periodic recovery sweep, and acquisition reclaim expired ownership. Recovery closes the attempt as `expired`, removes the active lease, and moves the task to `retrying` while retry budget remains. Exhausted tasks become `failed` with a durable `LEASE_EXPIRED` error.

An unexpired lease survives API/database recreation. An expired attempt's checkpoint ID survives on attempt history and is returned to the successor as `recoveryCheckpointId`. Durable workflow checkpoints, not worker memory, determine recovery position.

SQLite `BEGIN IMMEDIATE`, a unique active lease per task, immutable attempt numbers, and random fencing tokens prevent double acquisition, lost updates, stale commits, and concurrent completion. WAL and the existing bounded busy timeout apply to lease writes.

## Configuration

- `JARVIS_TASK_LEASE_SECONDS` — default lease/heartbeat duration; 1–3600 seconds, default 30.
- `JARVIS_TASK_LEASE_RECOVERY_INTERVAL_MS` — expired-lease sweep interval; 50–60000 ms, default 1000.

Workers may request a bounded duration during registration, acquisition, or renewal. Operators should choose a duration comfortably longer than normal transient database or process scheduling delays and renew well before the deadline.

## Operations and monitoring

`GET /api/health` and `GET /api/system/status` report:

- `activeWorkerCount`
- `activeLeaseCount`
- `expiredLeaseCount`
- `staleWorkerCount`

Expired leases and stale active worker heartbeats degrade health. The System page displays these counters and alerts. Lease acquisition, renewal, release, expiration, worker registration/drain/stop, completion, retry, failure, and cancellation remain visible through the transactional outbox and append-only audit history.

Useful operator responses:

- Stale worker without active work: drain or stop it, then investigate its heartbeat loop.
- Expired lease: allow automatic recovery, then inspect the attempt, checkpoint, and `task.lease.expired` audit/outbox record.
- Repeated expiration: increase the bounded lease duration only after checking worker latency and database contention.
- Exhausted retries: inspect the task error and attempt history before using the existing explicit retry command.

## Guarantees and limitation

The control plane guarantees one active owner, rejects stale writes, and commits task/attempt/audit/outbox state atomically. It therefore prevents concurrent duplicate execution and duplicate durable completion.

No lease algorithm can prove that a model call did not finish immediately before a process crashed. Phase 2C revalidates the lease before and after inference and before result/task commits, so stale output cannot become durable. A crash before validated-result persistence may repeat the local call; a crash after result persistence resumes finalization without a second call. Exactly-once inference is not claimed.
