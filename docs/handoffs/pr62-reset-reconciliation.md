# PR 62 reset reconciliation

Starting PR head: `208fbec6d539960428cb7f599a9b669201955652`.
Reconciled main: `f7ccb266af51d4abb3dc92a008bf551e2d5f412f`.

Both reset regressions pass individually on that unchanged main commit. Both
fail on PR 62 after bringing main into the feature branch, before the repair.
The earlier PR description's claim that these failures were pre-existing is
incorrect.

## Durable reset boundary

The reset route checks completed idempotency records before calling the
simulator. The simulator stages a reset audit at the next old-session sequence.
The repository then creates a new UUID session with sequence zero and commits
the reset state, audit, and command result in one unit of work. Commit failures
roll back and reload the repository; startup hydrates the committed system row.

PR 62 added a database cursor refresh immediately before reset's system-state
merge. That refresh copied the old committed session and sequence over the new
staged cursor. The transaction succeeded and stored its idempotency result,
but persisted no session transition. Status faithfully exposed that old value.

The repair removes only that refresh from reset. Ordinary persistence and event
writes retain their cursor refreshes. There is no status-endpoint workaround,
new migration, change to response envelopes, or change to sequence semantics.
The reset audit remains in the old session; the first subsequent domain event
uses sequence one in the new session. Runtime stream ordering remains separate.

## Regression coverage

- The original monotonic audit/outbox test remains unchanged and verifies
  durable cursor recovery after restart.
- The lost-response test now also recreates the application and replays the
  same key, checking the durable session and sequence, identical response, and
  exactly one reset audit.
- The decomposition replay/restart test additionally verifies that no
  executable child task rows exist after restart.

## Validation

- Unchanged main: both focused reset tests pass individually.
- Repaired branch: both focused reset tests pass individually.
- Persistence: 42 passed; decomposition: 28 passed; team selection: 4 passed.
- Additional context, idempotency, and control-plane subset: 51 passed.
- Migration: one head, `20260906_10`, based on `20260906_09`; blank upgrade,
  current/history, CI downgrade to `20260729_04`, and re-upgrade pass. A populated
  current-main database upgrades with its existing task preserved.
- Frontend: typecheck, ESLint, 101 tests, and production build pass.
- Runtime: process/command replay, browser submission/recovery, live office,
  and decomposition browser acceptance pass with deterministic local fixtures.
- Repository integrity and backend/script Ruff checks pass.

The first full local run encountered a sandbox filesystem restriction in the
runtime-doctor workspace check. That test passes outside the sandbox with a
fresh temporary directory. The complete rerun was still in progress when the
operator requested an immediate push and chose to follow up on CI. Full-suite
completion and exact-head Actions success are therefore not claimed here.

GitHub PR metadata and checks identify the final pushed head and Actions runs.
Specialist execution/coordinator work remains deferred to milestone 63.
