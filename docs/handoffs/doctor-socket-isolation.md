# Doctor socket-isolation checkpoint — October 6, 2026

- Branch: `codex/doctor-test-isolation`.
- Worktree: `C:/Users/DDistefano/.codex/worktrees/doctor-test-isolation/jarvis-agent-ecosystem`.
- Base: `7abb488d1b73dcfd4c990519ef1578c2906430db`.
- One inherited deterministic doctor test observed the real frontend socket despite
  fixture HTTP. With an unrelated listener on 5173, the overall status was blocked
  instead of the asserted degraded status. Reproduced on workspace and unchanged
  application branches; this is test isolation, not a Self-Build product regression.
- Repair: reuse `no_tcp(monkeypatch, connected=False)` in that scenario. Existing
  supervisor/overall assertions remain intact; production socket logic is unchanged.
- Ruff format/lint and the repaired test pass. Shared-base frontend typecheck,
  ESLint, 194 Vitest tests and build passed in both Self-Build worktrees; no frontend
  files differ in this branch. Full baseline/regression runs are still active.
- Publishing for exact-head CI and independent review; never auto-merge or waive
  failures. The two feature PRs may depend on this tiny prerequisite if their CI
  encounters the same occupied-port environment. Keep their product changes isolated.
