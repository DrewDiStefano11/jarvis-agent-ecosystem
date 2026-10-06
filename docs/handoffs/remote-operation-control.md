# Remote operation/control development checkpoint

The continuous-development instruction supersedes rigid sequencing and artificial
milestone stops. Preserve this work and pursue useful independent tasks while
gates run. PR #63 belongs to a separate session and must not be touched.

## State

- Branch/worktree: `codex/remote-operation-control`, `.worktrees/remote-operation-control`.
- Reconciled base: main `ee0dd09bf1825ab635e7a61d8fe2ae192f6e0444`, including #68/coordinator.
- PR #70: https://github.com/DrewDiStefano11/jarvis-agent-ecosystem/pull/70.
  Exact candidate SHA and fresh review requests are recorded in PR comments.
- No unmerged #69/#63 dependency; single migration head `20260907_11`.
- Latest persistence repair full backend: 1,501 passed/two existing skips in
  1,049.25 seconds. Ruff API/scripts and frontend typecheck/ESLint/104 Vitest
  tests/build pass. Earlier checkpoints below are validation history.
- #69's cancellation/blank-deliverable repairs are pushed at `47ce8d4` with clean
  exact-head review. Backend is running; browser/frontend/integrity passed.
- #71 at `808bc03` has clean exact-head review and three passed gates; hosted
  backend runner-allocation failure was rerun. Do not add code workarounds.

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
