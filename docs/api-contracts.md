# HTTP contracts

OpenAPI at `/openapi.json` is authoritative. All successful responses use `{ "data": ..., "meta": { "schemaVersion": "1.0" } }`. Domain failures use `{ "error": { "code", "message", "details" } }` with 404 for unknown IDs, 409 for invalid state/idempotency conflicts, 403 for prohibited black risk, and 423 for emergency-stop restrictions.

Routes:

- Health/system: `GET /api/health`, `GET /api/system/status`, `POST /api/system/emergency-stop`, `POST /api/system/resume`
- Departments: `GET /api/departments`, `GET /api/departments/{id}`
- Agents: `GET /api/agents`, `GET /api/agents/{id}`, `POST /api/agents/temporary`
- Tasks: `GET /api/tasks`, `GET /api/tasks/{id}`, `POST /api/tasks`, and `POST /api/tasks/{id}/{pause|resume|retry|cancel}`
- Context: `GET /api/context/assemblies`, `GET /api/context/assemblies/{id}`, and `POST /api/context/assemblies`
- Workers: `GET /api/workers`, `POST /api/workers`, `POST /api/workers/{id}/{heartbeat|drain|stop}`, and `POST /api/workers/{id}/tasks/acquire`
- Task leases: `POST /api/tasks/{id}/lease/{renew|release|complete|fail}`
- Approvals: `GET /api/approvals`, `GET /api/approvals/{id}`, and `POST /api/approvals/{id}/{approve|reject|edit}`
- History: `GET /api/audit-events`, `GET /api/artifacts`, `GET /api/notifications`, `POST /api/notifications/{id}/read`
- Simulator: `POST /api/simulator/{start|pause|resume|reset|failure|approval}`
- Events: `WS /ws/events`
- Identity and authorization: versioned routes under `/api/identity` for durable identities and lifecycle, rank/role/permission/capability/team definitions, assignments, permission evaluation, bounded hierarchy traversal, resource access policy evaluation, and paginated identity audit history. See [agent identity and RBAC](agent-identity-rbac.md) and OpenAPI for exact contracts.

Approvals are idempotency-guarded: processed, expired, unknown, black-risk, or emergency-blocked decisions never execute. A pending approval discovered past its expiration commits a durable `expired` transition before returning `APPROVAL_EXPIRED`; subsequent attempts return `APPROVAL_ALREADY_PROCESSED`. No Phase 1 command performs a real external action.

Simulator start accepts only idle state with emergency stop inactive. Running, paused, recovery-required, completed, and failed states return typed conflicts without creating a run, checkpoint, audit, or outbox event. Controlled failure is terminal: active or paused work commits a failed checkpoint and becomes ineligible for resume, while idle failure persists system/task state without fabricating a workflow run.

Phase 2A mutation routes accept an optional, printable `Idempotency-Key` header of
at most 200 characters for task creation, approval decisions, task retry,
temporary-agent creation, simulator start, and reset. Same-key/same-request calls
replay the durable response; changed content returns `IDEMPOTENCY_KEY_CONFLICT`
(409). System status additively reports storage, migration, event-session, outbox,
checkpoint, and recovery fields. Health distinguishes process, database, schema,
dispatcher, and recovery state without exposing database paths.

Context creation also accepts `Idempotency-Key`. Its typed request binds an existing task and project to a bounded policy and source set. A new durable assembly returns 201; identical canonical input already stored returns 200 without a duplicate audit or event. `completed` responses include the sanitized `modelRequest`; `review_required` responses withhold it and retain the manifest/report. Stable context errors include `CONTEXT_PROJECT_MISMATCH` (409), configured size/source/token policy errors (422), and the normal task/assembly not-found codes (404). OpenAPI defines the full context request, response, manifest, and model-request shapes.

Context preparation reads current durable task input. Its commit writes only the
assembly, audit, event, and keyed response, preserving unrelated task and agent
updates made by a separate worker. The same transaction rechecks the task's
project and request; changed input returns `CONTEXT_TASK_CHANGED` (409) without
storing a stale assembly. API startup and shutdown similarly update only lifecycle
columns, preserving worker results, event cursors, and emergency-stop state.

