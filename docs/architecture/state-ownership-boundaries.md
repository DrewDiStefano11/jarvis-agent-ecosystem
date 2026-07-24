# State Ownership and Authority Boundaries

**Status:** Current architecture reference
**Document basis (SHA reviewed):** 567c59a6ce47f73383c093e55e72715b7998e958
**Last verified against:** July 24, 2026
**Intended audience:** Core contributors, code reviewers, and autonomous agents

> **Warning:** This document does not replace exact code and migration review. If the current implementation differs from these rules, this document must be revalidated and updated, or the implementation must be corrected. Future implementation changes require these documents to be revalidated.

## Executive summary

The system operates under a strict state-ownership model designed to prevent split-brain scenarios and overlapping sources of truth:
* The application control plane and its durable database own authoritative business and orchestration state.
* The event broker and WebSocket layer distribute state changes but do not own them.
* The frontend renders and caches state but does not own durable truth.
* Worker processes execute work but do not own task orchestration.
* The worker-supervisor prototype owns only process lifecycle when integrated correctly.
* Filesystem sandbox code governs permitted file operations but does not own task/workflow state.
* Audit and outbox records are durable historical/publication records, not alternate mutable domain state.
* Context Assembler produces and persists governed model context but does not execute tasks or become a general orchestrator.

## Terminology

* **Authoritative state:** The single source of truth for a domain entity, whose mutation defines the system's actual reality.
* **Durable state:** State that is persisted to disk (e.g., SQLite WAL/tables) and survives application restart.
* **Derived state:** Information computed or aggregated from authoritative state, which can be safely recalculated.
* **Cached state:** A copy of state held in memory (e.g., `InMemoryRepository`) for read performance, invalidatable by durable updates.
* **Replicated state:** State copied to another component (e.g., frontend stores via WebSocket) for localized reads.
* **Ephemeral state:** State that exists only during process runtime and is intentionally lost on restart.
* **Display state:** Visual representation logic running on a client, driven by replicated state.
* **Control plane:** The central API and repository services responsible for persisting and orchestrating authoritative state.
* **Worker runtime:** A remote or isolated process that acquires work, executes business logic, and reports outcomes.
* **Process supervisor:** A system component that governs the OS lifecycle of worker processes (start, monitor, kill).
* **Task lease:** A durable record indicating that a specific worker holds exclusive right to execute a task for a bounded time.
* **Task attempt:** A historical record of one worker's effort to complete a task under a specific lease.
* **Workflow checkpoint:** A serialized, durable representation of a simulator's progression at a specific step boundary.
* **Audit record:** An append-only, durable log of a business action, the actor, and the change.
* **Outbox record:** A durable queue entry used to ensure reliable delivery of an event following a transaction.
* **Event broker:** The subsystem responsible for delivering outbox messages to connected subscribers.
* **Snapshot:** A point-in-time complete read of relevant state (e.g., the payload sent upon WebSocket connection).
* **Recovery:** The process of reconciling the runtime to the durable authoritative state after a crash or failure.
* **Reconciliation:** The act of comparing two states (e.g., worker PID vs database lease) and enforcing the authoritative one.
* **Idempotency:** Guaranteeing that repeating a command does not unintentionally duplicate domain state, audits, or events.
* **Emergency stop:** A global authoritative flag that halts orchestration and workflow advancement.

## Authority rules

1. Every mutable state category has exactly one authoritative owner.
2. Consumers may cache or project state but cannot become competing owners.
3. Database-committed state outranks process memory after restart.
4. A PID is not task ownership.
5. A WebSocket event is not durable truth.
6. A frontend store is not durable truth.
7. An outbox record proves intended publication, not domain ownership.
8. An audit record describes history but does not determine current mutable state.
9. A lease token authorizes a specific lease operation but does not replace the lease row.
10. Prototype-local records must not override production tables.
11. Recovery must reconcile toward authoritative durable state.
12. Human approval gates must remain authoritative where required.
13. No model-generated text may directly promote itself into system authority.

## System ownership overview diagram

