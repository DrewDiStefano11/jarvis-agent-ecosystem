# Backend CI inactivity repair — October 7, 2026

- Branch: `codex/backend-ci-inactivity`; worktree: `C:/Users/DDistefano/.codex/worktrees/backend-ci-inactivity/jarvis-agent-ecosystem`.
- Base: PR #88 head `f8bee73da3d79db5f9f54b2cb3b7c08b514b7bc5`, now merged on main `1b02bca`.
- Root cause: #89 workflow `37529237604`, autonomy job `112496470237`, passed tests at 97% when the old 1,500-second accumulated-runtime watchdog killed it. No assertion failure appeared before termination.
- Repair: measure time since received output, preserving the existing 1,500/120-second inactivity windows, per-test 300-second guards, child identity checks/cleanup, serial execution, temporary databases and collection coverage. Remove the separate backend-shard 30-minute job budget; GitHub's default platform execution ceiling remains.
- Full isolated backend: 1,854 passed, two existing environment skips (1,002.33 seconds). All 35 focused CI tests pass, including healthy progress beyond its inactivity window, one-output-then-stall failure and surviving child cleanup.
- Ruff format/lint, frontend typecheck/ESLint/194 Vitest tests/build, 1,856-case exact collection coverage and blank migration roundtrip pass.
- Exact pushed-head CI and independent review remain required. No waiver, weakened assertion or merge attempt.
- Once reviewed, integrate this infrastructure-only repair into #89 so its required exact-head CI can finish. Native Git inspection #91 depends on #89; validation #90 is independent.
- The parent Self-Build campaign remains active: native workspace creation, source editing, contained validation commands, publication/review/repair, production dogfood and post-run improvement audit remain incomplete.

Hosted review found P2 `4206860546` on initial head `7e2ad954b8fd318a49f1976336662d3adc6e89d9`: final drained output was printed without updating its timestamp. A real fast-exit process with forced final-drain observation reproduced `last_output_elapsed=0`. Repair measures that output too. Final 36 focused CI cases, Ruff format/lint, 1,857-case collection coverage and blank migration roundtrip pass; the prior full backend remains evidence for its original head.


Companion review finding 4207141494 on integrated PR #89 identified unguarded
setup/upload steps. The identical repair is backported here: checkout/upload each
have five-minute budgets, setup ten and dependency installation fifteen. Pytest
retains output-inactivity/per-test supervision without a new accumulated-runtime
step deadline. The semantic workflow regression and entire CI helper package pass
37 tests. Prior clean review/CI belongs to e3311b59; this new head requires fresh
checks and review before a renewed merge-ready handoff. No merge attempted.
