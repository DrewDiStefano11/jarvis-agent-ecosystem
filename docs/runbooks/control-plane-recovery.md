# Local Control-Plane Recovery Runbook

**Document basis (SHA reviewed):** 567c59a6ce47f73383c093e55e72715b7998e958
**Last verified against:** July 24, 2026

> **Warning:**
> * Use only against the intended local development/runtime environment.
> * Do not run destructive commands without a backup.
> * Do not manually edit the database unless an owner explicitly authorizes it.
> * Do not assume a process is safe to kill based solely on PID.
> * Do not merge or deploy code as part of recovery unless separately reviewed.
> * Preserve logs and exact SHAs before changing state.

## Scope

**Covers:**
* local API control plane;
* database and migration state;
* workflows;
* tasks;
* leases;
* workers;
* Context Assemblies;
* approvals;
* audit;
* outbox;
* WebSocket/frontend synchronization;
* simulator state;
* emergency stop;
* startup/shutdown cleanliness.

**Does not fully cover:**
* unfinished filesystem sandbox;
* unfinished worker-supervisor integration;
* external production deployment;
* cloud infrastructure;
* external message brokers;
* third-party tool outages;
* data recovery after physical disk failure unless backup exists.

## Recovery principles

1. Stop making new changes before diagnosis.
2. Record current Git SHA and branch.
3. Identify the active database and runtime directory.
4. Preserve logs and database copy.
5. Determine the authoritative state source.
6. Prefer application-provided recovery operations.
7. Restart only after confirming startup will not compound the problem.
8. Never delete audit or outbox rows merely to make health green.
9. Never force task state without reconciling lease and attempt state.
10. Never treat frontend state as proof.
11. Never kill a process based only on PID.
12. Verify recovery by durable state, API state, and event state.
13. Document every manual action.

## Severity classification

* **SEV-4:** cosmetic or client-only issue (e.g. frontend caching anomaly). New work can continue.
* **SEV-3:** degraded local functionality with durable state intact (e.g. WebSocket connection drops). Backup recommended before major resets.
* **SEV-2:** blocked orchestration, stuck leases, migration failure, or growing outbox backlog. **Stop new work.** DB copy mandatory. Do not run destructive resets.
* **SEV-1:** suspected state corruption, duplicate execution, secret exposure, uncontrolled worker processes, or irreversible data risk. **Stop everything immediately.** DB copy mandatory. Escalation required before any mutation.

## Evidence collection checklist

Before recovery, collect the following into a text file or issue:
* current date/time and timezone;
* repository path;
* current branch;
* exact `HEAD` SHA;
* `main` SHA;
* uncommitted changes (`git status`);
* Python and Node versions;
* active runtime configuration (e.g., `JARVIS_DATABASE_URL` path);
* database path/URL with secrets redacted;
* Alembic current revision;
* Alembic head revision;
* application startup logs;
* latest shutdown state;
* health endpoint response;
* system-status response;
* pending outbox count;
* active/expired lease counts;
* workflow states;
* emergency-stop state;
* active process list where relevant;
* relevant audit records;
* exact error message;
* reproduction steps.

*Note: Never print or share full secrets.*

## Initial triage decision tree