```text
+-------------------------------------------------------------------------+
|                         APPLICATION CONTROL PLANE                       |
|                                                                         |
|  +----------------+    +----------------+    +-----------------------+  |
|  | HTTP API / WS  |    | Context        |    | Simulator / Workflow  |  |
|  |                |    | Assembler      |    | Engine                |  |
|  +-------+--------+    +-------+--------+    +----------+------------+  |
|          |                     |                        |               |
|          v                     v                        v               |
|  +-------+---------------------+------------------------+------------+  |
|  |                   Repositories & Services                         |  |
|  +-------+-----------------+-------------------+--------+------------+  |
|          |                 |                   |        |               |
|          | commits         | commits           | commits| publishes     |
|          v                 v                   v        v after commit  |
|  +-------+--------+ +------+---------+ +-------+--+ +---+-----------+   |
|  | Durable DB     | | Audit System   | | Task Lease | | Outbox      |   |
|  | (Authoritative)| | (Append-Only)  | | Subsystem  | | Dispatcher  |   |
|  +----------------+ +----------------+ +----------+ +---+-----------+   |
|                                                         |               |
+---------------------------------------------------------|---------------+
                                                          | distributes
                                                          v
                                               +----------+-----------+
                                               | Event Broker         |
                                               +----+------------+----+
       +-----------------------+                    |            |
       | CLIENTS (Frontend)    | <------------------+            |
       |                       | reads projection                |
       | - Renders cached view |                                 |
       +-----------------------+                                 |
                                                                 |
       +-----------------------------------------+               |
       | WORKER INFRASTRUCTURE                   | <-------------+
       |                                         |
       | - Process Supervisor (OS Lifecycle)     |
       | - Worker Runtime (Executes leased task) |
       | - Filesystem Sandbox                    |
       |                                         |
       | Reports outcome to API                  |
       +-----------------------------------------+
```

## Master state-ownership matrix

