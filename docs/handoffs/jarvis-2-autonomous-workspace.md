# Jarvis 2.0 campaign ledger

## Active checkpoint — November 6, 2026

- Goal: complete functional offline-verified autonomous workspace; full owner requirements are in attachment 57431584-eb40-4e2b-ad0b-72a7513fb7a1.
- Baseline: origin/main 6b38d4c61b4eae67ba4a0f3c9231cc4a8f26d31b, fetched and verified through GitHub.
- Branch: codex/jarvis-two-workspace.
- Managed worktree: C:/Users/DDistefano/.codex/worktrees/jarvis-two-workspace/jarvis-agent-ecosystem.
- Original feature/improvement-candidates checkout has pre-existing staged source/docs and validation files. Preserve its index and every earlier worktree.
- Architecture and feature inventory: docs/architecture/jarvis-2-autonomous-workspace.md.
- No implementation or validation pass claimed yet.
- Current milestone: architecture and durable draft intake / Home vertical slice.
- Tools verified: GitHub connector reads and git fetch work. gh CLI token is invalid. Browser Use and Context7 tools are listed; no need for external docs yet. Native managed worktree works. Local Playwright browsers/dependencies already exist.
- Environment: pnpm 11.28.5; default Node 24.17 (required target 22); installed Python 3.12.10 found by uv despite py launcher failure. Existing isolated cloud-readiness venv may be used read-only for dependencies, with explicit new-worktree source paths.
- Main handoff: docs/handoffs/software-readiness-20261105.md. Real acceptance handoff located read-only under cloud-readiness-20261106 worktree .local/cloud-task-readiness/EXECUTION-HANDOFF.md. It verifies worker/cloud/Groq profile disabled; task task-5259094efe remains under review. Do not mutate it.
- Restrictions: no inference, spending, external communications, broad permissions, production startup, merge or deployment. Disposable databases and fixtures only.
- PR: none yet. No push performed. Owner campaign instructions authorize publishing reviewed development PRs; merges remain prohibited.
- Next: create focused intake plan, failing transactional/replay/restart tests, additive migration and native-task-linked intake; implement Home via shared store; actual isolated API/browser checks. Continue later milestones from architecture acceptance list.

## Verified intake checkpoint

Implemented draft intake contracts and additive migration; one original message,
conversation and goal are atomically linked to one native task through the existing
task.created/outbox/idempotency transaction. Home uses shared store state and a
persistent session retry key. Existing dashboard moves to /mission-control;
legacy routes remain. Full final navigation and later campaign stages are pending.

Observed evidence (working tree, not yet committed):
- New backend tests: 9 pass (original text, duplicate/conflict, restart, bounds,
  local boundary, no context dispatch, atomic rollback, concurrent replay and
  populated-history downgrade refusal).
- API/context plus initial intake: 48 pass. Full backend collection: 4178 nodes.
- Blank migration upgrade/current/history/downgrade/re-upgrade: pass.
- Frontend full suite: 257 pass before two added state-edge tests; focused six
  state tests pass after fixes for session-storage denial and stale submit results.
- Typecheck and ESLint pass. Production build pass before the two small state fixes;
  rerun required before delivery. All four backend CI shards now running.
- Actual API/Playwright process acceptance: pass for save/task link, lost ack then
  reload/retry without duplicates, API restart recovery, six widths without overflow,
  and no uncaught page errors. Workers and inference disabled; no fixture inference
  is needed for draft intake. Evidence directory:
  C:/Users/DDistefano/.codex/visualizations/2026/11/06/01a1b1d4-40d7-7460-9d48-a0c80cc95b17/goal-intake.
- Browser routing: the Browser plugin/skill used by frontend-testing-debugging is
  not listed. Browser Use cloud tools are a different connector; use the already
  installed local Playwright for disposable loopback services, as explicitly allowed
  by owner requirements. No installation was needed.
