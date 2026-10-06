# CI evidence checkpoint

October 6 sharded-CI reconciliation: main
`05d7628b15b7665927584a7349ac3c1af9cf247c` includes merged #82 and #73.
The merge is conflict-free; added CI-adapter tests are explicitly assigned to the
models shard. Complete collection coverage passes for 1,686 cases, and blank
SQLite upgrade/downgrade/re-upgrade passes. Focused CI/self-improvement/correction
recovery passes 107 tests (45.90s). API/scripts Ruff and frontend typecheck/ESLint/
104 tests/build pass. Old monolithic backend runs are superseded. Require fresh
exact-head sharded CI and review; current user instruction prohibits automatic
merging. Root CLI documentation repairs and evidence bounds remain intact.

PR #72 is reconciled with merged #69/main
`9d5eab1c5e169f7c99f3cf1e9e2ecafa3a725fdf`. Combined evidence/verifier/worker/
migration integration passes 210 tests (124.49 seconds), plus all 19 CI-adapter
regressions (2.26 seconds), with Ruff clean. Frontend typecheck/ESLint/104 tests
and production build pass. The
original `c3ba136` exact-head review was clean and frontend/browser/integrity passed.
Its backend job exceeded the one-hour limit while continuing through roughly 93%
of the suite; logs show progress without an assertion failure. No timeout or retry
budget has been raised. Fresh exact-head CI/review follow reconciliation; the
earlier full backend checkpoint of 1,460 passed/two existing skips remains history.

Independent branch `codex/ci-evidence` starts at freshly inspected main
`ee0dd09bf1825ab635e7a61d8fe2ae192f6e0444`. Other sessions' branches are untouched.
PR #69's review repairs are pushed at `47ce8d4`; remote PR #70's next review repairs
are in progress. Durable improvement backlog PR #71 is pushed at `808bc03` with a
clean exact-head review. Its backend job did not acquire a runner and was rerun.

## Implemented vertical slice

An operator-exported bounded GitHub Actions run can enter native Improvement Lab
analysis through existing CLI `--evidence ci_run:alias:path`. Native DTOs discard
step/command/URL text. Recorded successful, failed, timed-out and startup-failed
jobs become numeric observations. Cancelled/skipped/neutral/action-required/stale
or pending jobs remain unmeasured; absence of jobs cannot claim success. CI heads
must match the requested baseline SHA. Job identity/clock/status and scan bounds
are validated. No external integration or GitHub mutation runs inside Jarvis.

CI outcomes lack frozen historical test suite/policy/configuration definitions, so
provenance remains incomplete and proposals remain evidence gathering. Native
deterministic proposals explicitly request cause/suite evidence rather than guessing
startup fixes or claiming a regression. The evaluator digest covers both the adapter
and its native input contract. No execution approval, migration or dependency.

## Validation and next action

October 6 review repairs: the CI example uses the repository-root wrapper
`python scripts/jarvis_self_improve.py`; the canonical artifact inventory includes
`ci_run`. The wrapper's `analyze --help` succeeds from the repository root.
All 19 adapter regressions, API/scripts Ruff and frontend typecheck/ESLint/104
tests/build pass. These repairs change documentation only; prior backend
integration evidence remains applicable. Request fresh exact-head review and CI.

- Full backend: 1,460 passed, two existing skips. Windows runner wall-clock timing
  includes a long session interruption; it is not a runtime performance measure.
- Ninety focused CI/native self-improvement tests pass, including real temporary
  SQLite migrations and CLI persistence. An actual bounded GH export validates.
- API/scripts Ruff, frontend typecheck/ESLint/104 Vitest tests/build and diff
  integrity pass. Single migration head remains `20260907_11`.
- Publish a coherent commit and PR, then record exact-head CI/review in PR comments.
  No root cause, regression attribution or software repair is inferred from job
  status alone. Continue priority review repairs while external gates run.

Preserve the original adaptive preparation; merged
coordinator now exists, but independent node verification/correction/replanning
still needs a complete real execution vertical slice.

## 2026-10-06 reconciliation after backlog and Mission Control merges

Main `91085847c19a6b96de43231c2102353732961149` includes merged #71.
Resolve the API-contract documentation conflict by retaining both bounded CI
provenance/import contracts and native backlog selection/read contracts. Keep
main's backlog models-shard registration and this PR's CI-evidence registration;
complete test collection verifies every test file is assigned exactly once.
Preserve imported Mission Control shell, approvals and runtime-history behavior.
Fresh validation and independent exact-head review are recorded in PR comments.
Stop during hosted CI; do not merge automatically. #71 and #73 are already merged.

Validation for this reconciliation: 152 focused backend tests passed; complete
1,717-test collection, Ruff and formatting pass. Frontend typecheck, ESLint,
144 Vitest tests and production build pass. No schema or migration change.
