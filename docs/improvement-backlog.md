# Durable improvement backlog

The Improvement Lab can select one recorded opportunity and admit a native queued
task. Selection is explicit local operator action and grants no execution or
proposal approval. Native tasks own progress; append-only backlog records retain
admission provenance rather than a second status ledger.

## Operator access

Use an active enabled registry identity with native permission on
`administrative_function/improvement_backlog`: action `select_improvement` for
selection and separate action `read_improvement` for inspection. Suggested stable
keys are `self_improvement.select` and `self_improvement.read`. Deny precedence and
lifecycle restrictions remain authoritative. Selection rechecks live permission
inside the SQLite write transaction. Emergency stop blocks admission there too.

First capture/analyze evidence using the [self-improvement CLI](self-improvement.md).
Selection accepts one to eight distinct persisted analysis baseline digests.
Request text cannot replace observations, proposal priority or experiment criteria.
Run from `apps/api` against the existing migrated native database:

```powershell
python -m app.self_improvement.backlog_cli --actor-id <registry-id> select --baseline <baseline-digest> --idempotency-key <unique-command-key>
python -m app.self_improvement.backlog_cli --actor-id <registry-id> list --limit 20
```

The native database setting is used unless `--database-url` precedes the command.
The CLI does not migrate schema. Output is bounded JSON; denial or invalid input
returns exit code 2 without database credentials.

Local HTTP clients POST `/api/self-improvement/backlog/select` with
`X-Jarvis-Actor-Id`, `Idempotency-Key` and body
`{"baseline_ids":["<baseline-digest>"]}`. New admission returns 201;
replay/empty/blocked returns 200 in the native `data` envelope.
GET `/api/self-improvement/backlog?offset=0&limit=20` requires the separate read grant
and returns immutable entries with current native `task_status`. Both routes retain
the loopback-only Improvement Lab boundary. OpenAPI defines their contracts.

## Selection and recovery

Proposed, needs-evidence and ready-for-review proposals are eligible. Approved,
rejected, superseded and evaluated proposals are excluded. Ordering uses recorded
critical/high/medium/low priority, then evidence confidence, affected subjects,
frequency, newer baseline capture and stable IDs. No aggregate score or model
recommendation replaces the evidence graph.

Incomplete evidence creates `gather_evidence` work. A ready-for-review proposal with
a frozen experiment creates `prepare_experiment` work, without approving a change.
The transaction revalidates baseline/proposal/weakness/evidence references,
experiment digest, work classification and deterministic entry/task identifiers.

A proposal is admitted once per baseline. Semantic scope suppresses active duplicates
across later captures, including protected tasks under review and failed tasks
with operator retries remaining. Completed, cancelled or retry-exhausted failed
work can be reconsidered only through a new baseline/proposal. A blocked
high-priority candidate does not starve independent lower-priority work.
Failed tasks cannot bypass native retry limits through pause/resume; the explicit
retry action is their recovery path when allowance remains.
Concurrent selectors use the native SQLite write fence. The entry, queued task,
audit, outbox and actor-scoped idempotency receipt commit together. Retrying after
acknowledgement loss returns the original entry without creating a second task.

Scans accept at most eight analyses/2,048 proposals, 2,048 admission records in the
requested baselines and 512 active entries. Admission rechecks active capacity
inside the write fence so competing selectors cannot exceed it. Overflow or missing linked tasks fails
closed; pages contain at most 100 entries. SQLite admission is supported; no new
migration or dependency is needed.

Admission creates no model execution, task lease, tool approval, workspace scope,
source edit or approval transition. Native reviewed-plan, runtime, checkpoint,
lease, RBAC and emergency-stop paths govern later execution. Fixture evidence
remains fixture evidence. Autonomous scheduling, Codex/computer delegation and
Git/worktree/PR orchestration remain future work.