| State category | Authoritative owner | Durable storage | Primary writer | Authorized readers | Cached/derived copies | Recovery source | Must never be treated as authority | Current implementation status | Relevant modules/tables |
|---|---|---|---|---|---|---|---|---|---|
| Departments | Control plane | `departments` table | Repository/API | Any | InMemoryRepository, Frontend store | DB | Frontend state | Implemented | `app.db.models.DepartmentRow` |
| Permanent agents | Control plane | `agents` table | Repository/API | Any | InMemoryRepository, Frontend store | DB | UI presence/sprites | Implemented | `app.db.models.AgentRow` |
| Agent capabilities | Control plane | DB via properties | Repository/API | Any | Derived from agents | DB | Frontend representation | Implemented | `app.db.models.AgentRow` |
| Agent status | Control plane | `agents` table | Repository/API | Any | InMemoryRepository, Frontend store | DB | Frontend display state | Implemented | `app.db.models.AgentRow` |
| Tasks | Control plane | `tasks` table | Repository/API | Any | InMemoryRepository, Frontend store | DB | Worker memory, process PID | Implemented | `app.db.models.TaskRow` |
| Task assignments | Control plane | `task_agents` table | Repository/API | Any | Frontend store | DB | Worker supervisor memory | Implemented | `app.db.models.TaskAgentRow` |
| Task dependencies | Control plane | `task_dependencies` | Repository/API | Any | Frontend store | DB | Context Assembler requests | Implemented | `app.db.models.TaskDependencyRow` |
| Task results | Control plane | `tasks` table | API (from workers)| Any | InMemoryRepository | DB | Worker output logs | Implemented | `app.db.models.TaskRow` |
| Workflow definitions | Application code | Hardcoded schemas | Developers | Simulator | N/A | Codebase | DB rows for workflows | Implemented | `app.simulator` |
| Workflow runs | Control plane | `workflow_runs` | Simulator Engine | API | InMemoryRepository | DB | Simulator memory | Implemented | `app.db.models.WorkflowRunRow` |
| Workflow checkpoints| Control plane | `workflow_checkpoints`| Simulator Engine | API / Workers | InMemoryRepository | DB | Active process state | Implemented | `app.db.models.WorkflowCheckpointRow` |
| Approvals | Control plane | `approvals` table | API | Any | InMemoryRepository, Frontend | DB | Context Assembly text | Implemented | `app.db.models.ApprovalRow` |
| Artifacts | Control plane | `artifacts` table | API / Workers | Any | InMemoryRepository | DB | Filesystem sandbox contents| Implemented | `app.db.models.ArtifactRow` |
| Notifications | Control plane | `notifications` | API | Any | InMemoryRepository, Frontend | DB | WebSocket events | Implemented | `app.db.models.NotificationRow` |
| System state | Control plane | `system_state` table| Repository/API | Any | InMemoryRepository | DB | Frontend metrics | Implemented | `app.db.models.SystemStateRow` |
| Emergency-stop state| Control plane | `system_state` table| API | Any | InMemoryRepository, Frontend | DB | Process signals | Implemented | `app.db.models.SystemStateRow` |
| Workers | Task-lease service | `workers` table | Worker API | Supervisor | Cache in supervisor | DB | OS Process List | Implemented | `app.db.models.WorkerRow` |
| Task leases | Task-lease service | `task_leases` table | Lease service | Workers / API | N/A | DB | Supervisor local state | Implemented | `app.db.models.TaskLeaseRow` |
| Task attempts | Task-lease service | `task_attempts` | Lease service | Auditing | N/A | DB | Worker local state | Implemented | `app.db.models.TaskAttemptRow` |
| Worker heartbeat | Task-lease service | `workers` (last_seen)| Worker API | Lease sweeper | Supervisor | DB | Local PID liveliness | Implemented | `app.db.models.WorkerRow` |
| Context Assembly requests | Context Assembler | `context_assemblies`| Assembler API | None (redacted) | N/A | DB | Raw user input | Implemented | `app.db.models.ContextAssemblyRow` |
| Context Assembly manifests | Context Assembler | `context_assemblies`| Assembler API | Auditing / UI | N/A | DB | N/A | Implemented | `app.db.models.ContextAssemblyRow` |
| Context Assembly reports | Context Assembler | `context_assemblies`| Assembler API | Security | N/A | DB | N/A | Implemented | `app.db.models.ContextAssemblyRow` |
| Context Assembly model requests | Context Assembler | `context_assemblies`| Assembler API | LLM Providers | N/A | DB | Generated text output | Implemented | `app.db.models.ContextAssemblyRow` |
| Audit records | Control plane | `audit_events` | API | Auditing | InMemoryRepository, Frontend | DB | Transient logs | Implemented | `app.db.models.AuditEventRow` |
| Idempotency records | Control plane | `idempotency_records`| API | API | N/A | DB | In-memory sets | Implemented | `app.db.models.IdempotencyRecordRow`|
| Outbox events | Control plane | `outbox_events` | API / Repositories| Dispatcher / API | N/A | DB | Broker unacknowledged queue| Implemented | `app.db.models.OutboxEventRow` |
| Published-event status | Control plane | `outbox_events` | Dispatcher | Dispatcher | N/A | DB | WebSocket connections | Implemented | `app.db.models.OutboxEventRow` |
| Event broker subscriptions | Event broker | Memory | Broker API | Dispatcher | Ephemeral connection maps| Snapshot / DB | Durable routing rules | Implemented | Memory-only |
| WebSocket connection state | Event broker | Memory | Client | Server | N/A | N/A (reconnects) | Durable session markers | Implemented | Memory-only |
| Frontend stores | Frontend | Client memory | WebSocket / HTTP | React | UI projections | Backend snapshot | Authoritative domain state | Implemented | `apps/web` |
| Simulator runtime tasks | Simulator | Memory | Engine | Engine | N/A | Checkpoints (DB) | Durable DB task state | Implemented | `app.simulator.engine` |
| Filesystem permissions | Filesystem sandbox | Sandbox local config| Orchestrator | Sandbox | N/A | Config | Application authz models | Prototype only | Sandbox prototype |
| File-operation results | Filesystem sandbox | File system | Sandbox code | Worker / UI | Artifact cache | FS | Database artifacts | Prototype only | Sandbox prototype |
| OS process handles | Worker supervisor | Supervisor memory | Supervisor | Supervisor | N/A | OS | Database worker records | Prototype only | `phase-2b-worker-supervisor`|
| PID/create-time identity | Worker supervisor | OS | OS | Supervisor | Supervisor memory | OS | Durable worker identity | Prototype only | `phase-2b-worker-supervisor`|
| Worker-supervisor local state | Worker supervisor | Memory | Supervisor | Supervisor | N/A | DB worker tables | Task authorization | Prototype only | `phase-2b-worker-supervisor`|
| Application configuration | Deployment env | Environment vars | Operator | App Startup | Settings classes | Env | Database state | Implemented | `.env` / `config.py` |
| Migration revision | Alembic | `alembic_version` | Migration tool | Health check | N/A | DB | Application models | Implemented | Alembic |
| Health/status projections | API | Derived | API | External monitors| N/A | DB | Domain truth | Implemented | API routes |
| Bootstrap/seed records | Repositories | Hardcoded / DB | Startup logic | Repositories | DB | Codebase / Config | Mutable business data | Implemented | `app.services.seed` |

