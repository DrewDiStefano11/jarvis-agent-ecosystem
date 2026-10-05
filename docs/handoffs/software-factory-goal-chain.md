# Software Factory goal chain

## Current milestone

PR 1 — independent result verification and critique.

- Repository: `DrewDiStefano11/jarvis-agent-ecosystem`.
- Branch: `codex/independent-result-verification`.
- Isolated worktree: `.worktrees/independent-result-verification`.
- Exact base: `6626c5c4e267b737d86f8883201450e946aa1831`.
- Candidate head: the feature branch HEAD; exact pushed SHA will be recorded in the PR gate comments. Source implementation is based on the exact base above.
- PR URL: https://github.com/DrewDiStefano11/jarvis-agent-ecosystem/pull/69.
- Merged milestones: none. PRs 2–4 have not started.
- Concurrent #68 and #63: both open at latest GitHub inspection; untouched.
- Migration head: exactly `20260906_10`; no migration added. Blank upgrade, populated downgrade to `20260729_04`, and re-upgrade passed.

## Implementation and validation

Opted-in queued planning runs freeze completion criteria and gate completion on
independent verification. Deterministic result checks and bounded semantic
reviewer requests reuse the existing worker, model router, RBAC, leases and
runtime checkpoint/audit/outbox machinery. A read-only authorized API exposes the
durable structured verdict. Nonpassing results pause for operator review.
Uncertain dispatch acknowledgements never cause a repeated reviewer call.

- Focused verifier tests: 29 passed, including real workspace artifacts, live
  loopback HTTP transport with deterministic inference, malformed reviewer repair,
  invented evidence rejection, restart recovery, cancellation, emergency stop,
  lease loss, and target revocation.
- Earlier verifier/structural review/runtime contract regression checkpoint:
  64 passed before the additional acceptance scenarios.
- Ruff format and lint: pass, including root scripts with exact successful-main CI Ruff 0.16.10; no dependency manifest changed.
- Frontend: typecheck, ESLint, 101 tests, build pass; no frontend source changes.
- Review-fix full backend pytest: 1,324 passed, two existing skips in 632.60 seconds. The browser-fix full run is recorded in the PR body after it completes.
- Original head `9ec92e2f4e48ee9dc19ead97a7b0f39d329f4669`: all four jobs passed in Actions run `37309997386`. Review-fix head requires new checks.
- Codex reviewed original head `9ec92e2f4e48ee9dc19ead97a7b0f39d329f4669`, finding one P2: artifact criteria cannot be fulfilled in the planning lifecycle. The fix rejects them at specification validation, with Python/JSON regression tests and corrected capability documentation. Fresh final-head review remains required.
- Review threads and merge gates: pending.

## Capability evidence boundaries

Proven on the production runtime path in isolated acceptance fixtures: immutable
criteria, deterministic checks, durable verdict persistence/read RBAC, nonpass
completion gating, restart recovery, and authorization/stop/lease fences. The
production Ollama HTTP adapter and router have been exercised with a local HTTP
fixture. Semantic judgment responses are deterministic fixtures/mocks, not real
model inference. The host's loopback Ollama endpoint refused connections; no
Ollama CLI was found. No models were downloaded or provisioned.

Test/build execution evidence, adaptive correction/reassignment/replanning,
software command/worktree execution and autonomous PR delivery are not implemented
by this milestone. Planning artifact criteria are rejected until post-tool verification
is integrated; the internal artifact checker test is component coverage only.
`test_evidence` is deliberately unverifiable until the command
journal exists. Existing worker structural revisions retain their prior behavior.

## Next actions

1. Finish complete local checks for the review fix; commit and push only this milestone's files.
2. Record exact final head and validation evidence in the PR body and ignored gate state.
3. Require all exact-head Actions jobs and a fresh explicit final-SHA Codex review.
   Resolve the addressed artifact thread; allow at most five review/fix cycles.
4. Do not merge without zero actionable findings/unresolved threads, current-main
   reconciliation, passing required gates and a clean worktree. Do not begin PR 2
   before PR 1 has merged. No CI/review polling loops while waiting.
5. Record merge evidence, then start the next milestone from refreshed exact main.

The four infrastructure milestone PRs have explicit goal-scoped push/merge
authority once all gates pass. Do not merge #68/#63 or delete feature branches.
Software Factory-created software PRs must never self-merge under this policy.

## October 5 pipeline update

The updated goal explicitly permits independent/dependent preparation while CI
runs, overriding the earlier strictly sequential start rule. Merge gates remain.

- Runtime-browser attempt 2 of run `37319318730`, job `111894309964`, failed in
  the live-office task-to-planning transition: task creation returned before the
  frontend refresh completed, and `selectOption` targeted a detached selector.
  The smoke now follows the completed task-created planning link and asserts
  shared runtime task selection. Both actual local browser commands passed,
  including live office worker completion and emergency stop; inference is fixture
  data. Application behavior and assertions were not weakened.
- Independent next work: `codex/remote-operation-control` in
  `.worktrees/remote-operation-control`, based on exact main `6626c5c4e267b737d86f8883201450e946aa1831`,
  with no #69 dependency. Authentication/RBAC core and nine isolated-database tests
  pass locally; HTTP/WebSocket/UI integration, TLS boundary, mutation audit and
  revocation/transport acceptance still need implementation. No PR exists yet.
- Adaptive preparation: `codex/adaptive-correction-replanning` in its own worktree,
  dependent on #69 head `7d3777ff08f4a50faa363b23416b39eebc823424`.
  `docs/handoffs/adaptive-correction-preparation.md` maps required evidence and
  current-main gaps. Existing graphs are planned work, not executable nodes;
  full reassignment and verified-result replan reuse must not be claimed from
  retry-only/component tests. Do not import #63's unmerged execution contracts.
- #68/#63 remain separate, untouched work. Check GitHub before each PR boundary.
- After each validated #69 push, request explicit final-SHA review and let CI run
  asynchronously. Work on the independent remote milestone, then return at a
  meaningful checkpoint. No repeated CI waiting/status loops.