- First browser harness used JARVIS_WEB_ORIGIN, which is not the native setting;
  exact-Origin middleware correctly refused it. Corrected harness to WEB_ORIGIN.
  No application boundary was weakened. Full-page screenshots are captured after
  resetting scroll; otherwise fixed navigation appeared in the wrong vertical spot.
- A review subagent is auditing this isolated diff under requesting-code-review.

Ruling: execute the supplied detailed design/sequence inline without another design
approval — owner expressly instructs continuous implementation and no repeat
permission for ordinary engineering. Cost if wrong: owner can review isolated PR
before any integration; no merge/deployment authority exists.

## Delivery checkpoint (offline draft intake)

- Independent review: resolved Unicode minimum-length/definite-422 input lock,
  overlap between history refresh and save, and unmeasured zero-dispatch claim.
  Reviewer checked fixes and reported no additional actionable findings.
- Final frontend: 262 tests / 37 files pass; production build, typecheck and ESLint
  pass. Nine focused shared-state tests cover lost acknowledgement/reload, explicit
  retry identity, changed request rejection, storage denial, stale selection,
  Unicode validation, definite precommit rejection and overlapping history reads.
- Backend full shards: runtime 437 passed; autonomy 510 passed; models 1378 passed,
  3 explicit environment skips. System shard remains IN PROGRESS; do not certify
  its pass or repeat it while active. Evidence .local/jarvis-2/backend-{shard} and
  corresponding output logs. Collection remains 4178 nodes.
- Ruff lint/format clean (593 Python files). Blank migration cycle passed; populated
  history downgrade refusal passed in focused tests. Diff whitespace clean.
- Actual process/browser smoke now reads eight native tables before/after and
  after 1000ms idle: model_executions, agent_runtime_runs, agent_runtime_attempts,
  tool_executions, context_assemblies, groq_acceptance_runs, groq_rate_states and
  free_ai_quotas all remain zero. Two intentional submissions create exactly two
  tasks; lost acknowledgement retry creates none. Console clean before injected
  network faults, no uncaught page errors. Screenshots inspected at 1440 and 320;
  all six width captures and overflow assertions pass.
- Node validation currently uses installed Node 24.17 rather than requested Node22;
  hosted CI remains configured for Node22. Python3.12.14 and pnpm11.28.5 verified.
- Draft PR can be prepared with pending system/hosted checks disclosed; no merge
  is allowed. This checkpoint supports durable draft intake only. Automatic bounded
  execution, follow-up conversations, final navigation migration, native reviews,
  archive/trash, Library/files and remaining campaign acceptance are unfinished.

Next native budget investigation: main.py context assembly currently invokes team
selection before assembly and decomposition on new/replayed assemblies whenever
local/cloud execution is permitted. Existing planning.ts hardcodes 3 attempts,
2 provider requests/1 repair, 8192 context and2048 output. GroqCapacityStore already
owns durable four-dimension accounting; extend admission across stages without a
parallel quota ledger. Design and test before enabling any new path. Keep drafts
inert until separately activated standing policy and complete admission checks.

Publication: source commit 11b7dd2, draft PR #163:
https://github.com/DrewDiStefano11/jarvis-agent-ecosystem/pull/163
Published without merge; full local system shard and hosted checks remain pending.
Continue budgets in a separate managed checkout based explicitly on this PR's
commit so this worktree/source remains unchanged for the running system tests.

Follow-up: legacy browser acceptance scripts still expected Overview at the old
root. Updated smoke-browser to wait for Home and smoke-mission-control to open
/mission-control. Actual API/separate-worker/Playwright legacy planning transport
passed (fixture inference only): submission/correction, lost acknowledgement and
reload/recovery, one result per task, Office camera/regions, desktop/mobile.
Backend application/test source remains identical to 11b7dd2 for running shards.
## Next active milestone