## Domain-by-domain ownership sections

### 7.1 Departments and agents
* **Authoritative records:** Owned by the application control plane and durably stored in the `departments` and `agents` database tables.
* **Assignment and capability relationships:** Managed durably in the database.
* **Agent online/busy status:** The control plane strictly governs agent status transitions and persists fields such as `status`, `previous_status`, `current_task_id`, `progress`, `status_message`, and `deployment_status` directly in the database.
* **Derived values:** Any summary aggregations are projections, but the core status fields are durable.
* **Frontend caching:** The frontend may cache agent records but must treat them as read-only projections. Frontend display state is not authoritative.
* **Bootstrap records:** Initial seed data interacts with user-modified data. Startup logic preserves user modifications while ensuring required system roles exist.
* **Non-authority:** What must not be inferred from UI sprites, animations, or UI presence is durable business logic. Visuals are display-only projections.

### 7.2 Tasks
* **Authoritative task state:** The `tasks` database table is the sole source of truth.
* **Legal writers:** Only the control plane API and associated transactional repositories can write task state.
* **Relationships:** Tasks relate to assignments, leases, attempts, approvals, workflows, audits, and outbox strictly through durable database transactions.
* **Task vs process status:** Task status is independent of process status. An executing PID does not mean the task is durably running if the database says otherwise.
* **Idempotency:** Task creation and mutation commands respect idempotency claims in the database.
* **Recovery:** Loaded purely from the database on restart.
* **Worker Process Authority Rule:** A worker process reporting success does not become authoritative until the control-plane transaction commits the result.

### 7.3 Workflows and checkpoints
* **Workflow-run authority:** The `workflow_runs` table is authoritative for the state of a run.
* **Checkpoint authority:** The `workflow_checkpoints` table stores the authoritative JSON payload representing simulator position.
* **Simulator runtime vs durable checkpoint:** Simulator memory is ephemeral. Durable checkpoints are the only trusted source.
* **Restart reconciliation:** Startup reconciles in-memory simulator instances from the latest durable checkpoint.
* **Paused, interrupted, completed, and failed state:** State is strictly defined by the database. An interrupted `running` workflow becomes `recovery_required` upon startup.
* **Auto-resume capabilities:** Auto-resume (if enabled) may pick up from the last checkpoint; it may not fabricate steps or assume completions that lack a checkpoint.
* **Definition vs execution:** Workflow definition (code) dictates behavior, but execution state is purely what is persisted in `workflow_runs` and `workflow_checkpoints`.

### 7.4 Approvals
* **Creators and deciders:** Control plane APIs restrict who can create and decide requests.
* **Authority of status:** The `approvals` table strictly defines whether an action is blocked or permitted.
* **Frontend display:** Display state cannot bypass approval. The backend enforces the gate regardless of UI state.
* **Idempotency and duplicate decisions:** Approval endpoints utilize idempotency keys to prevent duplicate outcomes and conflicting decisions.
* **Audit requirements:** Every approval creation or decision strictly requires a corresponding atomic audit record in the same transaction.

