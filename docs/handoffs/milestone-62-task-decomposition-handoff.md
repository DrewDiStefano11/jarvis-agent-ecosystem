# Milestone #62 Task Decomposition Handoff

## Repository
DrewDiStefano11/jarvis-agent-ecosystem

## Branch
codex/task-decomposition-assignment

## Base
- Base branch: main
- Original base SHA: d9198b8c3cd73e65ea9d2d6b8571ad35f962e797
- Latest pushed implementation SHA: 5de2b168957b50991708c0bb900c322b2f333469

## PR
Not opened yet.

## Goal
Milestone #62: Automatic Task Decomposition + Assignment.

#61 determines WHO works.
#62 determines WHAT each selected specialist does.
#63 will later RUN, MONITOR, RETRY, REPLAN, SYNTHESIZE, and FINALIZE.

Do not implement #63 as part of this milestone.

## Completed
- Added durable versioned decomposition graph.
- Planned subtasks are separate from executable simulator tasks.
- Decomposition references team-selection state.
- Maximum 12 subtasks.
- Maximum dependency depth 6.
- Deterministic ordering/topological behavior.
- DAG validation.
- Capability-aware specialist ownership.
- Assignments restricted to #61 selected team.
- Missing capability produces needs_team_reselection rather than silent team expansion.
- Team selection/context transaction handling hardened to avoid partial planning state.
- Normal planning/context integration added.
- Planned-work frontend UI added.
- Backend persistence/repository/service/router/model work added.
- Alembic migration added.
- Runtime/browser acceptance scripts added.
- 26 new decomposition tests reportedly passed before usage exhaustion.
- Context restart/replay/concurrency/rollback checks reportedly passed.
- Frontend TypeScript checks reportedly passed.
- Frontend suite reportedly reached 101 passing tests.
- Real process/browser acceptance reportedly passed.
- Restart/browser reload reportedly preserved decomposition.
- Blocked security-capability scenario reportedly produced needs_team_reselection.

## Important Architecture Decisions
- Existing parent/child simulator tasks remain executable runtime tasks.
- #62 uses a narrow planned-work graph so decomposition cannot accidentally start execution.
- Jarvis remains authoritative control plane.
- Assignment grants no permissions, roles, ranks, workspace grants, tool grants, or system authority.
- Decomposer may assign only specialists already selected by #61.
- No automatic catalog activation or silent team expansion.
- #63 coordinator/execution/synthesis remains explicitly deferred.

## Migration
apps/api/migrations/versions/20260906_10_task_decomposition.py

## Important New Files
- apps/api/app/decomposition/
- apps/api/app/models/decomposition.py
- apps/api/tests/test_task_decomposition.py
- apps/web/src/components/PlannedWork.tsx
- apps/web/src/state/useDecompositionState.ts
- apps/web/src/types/decomposition.ts
- apps/web/tests/decomposition.test.tsx
- scripts/decomposition-fixture.py
- scripts/smoke-decomposition.cjs

## Validation State
Confirmed by the prior Codex session before usage exhaustion:
- 26 decomposition tests passed
- context integration tests passed
- restart persistence passed
- replay passed
- concurrent submission passed
- rollback checks passed
- runtime/browser acceptance passed
- frontend typecheck passed
- frontend tests reached 101 passing

IMPORTANT:
The full backend suite had started, but its final result was not explicitly reported before Codex usage expired.

Do not claim full backend validation is green until rerun or verified.

Exact-head GitHub Actions have not yet been verified for this branch.

## Windows/Test Notes
Pytest's default temp directory caused Windows locking/permission issues.
Use isolated temp/database paths.
Do not fix locking with arbitrary sleeps.
Ensure SQLAlchemy sessions/connections are closed cleanly.

## Known Important Fix
Team selection was being committed before context validation completed.
That update was moved into the context transaction so rejected context should not leave partial planning state.
Retain regression coverage.

## Next Actions
1. Fetch and inspect this branch.
2. Review git diff origin/main...HEAD.
3. Run focused decomposition tests.
4. Run full backend validation.
5. Run frontend validation.
6. Run migration validation.
7. Run runtime/browser acceptance.
8. Fix any P0/P1 findings only.
9. Rebase/update onto latest main only if main has advanced and it is safe.
10. Open PR #62 targeting main.
11. Verify exact-head GitHub Actions.
12. Do not merge automatically.

## Security Requirements
- zero permission grants
- zero role grants
- zero rank changes
- zero workspace grants
- zero tool grants
- zero system-agent elevation
- no dormant agent activation
- no silent team expansion

## Milestone Boundary
Do not implement #63.