Reset hashing uses only the stable client action, never event-session or workflow state. An in-progress claim is owned by the request that created it; duplicate in-progress or conflicting requests cannot abandon another request's claim.

For every keyed mutation, the terminal idempotency response is written by the same unit of work as the domain rows, audit entry, checkpoint (when present), and transactional outbox event. A failed commit reloads cached domain state from the database and the owning request abandons only its uncompleted claim; a committed command is immediately replayable even if response delivery or outbox publication is interrupted.

Task creation accepts optional `correctionOfTaskId`. It creates a fresh `queued`
task containing the operator's corrected title, description/request, and priority,
and inherits the existing source task's `projectId`. The source must exist and be
`under_review`, `failed`, `cancelled`, or `completed`; other states return
`TASK_CORRECTION_NOT_ALLOWED` (409). Unknown sources return `TASK_NOT_FOUND` (404).
The transaction rechecks source eligibility and project; a concurrent project
change returns `TASK_CORRECTION_SOURCE_CHANGED` (409). The source task, results,
artifacts, runtime, review state, and permissions remain unchanged. Creation does
not prepare or queue a model run; the operator does those separately for the new
task. The durable correction link appears on the task, its `task.created` outbox
event, and creation audit payload. It is separate from parent/child delegation.
Only the new task is inserted, so creation cannot flush stale API task or agent
state over newer worker data. A correction and its audit, event, and keyed retry
response commit atomically. Replaying the same key returns the same task even
after restart or a lost acknowledgement; changing the correction source or request
under that key conflicts. Absent/null correction fields are omitted during
serialization, preserving legacy creation hashes and response payloads.

Pending idempotency claims carry a durable 30-second lease by default (`JARVIS_IDEMPOTENCY_LEASE_SECONDS`). Same-request retries cannot take an unexpired lease. After expiration, one requester atomically renews the existing claim; ownership is bound to the exact lease token so stale requests cannot abandon or complete a reclaimed claim. Completed responses remain durable and canonical-request conflicts remain unchanged.

Failed outbox deliveries are selected only while `publishAttemptCount` is below `JARVIS_OUTBOX_MAX_ATTEMPTS` (default 10). Exhausted rows remain durable and visible as failed for operator inspection but are not retried on later dispatcher polls or restarts.

Health and system status report exhausted outbox rows separately from the compatible aggregate pending count. Any exhausted row degrades health until an operator repairs or explicitly reconciles it; healthy eligible envelopes continue dispatching. Dispatch uses the durable row ID for attempt accounting even when an envelope's embedded ID is corrupted.

Health additively reports context readiness/count. System status additively reports the durable context assembler state and assembly/source/security counts. Review-required content is a domain outcome and does not by itself make the API unhealthy.

Phase 2B workers register a stable `instanceId`, then acquire an eligible task before processing it. Acquisition returns the compatible task plus a capability-bearing `leaseToken`; only the matching worker/token pair may renew, release, complete, or fail that attempt. Tokens are never placed in audit or event payloads—only a one-way fingerprint is recorded. A stale, expired, cancelled, or superseded token returns `TASK_LEASE_LOST` (409). Repeating a successful completion with the same attempt token is idempotent.

Workers in `draining` state cannot acquire work and release their active leases. Cancellation atomically revokes an active lease before returning. Acquisition returns `data: null` for an empty or dependency-blocked queue. Lease duration defaults to `JARVIS_TASK_LEASE_SECONDS`; callers may request a bounded override. Health and system status add active worker, active lease, expired lease, and stale worker counts without changing existing fields.

New autonomous runtime create/queue commands require the durable task to remain
`queued` or `retrying` within the command transaction. Otherwise they return
`AUTONOMOUS_TASK_NOT_READY` (409) without a run/event/audit/idempotency mutation.
Already accepted command IDs remain replayable after task completion. General
runtime commands without autonomous execution keep their existing behavior.

