# Durable validation journal

This milestone stacks on the trusted validation evidence PR. It is an internal
service and has no registered HTTP/model route, collector key or process runner.
A trusted source-state reader and receipt verifier must be supplied by the host;
the service does not accept a model-selected reader or an assertion of test success.
Production collector/state-observation wiring remains disabled.

Journal intent and transitions are append-only records in existing audit storage.
The task-lease transaction helper adds the corresponding outbox event in the same
transaction. The journal adds no database, lease table, scheduler, recovery cursor,
migration, runtime route or orchestration subsystem. Histories are reconstructed
from a bounded sequence and a hash chain; the database and native authority layer
remain trusted storage, not a cryptographic defense against a database administrator.

Exact approval binds the immutable source state and packaged execution policy.
Preparation and every transition check configured operator access, resource RBAC,
emergency stop, active runtime attempt/executor and the original workspace lease.
The private lease token is never journaled. Only its fingerprint is retained.
A candidate plan or model response cannot approve itself.

Every journal entry requires a native runtime checkpoint with its exact hash,
attempt, plan identity and monotonic sequence before the next step can advance.
Intent is committed before checkpoint publication. A crash between these commits
is recovered by replaying acknowledgement against the original persisted entry;
the existing native idempotency and version fences control checkpoint commits.
No command is executed by this service.

A step progresses from durable intent to started to a signed measured disposition.
Reconciliation changes unfinished steps to uncertain. It never retries the command
or fabricates a successful exit. Finalization requires every ordered gate to have
a measured or uncertain disposition, and does not itself mean validation passed.
Only authenticated exact-state evidence assessment can grant passing status.
Journal history remains readable after lease loss or runtime termination, while
new writes still require live original authority. An operator may renew the exact plan for the same live owner; the original approval
reference stays immutable and subsequent audit/outbox entries record the current
grant. Source, plan, lease or attempt changes still require explicit new authority.

Local fixtures exercise native runtime and SQLite ownership with temporary
databases, checkpoint crash gaps, replay conflicts, uncertain reconciliation,
lease loss, emergency stop, exact-plan approval and audit/outbox atomicity.
No production workspace or untrusted source is executed. Hosted independent
review is unavailable; author-performed local review must be documented after
these tests and repository gates run.


## Local validation and review

Journal/ownership regressions: 33 passed, including checkpoint crash gaps, atomic
rollback, forged receipts, stale source state, blocked-scope approval refusal,
corrupt native attempts, tampered history and final-checkpoint assessment.
Backend Ruff and frontend typecheck, ESLint, Vitest (214 tests) and build passed.
The full dependency baseline passed 2,350 tests with 2 skips; focused regressions
cover this delta. Exact final-head CI remains required after publication.
Author-performed local review checked persisted authority at every transition,
original lease/attempt binding, append-only history, journal sequence ordering,
checkpoint integrity, no invented successful recovery and no process launch.
This is not hosted independent review.

Exact-plan renewals are bounded to one hour, matching the measured backend-suite
policy. Renewal never adopts another worker, lease, attempt, source state or plan.
The original intent/checkpoint remains unchanged; expired grants cannot be reused.

Final follow-up validation: 34 focused regressions passed with no-cache Ruff.

Historical replay checks recorded plan integrity without granting current policy
authority. Old plans remain inspectable after upgrades; current approval and
passing assessment continue to reconstruct and require current packaged policy.
