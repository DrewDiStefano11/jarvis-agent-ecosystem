# Self-Build validation-planning checkpoint — October 6, 2026

- Branch: `codex/self-build-validation`.
- Worktree: `C:/Users/DDistefano/.codex/worktrees/self-build-validation/jarvis-agent-ecosystem`.
- Exact main base: `7abb488d1b73dcfd4c990519ef1578c2906430db`; remote main was
  checked before starting. No existing planner/command journal was found on main.
- Independent of workspace-reservation branch `codex/self-build-workspaces`; no
  sibling migration and no imports from its unmerged contracts.
- Implemented E1: bounded exact-code-state/gate/evidence contracts, deterministic
  focused/publication policy, browser/migration derivation, relevant component
  fingerprints, policy source digest, mandatory-gate rederivation, protected-path
  blocking and conservative failure/staleness assessment.
- Full isolated backend: 1,896 passed, two existing environment skips, one inherited
  occupied-port doctor failure in 901.45 seconds. PR #88's exact independent repair
  is included as a prerequisite; hosted Codex reviewed it clean at f8bee73.
- Final changed planner/doctor package: 47 passed (46 planner cases plus the repaired
  doctor scenario). Final delta tightens unknown text-file classification and
  normalizes policy source line endings. This is composite validation, not an
  uninterrupted green full run.
- Ruff format/lint, frontend typecheck/ESLint/194 Vitest tests/build, CI collection
  coverage and blank migration roundtrip pass. Exact final-head CI and independent
  review remain pending. No merge-ready claim or waiver.
- This is deterministic planning/assessment, not command execution or durable
  validation evidence. A native trusted Git observer and command journal must
  supply verified complete snapshots and measured records. Do not feed model text
  into these trusted boundaries or call a plan an executed validation.
- Next: integrate with reviewed bounded development command families, persist
  observations through native runtime/audit/outbox/checkpoints, and prove staleness
  and cancellation/restart behavior in the actual development workflow.
- The parent campaign remains continuous. Keep both isolated worktrees intact;
  no auto-merge. Preserve primary checkout and all other sessions' branches.


Independent review of PR #90 head `03456e79809d7e5309ff8b48899aef0a086005a2`
found P1 derivation-input forgery and P2 missing SQLite3 backup/sidecar protection.
Repair persists bounded derivation inputs in the immutable plan, requires the
original independently trusted inputs at assessment, and compares the entire
rederived plan. Candidate inputs cannot become authority merely through a hash.
SQLite/SQLite3 and generic WAL/SHM/journal artifacts now block publication.
All 54 final planner regressions pass, including self-rehashed unrelated-test and
removed-browser attacks. Changed-head full publication checks and review pending.


Final repair publication gates: uninterrupted full isolated backend 1,906 passed,
two existing environment skips (964.43 seconds). Ruff format/check, frontend
typecheck/ESLint/194 Vitest tests/build, blank migration roundtrip and 1,908-case
shard coverage pass. This supersedes the initial composite local baseline for the
planner repair. Exact changed-head CI and hosted re-review remain required.
