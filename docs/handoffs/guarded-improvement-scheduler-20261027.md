# Guarded improvement scheduler checkpoint

Branch: `codex/guarded-improvement-scheduler-20261027`; base `d8c90576f9fa04a56ff938b7deee005dbff32f72`. Independent feature worktree. No commits, pushes, PRs, merges or activation yet.

Implemented default-off bounded API lifecycle scheduling, immutable frozen runtime captures/results using existing improvement records, native cycle idempotency and existing authorized backlog admission. Maximum one active improvement task is enforced inside native admission. Added typed read-only status endpoint. No inference or code execution.

Verification as of this checkpoint:
- Initial six tests RED for missing contracts/service, then six PASS.
- Lost-admission-ack regression RED (incorrect blocked outcome), repaired; scheduler plus backlog 36 PASS.
- Extended scheduler gates/recovery/concurrency suite 28 PASS in 23.42 seconds before the later status/empty-window/settings tests.
- Affected package after status/empty-window/settings additions: 193 PASS in 74.39 seconds.
- Actual subprocess abrupt-exit/restart tests: 2 PASS in 10.76 seconds; native capture and admission recovery, retained snapshot despite changed source rows, one native task. No model call.
- Final expanded affected package: 197 PASS in 85.91 seconds, including enabled API lifecycle shutdown, in-flight cancellation, actual subprocess crash/restart and all existing backlog/evidence/analysis tests. Evidence: schedule-final.xml/log.
- Independent review: no confirmed Critical/Important findings. Permanent-host behavior and sustained evidence growth remain unverified.
- Fresh frontend typecheck, ESLint, 234 Vitest tests / 33 files and production build PASS in this worktree.
- Final shard manifest coverage and full collection PASS: 3870 cases. Ruff/diff checks PASS.
- Full backend baseline on the acceptance/runtime worktree remains running in session 72990; it began on an earlier tree and is retained baseline evidence only. Do not relabel it as final scheduler revision evidence.
- Frontend source is unchanged from base; retained acceptance-worktree typecheck/ESLint/234 Vitest/build evidence is separate from new backend runs.

Remaining: finish the full backend baseline, resolve actual failures, preserve final-head source/test provenance and publish authorized feature PRs with final-head CI/review. Do not mark full operational goal complete or merge.
