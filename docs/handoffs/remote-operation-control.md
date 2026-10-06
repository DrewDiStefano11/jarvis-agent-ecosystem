# Remote operation/control development checkpoint

October 6 sharded-CI reconciliation: main
`05d7628b15b7665927584a7349ac3c1af9cf247c` includes merged #82/#73. The merge
is conflict-free and preserves remote authority/cancellation and main's native
correction failure guards. Seven remote test files are assigned exactly once
across the four shards; full collection coverage passes for 1,727 cases. Blank
SQLite upgrade/downgrade/re-upgrade passes. Final focused native remote/TLS/
correction/coordinator/CI package passes 132 tests (125.52s).
An initial mixed-loop test stalled; narrowed faulthandler/timeout evidence isolated
the correction emergency-stop call made on pytest's loop while TestClient owns
the app lifecycle. Sending that operator action through the native HTTP route
preserves stop/no-extra-dispatch assertions and passes the original reproduction.
The 13-case narrowed package passes (22.19s). No production timeout or safety
guard was weakened. API/scripts Ruff and frontend typecheck/ESLint/104 tests/build
pass. Old monolithic backend runs are superseded. Require exact-head sharded CI
and independent local review if hosted quota remains exhausted. Do not merge
automatically under the current user instruction.

The continuous-development instruction supersedes rigid sequencing and artificial
milestone stops. Preserve this work and pursue useful independent tasks while
gates run. PR #63 belongs to a separate session and must not be touched.

## State

- Branch/worktree: `codex/remote-operation-control`, `.worktrees/remote-operation-control`.
- Reconciled base: main `9d5eab1c5e169f7c99f3cf1e9e2ecafa3a725fdf`, including merged #69.
- PR #70: https://github.com/DrewDiStefano11/jarvis-agent-ecosystem/pull/70.
  Exact candidate SHA and fresh review requests are recorded in PR comments.
- No unmerged #69/#63 dependency; single migration head `20260907_11`.
- Latest persistence repair full backend: 1,501 passed/two existing skips in
  1,049.25 seconds. Ruff API/scripts and frontend typecheck/ESLint/104 Vitest
  tests/build pass. Earlier checkpoints below are validation history.
- #69 merged after clean exact-head CI/review. The authorization conflict retains
  task-scoped `authorize_task` and the session-aware runtime authorizer protocol;
  documentation retains both remote-operation and verifier contracts.
- The latest P2 was reproduced on both remote/native stop paths: restored active
  status retained `Paused by emergency stop`. Resume now atomically persists
  `Resumed after emergency stop`; both reload/restart assertions pass. The affected
  controls/HTTP/persistence suite passes 76 tests. Combined full backend passes
  1,585 tests with two existing skips (841.04 seconds).
- #71 at `808bc03` has clean exact-head review and all four passed PR-event gates.
  It is being reconciled against merged #69. No runner-allocation code workaround.
- #73 frontend lazy-import failures were reproduced and repaired; node-verifier
  head `6d600a1` is pushed with full backend 1,555 passed/two existing skips,
  frontend104 and fresh gates. Reassignment/replanning remains unfinished.

## Implemented and exercised

Dedicated disabled-by-default HTTPS API and verified-TLS CLI submit/inspect/cancel
native goals, inspect planned graphs/results/audit/active identities/status,
request native runtime pause/resume/cancel, and perform native system stop/resume.
See [remote operation](../remote-operation.md) for configuration and limits.
No remote legacy administration, browser/WebSocket access, forwarded transport
trust, arbitrary completion command, or automatic autonomous admission.

Remote and native grants are checked per request and inside fenced writes.
Registry audit identity does not populate the simulated-agent foreign key.
Native outbox/sequence, lease, command replay, deny precedence and checkpoint
semantics are retained. Stop preserves worker rows despite stale API cache;
revocation rolls back native control state, and committed stop survives lost
acknowledgement without duplicate events. Resume does not forge execution.
The gateway is outermost, including CORS preflight. Deployment supports SQLite
single process only.

## Recorded validation

- Final reconciled main `ee0dd09bf1825ab635e7a61d8fe2ae192f6e0444`: full backend
  1,494 passed/two existing skips in 813.50 seconds; frontend typecheck/ESLint,
  104 Vitest tests/build pass. API/script Ruff pass. Single inherited migration
  head `20260907_11`. Remote authorization and native coordinator commit guards
  both acquire the write fence before reads; both remain intact.
- Access/goals/native leases: 23 passed.
- Remote runtime/native runtime/leases: 39 passed.
- Configuration/HTTP/actual TLS: 25 passed before final system additions.
- System controls/native API/persistence/leases: 84 passed.
- Latest HTTP/system/actual TLS plus CORS ordering regressions: 20 passed.
- Full remote backend: 1,414 passed, two existing skips in 757.34 seconds.
- Final source-read guard: HTTP/goal/TLS package 23 passed, then all three source
  correction cases (authorized, initial denial, commit-time denial) passed.