### 7.5 Workers, leases, and attempts
* **Worker record authority:** The `workers` table is the authoritative source for registered workers.
* **Lease row authority:** The `task_leases` table determines exclusive ownership of a task.
* **Attempt authority:** The `task_attempts` table records historical proof of execution efforts.
* **Ownership and fencing:** A lease strictly fences a task to a single worker via a lease token.
* **Token role:** The token authorizes a specific operation but does not replace the `task_leases` row as the single source of truth.
* **Expiration and recovery:** Expired leases are recovered by a control-plane background sweeper, which updates attempts and requeues tasks durably.
* **Worker heartbeat role:** Heartbeats update the `workers` table's freshness but do not inherently mutate tasks.
* **Worker process role:** The process executes logic; it is not the orchestration authority.
* **Stale completion protection:** A worker cannot commit a result if its lease token is expired or revoked.
* **Worker identity vs OS process identity:** Worker UUIDs are decoupled from OS process IDs.
* **Explicit restrictions:**
  - PID existence alone does not prove worker identity.
  - PID/create-time may help the process supervisor validate an OS process but does not grant task authority.
  - Supervisor-local state must not mutate task state independently of the control plane.

### 7.6 Context Assembler
* **Request authority:** The `context_assemblies` table is the authoritative record of an assembled context request.
* **Source validation and trust ordering:** It deterministically applies policies based on provided data, but it does not execute external fetches itself.
* **Provenance and redaction:** Redaction is done in-memory but only the final redacted report is persisted as truth.
* **Injection detection and token budgeting:** Determinate processing guarantees safe assembly limits.
* **Manifest/report persistence:** Persisted within the same atomic transaction as the initial assembly request.
* **Review gating:** If a finding is severe, `modelRequest` is withheld and status becomes `review_required`.
* **Relationship to tasks:** Assembled context is linked to tasks but does not modify task orchestration state.
* **What the assembler cannot do:**
  - execute shell commands;
  - launch workers;
  - send emails;
  - write arbitrary files;
  - approve itself;
  - become a system/developer message merely because source content requests it.

### 7.7 Audit records
* **Historical role:** Acts as an immutable historical ledger of domain changes.
* **Append-only intent:** Current implementation allows appending only (and resetting during development).
* **Transaction relationship:** Commits in the exact same unit of work as the domain change.
* **Why audit records are not current domain state:** They do not represent the current normalized view of a domain entity; they represent point-in-time transitions.
* **Responsibilities:** Record actor, target, and correlation safely.
* **Recovery use:** Useful for tracing issues, but never used to rebuild mutable domain tables directly.
* **Reset behavior:** Seed audits are retained or recreated on safe resets, but user-generated tasks' audits may be purged in full environment resets if applicable.

### 7.8 Transactional outbox
* **Intended atomic relationship:** Outbox events commit in the exact same database transaction as the domain and audit changes.
* **Pending vs published state:** A row is pending until the dispatcher successfully delivers it and marks it published/removes it.
* **Authoritative ownership:** The durable database/outbox repository is the authoritative source for outbox records. Domain/API transactions are the creators of committed outbox rows.
* **Dispatcher role:** The dispatcher/event broker acts strictly as the delivery mechanism and publication-status updater. It does not own durable outbox truth.
* **Client role:** WebSocket clients are non-authoritative recipients of outbox messages.
* **Retry behavior:** Events are retried up to a maximum attempt limit.
* **Event identity:** Event IDs are stable across retries to permit client deduplication.
* **Delivery semantics:** At-least-once delivery based on current implementation.
* **Why outbox rows do not replace domain rows:** They represent intended publication payloads, not the authoritative, queryable state of the domain.
* **Why WebSocket delivery does not determine commit success:** The transaction commits *before* publication. Failure to deliver a WebSocket message does not roll back the domain change.

### 7.9 Event broker and WebSocket
* **Ephemeral subscription state:** Subscription lists live in memory only.
* **Initial snapshot vs live event:** Clients fetch a full authoritative HTTP snapshot upon connection, then apply ordered live events.
* **Reconnect behavior:** Clients request a new snapshot on reconnect.
* **Missed-event recovery:** Recovered strictly through fetching fresh state via HTTP.
* **Client-side caches:** Maintain read-only projections.
* **Absence of durable authority:** No component in this layer owns or persists domain facts.
* **Failure isolation:** Broker failure delays updates but does not corrupt the database.