Autonomous execution specifications accept optional `response_format: planning_review_json_v1`.
It enables bounded planning JSON generation preferences; it adds no tools or remote access.
Absent values are omitted during serialization so legacy persisted commands and checkpoints
retain their hashes. New values participate in command identity and execution-request hashing.

## Explicit workspace tool execution (integration checkpoint)

`GET /api/tool-workspaces` returns configured aliases and their allowed tools/read/write
prefixes (`workspaceId`, `displayName`, `allowedTools`, `readPrefixes`, `writePrefixes`,
`ready`, `reasonCode`). Host paths are never returned. This read-only capability list
requires no runtime actor; all execution and result endpoints authenticate
`X-Jarvis-Actor-Id` through the existing identity service.

An autonomous runtime with `execution_type: workspace_plan` must capture
`response_format: workspace_plan_json_v1`. Its result retains all planning/review fields
and adds one to eight fixed `steps`: `{tool, path, content?, expectedContentHash?}`. Tools
are `workspace.list`, `workspace.read`, `workspace.write`, and `workspace.report`.
`ModelExecutionResult.resultHash` identifies the exact reviewed plan. Existing planning
requests omit the new fields when absent, preserving their recorded request hashes.

`POST /api/tool-executions/authorize` accepts:

```json
{
  "commandId": "operator-authorization-1",
  "sourceExecutionId": "exec-...",
  "expectedPlanHash": "64 lowercase hexadecimal characters",
  "scope": {
    "workspaceId": "lab",
    "allowedTools": ["workspace.list", "workspace.read", "workspace.report"],
    "readPrefixes": ["inputs"],
    "writePrefixes": ["reports"],
    "maximumBytes": 65536,
    "maximumSteps": 8
  }
}
```

The source must be a completed workspace plan, the actor must be the configured local
worker identity and already hold source create/queue/execute access, and every proposed
step must fit the explicit scope. The trusted local operator action provisions existing
runtime permissions only for a new linked task; existing denials are preserved. It
creates a durable authorization intent, task, audit record and outbox event atomically,
then idempotently creates and queues the existing runtime. Interrupted setup can be
replayed with the same actor and command ID; changed contents return
`IDEMPOTENCY_KEY_CONFLICT`. The source task/runtime stay unchanged, and the new task
inherits its source project.

Responses include `executionId`, `sourceExecutionId`, `sourceTaskId`, `taskId`,
`runtimeRunId`, `targetAgentId`, `workspaceId`, `planHash`, `scope`, `stage`, `steps`,
`artifacts`, bounded `failureCode`, and timestamps. Stages are `preparing`, `queued`,
`running`, `completed`, `failed`, or `paused`. Step records carry their index,
tool/path, `pending|started|completed|failed` status, bounded observation, optional
artifact ID and failure code.

`GET /api/tool-executions?taskId=...` finds both source and execution task history.
`GET /api/tool-executions/{executionId}` reads one durable projection.
`GET /api/tool-artifacts/{artifactId}` returns the descriptor plus verified UTF-8 content.
Artifact descriptors contain `artifactId`, `executionId`, `taskId`, `relativePath`,
`contentHash`, `byteCount`, and `mediaType`. Downloads use the immutable stored content;
they do not widen filesystem read scope or read arbitrary host paths. Source runtime
read access and (once created) linked execution runtime read access are required for
results and artifacts. Source-only readers do not inherit workspace observations.

The existing autonomous worker dispatches authorized tool runs using task leases and
runtime claim/attempt/checkpoint/completion commands. Each file operation is preceded
by a durable started record. Bounded file execution, current policy/lease/emergency
checks and the completed step/outbox commit share the SQLite write boundary. If a
process stops after atomic replacement but before commit, recovery adopts only the
already matching desired content hash. Completed observations and artifacts are not
executed again. Task completion rechecks runtime state and permissions inside its own
transaction. A filesystem failure pauses the task for operator review; correction
means creating and authorizing a new reviewed plan.

This slice executes the fixed steps the operator reviewed. List/read observations are
stored and displayed, but are not fed to another model for synthesis. It exposes no
shell, Python, cloud, network, or unrestricted absolute-path tool.