- Actual TLS CLI saved-command pause/replay/resume/cancellation passes; worker
  confirmation is rejected remotely and performed locally as an explicit fixture.
- Required frontend typecheck/ESLint/101 Vitest tests/build pass; Ruff and diff
  integrity pass. One migration head `20261002_si`; no new migration/dependency.
- Real TLS uses isolated ephemeral certificates/databases. Native runtime worker
  confirmation is deterministic; no public deployment/model reasoning claim.
- Windows shared pytest temporary state is inaccessible. Use a fresh validated
  short `$env:TEMP` directory via `--basetemp`; do not skip tests or change behavior.

## Next work

Fresh review of `22ac979` found agent stop/resume transitions were only in memory.
Both reproductions failed before repair: stop restored thinking after reload;
resume of native-stopped agents restored paused after restart. The current repair
reads native agent rows under the same write fence and updates control fields with
the flag/audit/outbox transaction. Memory adopts committed projections only after
commit. Existing leased task state is never flushed from API cache. A pending stop
checkpoint records current fenced agent/task projections rather than stale cache.
112 affected native HTTP/system/TLS/API/persistence/lease tests pass; the added
checkpoint projection case passes. Full backend passes 1,501 tests/two existing
skips at `.local/durable-controls-full-backend.log`; publish and request fresh gates.

Latest review repairs are implemented: both direct body-bound errors carry
no-store; desired-state resume equality is checked inside the simulator lock;
failed pre-commit resume restores paused agent snapshots while committed lost
acknowledgements preserve the resumed state. Concurrent stop/resume retries return
the same current status and commit one native audit/outbox event. Authorization
is rechecked inside the lock, including no-op controls, and at mutation commit.
Native non-remote resume retains its existing inactive-stop error.

All 110 HTTP/system/real-TLS/native API/persistence/lease tests pass (107.54 seconds).
Ruff API/scripts and frontend typecheck/ESLint/104 Vitest tests/build pass.
Reconciled full backend checkpoint remains 1,494 passed/two existing skips.
The previous hosted backend failed with runner communication loss and no steps;
its browser job never acquired a runner. Publish repairs and use fresh-head gates.
Current main remains ee0dd09; no new migration or dependency. Single head20260907_11.

1. PR [#70](https://github.com/DrewDiStefano11/jarvis-agent-ecosystem/pull/70)
   is open and attached. Fresh review of `41147cdb2826637d1108fe3e6557d347f4cfc364`
   found explicit HTTPS default-port normalization incompatible with the supported
   client. The follow-up normalizes configured and received authorities using
   HTTPX URL semantics, preserving single-host and nondefault-port checks.
   HTTP/configuration/actual TLS tests pass: 32 tests. Publish the fix and request
   exact-head CI/fresh review; record the final SHA in the PR and continue useful work.
2. Reconcile newly merged main while preserving changes; shared RBAC overlaps
   are expected if #69 merges. Return to #69 when actionable.
3. Adaptive correction needs real executable nodes
   for reassignment/result-preserving replanning. Do not touch #63 for that dependency.
4. Build on merged #68 evidence/diagnosis/planning toward durable task selection,
   delegation, Git lifecycle or unattended recovery; none is already executed by
   its advisory foundation.

## 2026-10-06 task-graph main conflict resolution

Reconciled main `7b761b1cf6a4aac6fd689b2699a6dfc9db6a4f46` after #80 merged.
Both sides fixed the emergency-stop test's cross-loop lock access. Preserve this
branch's real HTTP call via asyncio.to_thread, keeping the TestClient portal as
simulator/broker owner. All assertions remain. Retain main's shard redistribution
and all seven remote-control test registrations. No runtime behavior is dropped.
Ruff/format and complete 1,727-test collection pass. Focused backend: 54 passed.
Frontend typecheck, ESLint, 111 Vitest tests and production build pass. Publish
this reconciled head, record fresh independent review, and stop during hosted CI.

## 2026-10-06 reconciliation after backlog and Mission Control merges

Main `91085847c19a6b96de43231c2102353732961149` includes merged #71.
Preserve both remote actorIdentityId and backlog verifiedActorId attribution in
append-only audit persistence. Keep authorization on every broker write path,
with one callback inside the native repository write fence. Keep guarded receipt
completion from main and remote atomic emergency-stop/checkpoint persistence.
Frontend Mission Control shell, approval and runtime history behavior is inherited.
No authorization fence, audit record, native task or checkpoint behavior is dropped.
Fresh validation and independent exact-head review are recorded in PR comments.
Stop during hosted CI; do not merge automatically. #71 and #73 are already merged.

Validation for this reconciliation: 108 focused native remote-control, backlog,
API and CI tests passed, including rollback/revocation and both audit identity
paths. Complete 1,758-test collection, Ruff and formatting pass. Frontend typecheck,
ESLint, 144 Vitest tests and production build pass. No schema or migration change.
