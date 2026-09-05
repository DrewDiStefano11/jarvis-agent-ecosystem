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

Reset hashing uses only the stable client action, never event-session or workflow state. An in-progress claim is owned by the request that created it; duplicate in-progress or conflicting requests cannot abandon another request's claim.

For every keyed mutation, the terminal idempotency response is written by the same unit of work as the domain rows, audit entry, checkpoint (when present), and transactional outbox event. A failed commit reloads cached domain state from the database and the owning request abandons only its uncompleted claim; a committed command is immediately replayable even if response delivery or outbox publication is interrupted.

Pending idempotency claims carry a durable 30-second lease by default (`JARVIS_IDEMPOTENCY_LEASE_SECONDS`). Same-request retries cannot take an unexpired lease. After expiration, one requester atomically renews the existing claim; ownership is bound to the exact lease token so stale requests cannot abandon or complete a reclaimed claim. Completed responses remain durable and canonical-request conflicts remain unchanged.

Failed outbox deliveries are selected only while `publishAttemptCount` is below `JARVIS_OUTBOX_MAX_ATTEMPTS` (default 10). Exhausted rows remain durable and visible as failed for operator inspection but are not retried on later dispatcher polls or restarts.

Health and system status report exhausted outbox rows separately from the compatible aggregate pending count. Any exhausted row degrades health until an operator repairs or explicitly reconciles it; healthy eligible envelopes continue dispatching. Dispatch uses the durable row ID for attempt accounting even when an envelope's embedded ID is corrupted.

Health additively reports context readiness/count. System status additively reports the durable context assembler state and assembly/source/security counts. Review-required content is a domain outcome and does not by itself make the API unhealthy.

Phase 2B workers register a stable `instanceId`, then acquire an eligible task before processing it. Acquisition returns the compatible task plus a capability-bearing `leaseToken`; only the matching worker/token pair may renew, release, complete, or fail that attempt. Tokens are never placed in audit or event payloads—only a one-way fingerprint is recorded. A stale, expired, cancelled, or superseded token returns `TASK_LEASE_LOST` (409). Repeating a successful completion with the same attempt token is idempotent.

Workers in `draining` state cannot acquire work and release their active leases. Cancellation atomically revokes an active lease before returning. Acquisition returns `data: null` for an empty or dependency-blocked queue. Lease duration defaults to `JARVIS_TASK_LEASE_SECONDS`; callers may request a bounded override. Health and system status add active worker, active lease, expired lease, and stale worker counts without changing existing fields.

Autonomous execution specifications accept optional `response_format: planning_review_json_v1`.
It enables bounded planning JSON generation preferences; it adds no tools or remote access.
Absent values are omitted during serialization so legacy persisted commands and checkpoints
retain their hashes. New values participate in command identity and execution-request hashing.