### 7.10 Frontend state
* **Stores and caches:** Redux/Zustand or context stores are read-only caches of the backend API.
* **Optimistic UI behavior:** Allowed for interaction snappiness, but must roll back if the authoritative backend rejects the command.
* **Refresh/reconnect behavior:** Always defers to a fresh HTTP snapshot on reconnect.
* **Authority boundaries:** Frontend input is merely a request until the control plane validates and commits it.
* **Conflict handling:** If the frontend disagrees with the backend, the backend wins and the frontend must resync.
* **Display projections:** UI sprites, rooms, occupancy, and visual animation must be projections of control-plane state, not independently authoritative models.

### 7.11 Emergency stop
* **Authoritative record:** Owned by the control plane and persisted in the `system_state` table.
* **Activation/clear authority:** Only authorized HTTP commands can activate or clear it.
* **Blocks operations:** Halts simulator steps, new tasks, and approvals.
* **What it does not automatically change:** It does not kill OS processes directly, and it does not rewrite past history.
* **Restart behavior:** Persisted to disk; survives application restarts.
* **Audit and event relationships:** Activation triggers its own transaction, audit, and outbox event.
* **UI representation:** Reflected system-wide in the frontend.

### 7.12 Filesystem sandbox
* **Intended ownership:** Governs capability evaluation, allowed roots, and normalized paths for executing tasks.
* **File-operation result reporting:** Reports outcomes to the API/Worker logs.
* **What it does not own:**
  - tasks;
  - workflows;
  - approvals;
  - agent authority;
  - worker lifecycle;
  - audit history.