PR #163 at 11b7dd2 is the explicit dependency for codex/jarvis-stage-budgets,
managed worktree C:/Users/DDistefano/.codex/worktrees/jarvis-stage-budgets/jarvis-agent-ecosystem.
The original intake checkout is retained unchanged for its running system suite
and exact-source evidence. Stage-budget design/plan: docs/plans/jarvis-2-stage-budgets.md.
No stage-budget implementation or test pass claimed yet. One-request direct graph
is the next supported slice; general multi-stage aggregate reservation remains
unsupported until it can be enforced through native quota authority.

## Budget slice implementation / resume evidence

Current branch: codex/jarvis-stage-budgets. Explicit dependency PR #163; its latest
published follow-up is 3dcd1fe049e5f5bff00f5b5fb57217f18397857d. Budget branch started
at 11b7dd2 and must incorporate/rebase onto that follow-up before publication.

Implemented opt-in strict frozen stage/aggregate limits persisted with context;
normal new Planning submissions capture direct-v1 (one request/attempt, no hidden
team/decomposition/repair calls). Replay preserves the captured profile; true
legacy and workspace submissions remain compatible. Worker caps scope, routed
model, conservative complete wire body and reported input/output/total usage.
Configured native Groq/account/rate checks remain authoritative; no quota ledger
or activation bypass was introduced. Unsupported multi-stage graphs stay blocked.

Review found a P1: subscription/Codex transports omit physical completion caps and
can hide native inference stages. Reproduced with inert type fixtures, then fixed
by exact native adapter type/capability filter, routing allowlist and predispatch
recheck. Two MockTransport tests inspect actual Ollama num_predict/format and
compatible max_tokens/response_format. Rejected fixtures now advertise the same
CHAT/STRUCTURED_OUTPUT metadata to prove metadata cannot grant the guarantee.
Reviewer rechecked and found no residual blocker.

Observed tests before final commit:
- 14 new budget tests pass; 90 relevant backend worker/context/output/HTTP tests
  pass, including legacy behavior. Ruff clean after formatting; last frontend-only
  profile consistency guard does not change that backend evidence.
- Frontend full suite 264 passed/build passed before one additional captured-profile
  regression; focused12 profile/recovery tests, typecheck and ESLint pass after it.
  Rerun final complete frontend/build before commit.
- Actual API+separateworker+frontend command replay fixture pass: one physical
  planning fixture call per task and zero organizational model calls.
- Actual Playwright browser original/corrected tasks, lost acknowledgement/reload,
  source preservation, Office camera/regions and desktop/mobile pass. Actual API
  restart retains immutable one-request envelopes and completed result links; no
  additional fixture calls. Output .local-stage-browser.log; screenshots at
  C:/Users/DDistefano/.codex/visualizations/2026/11/06/01a1b1d4-40d7-7460-9d48-a0c80cc95b17/direct-planning.
- Initial failed browser timeout was missing pinned Office assets in fresh checkout;
  ran office:assets. Later503 was the deterministic fixture's default chat-only
  metadata; explicitly declared its tested structured-output capability in the
  disposable harness. No live model/provider setting changed.
- First transport attempt failed closed at requestCount0 because duplicated schema
  instructions exceeded the cap. Direct structured mode now sends its schema once
  through the actual adapter contract; full conservative preflight precedes context
  commit, with exact model recheck before dispatch. No real inference occurred.

PR163 hosted run42264800321 at exact3dcd1fe: frontend (CI Node22), runtime-browser,
backend-static/migrations, repository integrity, backend runtime/autonomy/models
and Linux Git job succeeded. Hosted system remains in progress. Original local
system shard remains active at .local/jarvis-2/backend-system in jarvis-two-workspace;
latest observed progress22%, not a pass. Do not duplicate unchanged expensive runs.

Remaining after budget slice: automatic standing admission, conversational follow-up,
native result review/Inbox (including paused five-recommendation fixture), full
navigation, Work projects/lifecycle/retention, Library/files/sources, Settings and
remaining A-M end-to-end acceptance. No full-campaign completion claimed.
