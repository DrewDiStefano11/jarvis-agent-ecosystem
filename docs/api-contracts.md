# HTTP contracts

OpenAPI at `/openapi.json` is authoritative. All successful responses use `{ "data": ..., "meta": { "schemaVersion": "1.0" } }`. Domain failures use `{ "error": { "code", "message", "details" } }` with 404 for unknown IDs, 409 for invalid state/idempotency conflicts, 403 for prohibited black risk, and 423 for emergency-stop restrictions.

Routes:

- Health/system: `GET /api/health`, `GET /api/system/status`, `POST /api/system/emergency-stop`, `POST /api/system/resume`
- Departments: `GET /api/departments`, `GET /api/departments/{id}`
- Agents: `GET /api/agents`, `GET /api/agents/{id}`, `POST /api/agents/temporary`
- Tasks: `GET /api/tasks`, `GET /api/tasks/{id}`, `POST /api/tasks`, and `POST /api/tasks/{id}/{pause|resume|retry|cancel}`
- Approvals: `GET /api/approvals`, `GET /api/approvals/{id}`, and `POST /api/approvals/{id}/{approve|reject|edit}`
- History: `GET /api/audit-events`, `GET /api/artifacts`, `GET /api/notifications`, `POST /api/notifications/{id}/read`
- Simulator: `POST /api/simulator/{start|pause|resume|reset|failure|approval}`
- Events: `WS /ws/events`

Approvals are idempotency-guarded: processed, expired, unknown, black-risk, or emergency-blocked decisions never execute. No Phase 1 command performs a real external action.

Phase 2A mutation routes accept an optional `Idempotency-Key` header for task creation, approval decisions, task retry, temporary-agent creation, simulator start, and reset. Same-key/same-request calls replay the durable response; changed content returns `IDEMPOTENCY_KEY_CONFLICT` (409). System status additively reports storage, migration, event-session, outbox, checkpoint, and recovery fields. Health distinguishes process, database, schema, dispatcher, and recovery state without exposing database paths.

Reset hashing uses only the stable client action, never event-session or workflow state. An in-progress claim is owned by the request that created it; duplicate in-progress or conflicting requests cannot abandon another request's claim.

For every keyed mutation, the terminal idempotency response is written by the same unit of work as the domain rows, audit entry, checkpoint (when present), and transactional outbox event. A failed commit reloads cached domain state from the database and the owning request abandons only its uncompleted claim; a committed command is immediately replayable even if response delivery or outbox publication is interrupted.