1. **Does the application start?** (If NO -> [Application will not start](#application-will-not-start))
2. **Is the database reachable?** (If NO -> [Database unavailable or locked](#database-unavailable-or-locked))
3. **Is schema current?** (If NO -> [Schema revision mismatch](#schema-revision-mismatch))
4. **Is health green but UI stale?** (If YES -> [WebSocket connected but frontend stale](#websocket-connected-but-frontend-stale))
5. **Are workflows interrupted?** (If YES -> [Interrupted workflow recovery](#interrupted-workflow-recovery))
6. **Are leases expired or stuck?** (If YES -> [Expired or stuck task leases](#expired-or-stuck-task-leases))
7. **Is outbox backlog growing?** (If YES -> [Pending outbox backlog](#pending-outbox-backlog))
8. **Is emergency stop active?** (If YES -> [Emergency stop active unexpectedly](#emergency-stop-active-unexpectedly))
9. **Are worker processes unmanaged?** (If YES -> [Worker registered but not functioning](#worker-registered-but-not-functioning))
10. **Is state potentially corrupted?** (If YES -> [Suspected data corruption](#suspected-data-corruption))

## Safe backup before intervention

1. **Identify writers:** Identify any running API or Worker instances and stop them gracefully (Ctrl+C).
2. **Locate database:** Look at your `JARVIS_DATABASE_URL` environment variable or defaults (e.g., local sqlite file).
3. **Copy files:** Stop or quiesce all writers first. For SQLite, copy the `.db` file, `.db-wal`, and `.db-shm` files if they exist, or use a SQLite-supported backup procedure if available.
   **Unix shell:**
   ```bash
   cp jarvis.db jarvis.db.backup_20260724_abc123
   cp jarvis.db-wal jarvis.db-wal.backup_20260724_abc123 # if exists
   cp jarvis.db-shm jarvis.db-shm.backup_20260724_abc123 # if exists
   ```
   **PowerShell:**
   ```powershell
   Copy-Item jarvis.db -Destination jarvis.db.backup_20260724_abc123
   Copy-Item jarvis.db-wal -Destination jarvis.db-wal.backup_20260724_abc123 -ErrorAction SilentlyContinue
   Copy-Item jarvis.db-shm -Destination jarvis.db-shm.backup_20260724_abc123 -ErrorAction SilentlyContinue
   ```
4. **Verify copy:** Check that the copied file has a nonzero size (`ls -lh` or `Get-Item`).
5. **Security:** Never commit the backup file to Git. Redact secrets if sharing it.

## Application will not start

* **Evidence:** Startup logs, `JARVIS_DATABASE_URL`, Python environment.
* **Diagnosis:**
  - Import error: Verify `pip install -e ".[dev]"` in the active venv.
  - Port already in use: Another API instance is likely running.
  - Migration failure: Schema is incompatible.
  - SQLite lock: See [Database unavailable or locked](#database-unavailable-or-locked).
* **Non-destructive checks:** Run `alembic current` to check DB state.
* **Stop condition:** Do not proceed to `alembic upgrade head` without a backup.
* **Correction:** Kill conflicting processes gracefully. Fix environment variables.

## Database unavailable or locked

* **Identify writer:** In SQLite, a lock typically means another process has an open write transaction.
* **Kill safety:** Do not use `kill -9` indiscriminately on arbitrary Python PIDs. Use `lsof <database_file>` or Windows Resource Monitor to find the specific PID locking the file, and terminate *that* process gracefully if possible.
* **Restoration:** If the file is missing or path invalid, verify `JARVIS_DATABASE_URL` absolute path (use an absolute path, normalize backslashes where necessary, and verify the parsed SQLAlchemy URL points to the intended file).

## Schema revision mismatch

* **Check:** Run `alembic current` vs `alembic heads`.
* **Behind head:** Run `alembic upgrade head`. (Always backup first).
* **Ahead of code:** You have checked out an older branch but have a newer database. **Do not downgrade a real database.** Check out the correct branch or point to a fresh database for testing.
* **Missing revision table:** Do not manually stamp unless absolutely instructed by an owner, as it breaks the migration chain. Do not delete the `alembic_version` table.

## Dirty or unclean shutdown

* **Diagnosis:** Startup reports prior unclean shutdown or interrupted workflows.
* **Action:**
  1. Preserve previous logs.
  2. Start the application once under observation.
  3. The `mark_interrupted_workflow()` logic will automatically set `running` workflows to `recovery_required`.
  4. Inspect the System status.
  5. Avoid looping restarts; it will only re-trigger recovery.

## Interrupted workflow recovery

* **Diagnosis:** Workflow marked `recovery_required` after a backend restart.
* **Recovery:**
  1. Inspect the durable workflow (`workflow_runs`) and the latest checkpoint (`workflow_checkpoints`).
  2. Inspect the latest `audit_events`.
  3. Go to the UI System page and trigger the explicit "Resume" command.
  4. The operator should use supported API commands to resume if the checkpoint allows. (Note: "exact uncommitted step" resumption is not fully implemented).
  5. **Prohibited shortcut:** Never manually update a workflow row to `completed` via SQL.

## Expired or stuck task leases

* **Diagnosis:** Task is held by a worker that has died, or attempt count is inconsistent.
* **Recovery:**
  1. Automatic background sweep is not currently implemented on main. A later explicit `acquire` operation or explicit API call is required to reclaim expired leases. (Status: Planned)
  2. If a lease appears permanently stuck, verify the system time and the lease duration settings.
  3. If a worker process is gone, wait for the lease to expire (default `JARVIS_TASK_LEASE_SECONDS`). The task will automatically requeue.
  4. **Prohibited:** PID absence alone does not authorize manual database deletion of a lease row. Wait for the control plane to expire it naturally.

## Worker registered but not functioning

* **Diagnosis:** Worker in `workers` table shows online but processes no work.
* **Recovery:**
  1. Inspect `workers` last heartbeat.
  2. First reconcile durable task/lease state. Let the sweeper clean up the lease.
  3. Ensure the process is actually stopped. Gracefully terminate it if stuck.
  4. **Prohibited:** Unfinished supervisor prototype behavior must not be treated as production-safe task resolution.

## Pending outbox backlog

* **Diagnosis:** Pending count increasing on System status, dispatcher failing.
* **Recovery:**
  1. Inspect the `outbox_events` table for oldest pending records and their error attempts.
  2. Identify if it's a poison message or a downed subscriber (e.g., WebSocket disconnects).
  3. Restart the dispatcher/API cleanly.
  4. Verify the backlog decreases.
  5. **Prohibited:** Never delete pending outbox rows solely to clear health warnings. Wait for the event to exhaust its retry count and fail permanently, or fix the subscriber.

## WebSocket connected but frontend stale

* **Diagnosis:** Backend state is correct (HTTP GET shows new data), but UI is stale.
* **Recovery:**
  1. Inspect the WS connection in browser DevTools.
  2. Refresh the browser page to fetch a clean HTTP snapshot.
  3. Verify the frontend store matches the backend.
  4. Do not modify the database to fix a pure UI display issue. (SEV-4).

## Context Assembly stuck, failed, or review-required

* **Diagnosis:** Assembly is `review_required` or withheld.
* **Recovery:**
  1. Inspect the durable `context_assemblies` report.
  2. Determine if review is intentionally required (e.g. injection found, policy conflict).
  3. **Prohibited:** Never bypass review by manually editing the `state` in the database.
  4. Correct the source inputs and submit a new authorized command.

## Approval stuck or conflicting

* **Diagnosis:** Task blocked awaiting approval, duplicate decision suspected.
* **Recovery:**
  1. Inspect `approvals` and `audit_events`.
  2. Verify the deciding actor's authorization.
  3. Retry the approval decision (idempotency prevents double application).
  4. Do not directly update the target state without the authorized command.

## Emergency stop active unexpectedly

* **Diagnosis:** UI shows emergency stop active.
* **Recovery:**
  1. Confirm durable system state (`system_state.emergency_stop`).
  2. Inspect the audit logs for the actor/reason.
  3. Clear it only through the supported API UI "Resume System" command.
  4. **Prohibited:** Clearing emergency stop must not erase its history from audit logs.

## Health endpoint reports schema or subsystem unhealthy

* **Diagnosis:** Endpoint returns Degraded.
* **Recovery:**
  - *Schema*: Check `alembic current`. (See Schema mismatch).
  - *Database*: Check file locks. (See Database unavailable).
  - *Outbox*: Check pending queues. (See Pending outbox).
  - Use authoritative evidence for the specific degraded subsystem.

## Duplicate entities, events, or operations suspected

* **Diagnosis:** Duplicate active leases, two identical tasks.
* **Recovery:**
  1. Check IDs, idempotency keys, and audit correlation IDs.
  2. Identify if it is merely duplicate WS delivery (tolerable) or duplicate durable execution in SQLite (SEV-1).
  3. If durable duplication (e.g. two active leases for one task), stop writes immediately. Escalate to SEV-1.

## Bootstrap or seed data problems

* **Diagnosis:** User-modified records overwritten on startup, duplicate seeds.
* **Recovery:**
  1. Inspect `app.services.seed`.
  2. Current application logic should preserve user changes to un-seeded records.
  3. Test reseeding logic against a copied database. Do not delete all data manually.

## Suspected data corruption

* **Indicators:** Contradictory task/lease states, invalid foreign keys, missing required JSON fields, `schema revision inconsistent`.
* **Required response (SEV-1):**
  1. Stop all writers (kill API and Workers).
  2. Preserve database and logs.
  3. Record SHA.
  4. Do not run automatic cleanups.
  5. Do not manually edit hex.
  6. Reproduce against a backup copy.
  7. Escalate with exact evidence.

## Safe reset versus destructive reset

* **Safe operational recovery:**
  - Restart dispatcher.
  - Reconnect client (refresh browser).
  - Let lease sweeper run automatically.
  - Run `alembic upgrade head` after a backup.
* **Destructive recovery (Require Approval):**
  - Deleting the `jarvis.db` file.
  - Removing audit/outbox records manually.
  - Executing raw `UPDATE` or `DELETE` SQL statements against domain tables.
  - Migration stamping (`alembic stamp`).
  - Using the global UI Reset button (resets simulator state, purges demo tasks). Note: this UI reset preserves user-generated tasks and audit history, but is still considered a major state change.

## Restore from backup

1. Stop all writers (API, Workers).
2. Preserve the currently failed database (rename it to `.failed`).
3. Verify the backup file size and revision.
4. Copy the backup to the expected `JARVIS_DATABASE_URL` path.
5. Start a local test API instance.
6. Verify schema health, representative tasks, and workflow states.
7. Only then resume normal operations.

## Process cleanup safety

* Do not terminate a process by PID alone.
* Verify command line, parent, executable, start/create time, and working directory (e.g., using `ps -f -p <PID>` or Process Explorer).
* Prefer graceful stop (`SIGTERM` or `Ctrl+C`).
* Confirm the process does not belong to another distinct application running locally.
* Terminating a process does **not** update the task state. Reconcile durable lease state via the control plane APIs afterward.

## Verification after recovery

Always verify:
* [ ] Application starts cleanly.
* [ ] Database is reachable.
* [ ] Schema is current (Health is green).
* [ ] System status is expected.
* [ ] No unexpected emergency stop active.
* [ ] Workflows are consistent.
* [ ] No duplicate active leases exist.
* [ ] Outbox backlog is stable or decreasing.
* [ ] Frontend refreshes cleanly without staleness.
* [ ] Clean shutdown succeeds.
* [ ] Exact recovery actions documented.

## Recovery incident record template

```markdown
* Incident ID: [Date-BriefSummary]
* Date/Time:
* Operator:
* Repository SHA:
* Branch:
* Database backup path:
* Severity:
* Symptoms:
* Authoritative state observed:
* Relevant entity IDs:
* Logs collected:
* Suspected cause:
* Actions taken:
* Commands run:
* State before:
* State after:
* Validation results:
* Unresolved risks:
* Follow-up PR/issues:
* Approval for destructive action (if any):
* Final disposition:
```

## Escalation triggers

Stop and request owner review if you encounter:
* Suspected duplicate durable execution (SEV-1).
* Two active leases for one task.
* Audit or outbox inconsistency with domain state.
* Migration history ambiguity.
* Secret exposure.
* Corrupted database files.
* Unmanaged processes that cannot be confidently identified.
* Need for manual SQL edits.
* Need to downgrade or stamp migrations.
* Recovery behavior differing from documented code.
* PR #11 or #13 integration assumptions arising in production.

## Known limitations and future runbook additions

* No verified automated backup tool currently exists in the codebase.
* Limited multi-process testing in SQLite.
* Unfinished supervisor integration (Phase 2B prototype).
* Unfinished filesystem security work.
* No external broker recovery procedures (currently internal outbox only).
* No dead-letter queue routing for permanently failed outbox events.
* No formal disaster-recovery objectives.

## Cross-reference

* [State Ownership and Authority Boundaries](../architecture/state-ownership-boundaries.md)

Every recovery action must reconcile toward the authority defined in the ownership boundaries document.
