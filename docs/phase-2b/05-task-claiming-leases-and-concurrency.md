# 05 - Task Claiming, Leases, and Concurrency

This document designs the atomic task claiming mechanism for Phase 2B. Because Phase 2B relies on a single local SQLite database, the design leverages SQLite's transactional guarantees to prevent concurrent execution of the same task by stale processes or duplicate workers.

## Core Concepts

* **Worker Identity:** Every time the worker process starts, it generates a unique UUID (e.g., `worker-b38d...`).
* **Lease:** A durable record indicating that a specific worker is actively processing a task. It includes an `expires_at` timestamp.
* **Heartbeat:** The active worker periodically renews its lease before it expires.
* **Compare-and-Swap (CAS):** Claiming relies on updating a row only if its current state matches expected conditions (e.g., not claimed, or lease expired).

## Queue Selection Rules
1. The worker polls for tasks in the `queued` state, or tasks with an expired lease.
2. High priority tasks are selected first.
3. Within the same priority, tasks are selected via FIFO (oldest `created_at`).
4. Tasks blocked by dependencies (not implemented in Phase 2B, but structurally reserved) are ignored.

## The Concurrency Problem in SQLite
SQLite handles concurrency via locks (e.g., WAL mode). Multiple connections can read, but only one can write. Therefore, the claiming process must be a fast, isolated transaction to avoid `database is locked` errors.

### Exactly-once vs. At-least-once Semantics
The lease mechanism guarantees **at-least-once** delivery and execution. To simulate **exactly-once** semantics, all side effects (tool executions) must be idempotent or guarded by a checkpoint check. If a worker crashes after execution but before recording completion, the next worker to reclaim the task will observe the side-effect marker (checkpoint) and skip the redundant execution.

## Pseudocode Implementations

### Claiming a Task
```sql
-- Executed inside a single transaction (Unit of Work)
UPDATE tasks
SET
    worker_id = :current_worker_id,
    lease_expires_at = :now_plus_30_seconds,
    status = 'in_progress'
WHERE
    id = (
        SELECT id FROM tasks
        WHERE status = 'queued'
           OR (worker_id IS NOT NULL AND lease_expires_at < :now)
        ORDER BY priority DESC, created_at ASC
        LIMIT 1
    )
    AND status != 'completed'
    AND status != 'failed';

-- If row_count == 1, claim successful. Return task ID.
```

### Renewing a Lease (Heartbeat)
```sql
UPDATE tasks
SET lease_expires_at = :now_plus_30_seconds
WHERE id = :task_id AND worker_id = :current_worker_id;

-- If row_count == 0, the worker lost the lease (e.g., starvation or long pause).
-- The worker MUST immediately abort execution for this task.
```

### Completing a Claimed Task
```sql
UPDATE tasks
SET
    status = 'completed',
    worker_id = NULL,
    lease_expires_at = NULL,
    completed_at = :now
WHERE id = :task_id AND worker_id = :current_worker_id;
```

### Releasing or Cancelling a Task
```sql
UPDATE tasks
SET
    status = 'queued',  -- Or 'cancelled'
    worker_id = NULL,
    lease_expires_at = NULL
WHERE id = :task_id AND worker_id = :current_worker_id;
```

## Failure Scenarios

* **Stale-worker / Orphaned-task recovery:** If the worker crashes (Process A), it fails to send heartbeats. The lease expires. When Process B starts, it sees the task with `lease_expires_at < now` and successfully reclaims it.
* **Shutdown during claim:** The graceful shutdown handler intercepts the SIGINT/SIGTERM, releases the lease immediately (setting `worker_id = NULL`), and exits.
* **Crash after execution but before completion:** The lease expires. The next worker claims the task, loads the task's last checkpoint, observes the step was already executed, and proceeds directly to marking the task complete.
* **Clock assumptions:** The system assumes the local system clock does not jump backwards significantly during a 30-second lease window. All time comparisons use UTC.
* **Starvation prevention:** The worker manager includes a maximum concurrent tasks limit (default: 1 in Phase 2B). If the limit is reached, it skips polling until a task completes. Backoff logic (e.g., 250ms -> 500ms -> 1s) prevents thrashing when the queue is empty.
* **Duplicate Execution Prevention:** The `worker_id = :current_worker_id` constraint on updates ensures that a zombie worker that wakes up from a long OS sleep cannot overwrite the state of a task that has been reclaimed by a healthy worker.