* **Limitations:** Filesystem security must account for race conditions and platform-specific link/reparse behavior, but current implementations are not fully hardened (unfinished PR #11).

### 7.13 Worker supervisor
* **Intended narrow ownership:**
  - subprocess launch;
  - process identity validation (using PID/start-time combinations);
  - process liveness;
  - process termination;
  - process-local logs/exit status;
  - supervisor reconciliation with durable worker records.
* **Explicitly prohibited independent ownership:**
  - task queue;
  - task status;
  - task retry policy;
  - workflow state;
  - approval decisions;
  - idempotency;
  - audit history;
  - outbox publication.
* **Integration status:** Current PR #13 repair work must be evaluated independently before full integration. Supervisor prototype behavior is not production-safe authority.

## Write-authority table

| Component | May request task changes | May commit task changes | May update leases | May write audits | May enqueue outbox | May update workflow checkpoints | May update process state | May update frontend state | Restrictions |
|---|---|---|---|---|---|---|---|---|---|
| API route layer | Yes | No | No | No | No | No | No | No | Delegates to Services/Repositories |
| Control-plane Service/Repository | Yes | Yes | Yes | Yes | Yes | Yes | No | No | Only within proper unit of work |
| Simulator / Workflow engine | Yes | Yes (via Repo) | No | Yes | Yes | Yes | No | No | Writes via Repository interfaces |
| Context Assembler | No | No | No | Yes (own audits) | Yes (own events)| No | No | No | Writes assemblies and related only |
| Task-lease service | Yes (queue/retry) | Yes (status) | Yes | Yes | Yes | No | No | No | Only for lease lifecycle logic |
| Startup recovery | Yes (recovery_required)| Yes | No | No | No | No | No | No | Only marks interrupted state |
| Periodic recovery (Lease Sweeper) | Yes (requeue/fail)| Yes | Yes | Yes | Yes | No | No | No | Strict timeout conditions |
| Worker | Yes | No | Yes (via API) | No | No | No | No | No | Must call API to commit |
| Process supervisor | No | No | No | No | No | No | Yes | No | Local OS process state only |
| Filesystem sandbox | No | No | No | No | No | No | No | No | Executes file operations only |
| Outbox dispatcher | No | No | No | No | No | No | No | No | Only updates outbox publish status |
| Event broker | No | No | No | No | No | No | No | No | Read-only routing |
| Frontend | Yes (via API) | No | No | No | No | No | No | Yes (caches) | Read-only projection / API client |

## Read models and projections

* **Repository snapshot:** Source is the database (or InMemory cache). Completely refreshed upon frontend connection. Staleness risk is low if WebSocket is connected. Survives restart natively.
* **Health response:** Live probe of database reachability and Alembic migration state. Refreshed per request. Survives restart.
* **System-status response:** Aggregates database metrics (storage, migration, session, checkpoints, recovery status). Refreshed per request. Survives restart.
* **WebSocket initial state:** Sourced from HTTP snapshot. Mutation rules dictate replacing local state entirely.
* **Frontend stores:** Maintained via snapshot + ordered outbox events. Risk of staleness on disconnect. Does not survive client reload without fetching a new snapshot.
* **Context Assembler summaries:** Read from the `context_assemblies` table.
* **Task-lease counts:** Metrics computed on demand from the `task_leases` table.
* **Outbox metrics:** Pending and failed event counts pulled live from `outbox_events`.
* **Audit history:** Served directly from `audit_events`. Append-only. Survives restart.

## Transaction boundaries

Verified transaction groupings (commit all or rollback all):
* **Domain + Audit + Outbox:** Most standard mutations (e.g., task creation, agent assignment).
* **Domain + Idempotency completion:** When an `Idempotency-Key` is provided, the claim is finalized in the same commit.
* **Task + Lease + Attempt:** Worker acquisition or completion ensures task status, lease status, and attempt history are updated atomically.
* **Context Assembly + Audit + Outbox:** An assembly request commits the manifest, audit, and outbox envelope atomically.
* **Approval decision + Domain change:** Submitting an approval decision updates the `approvals` row and the targeted entity row together.
* **Workflow checkpoint + Audit/Outbox:** Simulator steps commit the run status, the `workflow_checkpoints` payload, and relevant outbox events in one block.

**Important:** An operation must not be described as successfully completed until the authoritative transaction commits.

## Recovery authority

| Failure | Authoritative evidence | Component allowed to recover | Recovery action | Prohibited shortcut |
|---|---|---|---|---|
| Interrupted workflow | `workflow_runs` status `running` on startup | Startup recovery logic | Mark `recovery_required` | Bypassing durable checkpoint |
| Expired lease | `task_leases` expiration vs DB time | Task-lease service (acquire) | Terminate lease on next acquire | Assuming PID absence means task failed |
| Stale worker | `workers` last_seen heartbeat | Worker supervisor (Prototype) | Reconcile OS process / Reclaim lease on next acquire | Worker killing another worker's process |
| Pending outbox | `outbox_events` published=False | Outbox dispatcher | Retry publication | Deleting row to clear errors |
| Incomplete Context Assembly | No row in `context_assemblies` | API/Client | Retry command with idempotency key | Persisting partial context |
| Dirty shutdown | SQLite WAL/DB integrity | SQLite engine | Rollback uncommitted transactions | Manual hex editing database files |
| Schema mismatch | Alembic version vs app models | Database Administrator / Migrations | Run `alembic upgrade head` | Bypassing health check manually |
| Frontend disconnect | WebSocket disconnect event | Frontend application | Fallback to HTTP polling, request full snapshot on reconnect | Assuming local UI is still authoritative |
| Supervisor crash | OS process list | Supervisor daemon | Restart supervisor, reconcile against DB worker rows | Supervisor inventing new tasks |

## Conflict-resolution rules

When sources disagree, the following rules dictate which source wins:
* **Database task state vs. Worker memory:** Database wins. The worker must accept that its lease is gone or the task is finished.
* **Database lease vs. Supervisor process list:** Database wins regarding task authority. If the DB says a lease is expired, the supervisor cannot force it active, though it can kill the stale process.
* **Durable checkpoint vs. Simulator memory:** Durable checkpoint wins. Simulator memory is overwritten by the checkpoint on restart.
* **Outbox record vs. Missing WebSocket event:** Outbox record wins as proof of intent; WebSocket is just a volatile transport.
* **Backend state vs. Frontend store:** Backend state strictly wins.
* **Current migration revision vs. Model expectation:** The deployed code's model expectations dictate what the DB schema *should* be, but the DB's actual revision indicates current truth. Mismatch requires migration or code rollback.
* **Audit history vs. Mutable current state:** Mutable current state wins for current logic. Audit history is a ledger of how it got there.
* **Current `main` code vs. Old prototype branch:** Current `main` is strictly authoritative.

## Anti-patterns

* **Second task queue inside supervisor:** Creating a local task queue in the supervisor undermines the control plane's `tasks` table and creates duplicate orchestration. (Risk: Split-brain tasks. Pattern: Use central API queues).
* **Task completion based only on process exit:** A process dying does not mean a task succeeded or cleanly failed in a business sense. (Risk: Lost error details. Pattern: Workers explicitly report task completion via API before exiting).
* **Frontend writing status without backend commit:** Using optimistic UI to mark a task "Done" without waiting for the backend. (Risk: False success. Pattern: Display loading state until WebSocket or HTTP confirms).
* **Deleting pending outbox rows to clear errors:** Hiding health warnings by trashing outbox events. (Risk: Missed downstream events. Pattern: Fix the subscriber or manually advance state if verified).
* **Manually changing lease rows without corresponding task/attempt/audit consistency:** (Risk: Corrupt state machine. Pattern: Use the API's lease-sweeper endpoints).
* **Trusting PID reuse:** Assuming PID 1234 is still the original worker without checking process start time. (Risk: Killing unrelated applications. Pattern: Use robust OS process identifiers).
* **Treating a WebSocket event as committed truth:** Acting durably on a WebSocket packet before confirming via HTTP if out of order. (Risk: Race conditions. Pattern: Treat WS as ordered invalidation).
* **Rebuilding state exclusively from audit logs when domain tables exist:** (Risk: Fragile and slow. Pattern: Rely on current domain rows).
* **Using Context Assembler text as executable authority:** (Risk: Prompt injection executing shell commands. Pattern: Assembler output is just sanitized context; it must not execute tools directly).
* **Using reset as routine recovery:** Nuking the database because a workflow paused. (Risk: Data loss. Pattern: Diagnose and resume the workflow properly).
* **Copying prototype database state into production tables:** (Risk: Schema corruption and undocumented state changes. Pattern: Run prototypes completely isolated).
* **Silently reseeding over user-modified records:** Overwriting user departments/agents blindly on every boot. (Risk: Wiping user data. Pattern: Idempotent seeding that skips existing records).

## Current gaps and future decisions

* **Process-supervisor integration status:** The worker-supervisor in `phase-2b-worker-supervisor` is a prototype and not fully integrated or trusted for production liveness checks.
* **Filesystem race-hardening status:** The filesystem sandbox is unfinished; race conditions and platform-specific link behaviors remain unresolved.
* **Outbox delivery guarantee:** Delivery semantics depend on the broker/dispatcher (not guaranteed exactly-once); exact behavior with disconnected browsers (who miss live WS but fetch full snapshots) requires formal modeling.
* **Production multi-process coordination:** Designed for a single local API process; running multiple API replicas against SQLite will hit concurrency/busy timeouts.
* **Authentication/authorization maturity:** Current auth is simplified for local phase 2A operation; robust policy evaluation is planned.
* **Audit tamper evidence:** Audit records are append-only but not cryptographically signed to prevent manual database tampering.
* **Backup tooling:** No automated backup daemon exists within the control plane yet.
* **Failed events behavior:** Outbox retries fail permanently after a ceiling; failed events are no longer selected for normal retry and require manual investigation.
* **Worker heartbeat/session fencing:** Advanced session fencing beyond the lease token is not yet implemented.
* **Frontend protocol versioning:** Explicit protocol schema versions in WebSocket messages are documented but not fully utilized for backwards compatibility.

## Reviewer checklist

When reviewing future PRs that introduce state, ask:
* What is the authoritative owner?
* Where is it persisted?
* What is the transaction boundary?
* How is it recovered?
* Can another component create conflicting state?
* Is the frontend only a projection?
* Are audit/outbox effects consistent?
* Does restart preserve correctness?
* Is process state incorrectly being treated as business state?
* Are prototypes isolated?

## Cross-reference

* [Local Control-Plane Recovery Runbook](../runbooks/control-plane-recovery.md)

The recovery runbook applies these ownership rules operationally.
