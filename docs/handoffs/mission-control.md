# Mission Control campaign handoff

## Scope and ownership

Mission Control is a frontend campaign over merged backend contracts. Work begins
from `origin/main` at `9d5eab1c5e169f7c99f3cf1e9e2ecafa3a725fdf` (PR #69).
The primary checkout remains on another session's branch and is untouched.

- Shell/overview branch: `codex/mission-control-shell`.
- Worktree: `C:/Users/DDistefano/Documents/jarvis-agent-ecosystem/.worktrees/mission-control-shell`.
- PR/head: publication follows this implementation commit. The branch ref records its exact head; the next milestone handoff records the published PR and review SHA.
- Merge/push policy: feature branches only; campaign explicitly authorizes publishing
  coherent PRs and requesting exact-head Codex review. Never merge into main here.

## Audit, 2026-10-06

Open PRs at intake: #70 remote control (`5289ffee`), #71 improvement backlog
(`cd4fcb1`), #72 CI evidence (`a2fb5ab`), #73 adaptive correction (`6d600a1`).
Only frontend overlaps are #70's one contract type change and #73's small
`apps/web/tests/app.test.tsx` adjustment. No unmerged code or contracts are used.
Existing worktrees also cover research retrieval/transport, runtime, verification,
and self-improvement. No Coordinator project marker exists; no board is created.

`AppStore` owns the snapshot, ordered WebSocket cursor, reconnect/HTTP fallback,
and all domain hooks. Runtime runs/results are identity-authorized and optionally
task-filtered. The existing projection fetches at most 50 runs and exposes a next
page offset. No actor means visibility is unavailable, not an empty global queue.
Tasks have no universal execution provenance field. Seed tasks, simulator work,
and operator-created tasks share their contract. The overview must not classify
all durable tasks as real autonomous goals or invent completion analytics.

System status contains real database/schema, worker/lease, outbox, emergency stop,
and autonomous-worker/provider facts. Its resource values are hardcoded simulation
fixtures. Approvals are existing simulator decisions, distinct from workspace plan
hash authorization. Office projects durable identities and actual movement/runtime
activity; it has its own shared AppStore hook and no separate scheduling authority.

Existing reusable components include task/agent drawers with focus restoration,
TaskCreateForm with durable creation acknowledgement recovery, runtime submission
recovery, workforce lifecycle controls, PlannedWork and CoordinatedWork, Status,
Progress, and Office. Product/roadmap documentation has older phase statements;
merged model/router contracts and current workforce/runtime/Office docs prevail.

## First milestone

- Charcoal/purple Mission Control tokens and shell, restrained icon navigation.
- Collapsible desktop sidebar; automatic laptop rail; five-item phone navigation
  with More exposing the other five destinations, including Office and System.
- Overview: system attention, authorized runtime view, compact health rows,
  bounded durable task requests opening the shared drawer, recent activity.
- Simulated resources/seeded agents are in a collapsed demonstration disclosure.
- Unknown health and identity scope are explicit. Disconnected/error/gap state
  retains useful records with a dated stale banner and HTTP refresh.
- New task navigates to the existing idempotent creation form; no inference queues.
- Shell stop/resume uses existing commands with confirmation and acknowledgement
  error handling. Other page-specific controls retain existing behavior.

## Validation and evidence

Baseline: 104 Vitest tests, frontend typecheck/lint, backend Ruff passed.
After changes: 112 Vitest tests, typecheck, ESLint and production build passed.
Backend pytest initially hit the existing shared temporary-directory permissions
problem. A fresh task-specific `--basetemp` rerun is underway; no backend source was
changed. Record its final result before committing.

Browser plugin/skill is absent, so the existing Playwright Chromium approach is
used with real API processes, Alembic-managed isolated temporary SQLite databases,
dynamic loopback ports, inference disabled, and process cleanup.

`node scripts/smoke-mission-control.cjs` passed actual task creation, shared task
drawer/Escape/focus restoration, sidebar collapse, phone More/System/Office,
approval navigation, declined and accepted emergency stop/resume, retained data
while offline, automatic reconnect, and no execution command from the overview.
Horizontal overflow checks passed at 1440x1000, 1920x1080, 1024x768, 390x844,
and 320x740. Browser console/page and HTTP checks passed except expected offline
transport errors. Evidence: `C:/Users/DDISTE~1/AppData/Local/Temp/jarvis-mission-control-EbMlzX`.
Baseline captures: `C:/Users/DDISTE~1/AppData/Local/Temp/jarvis-workforce-fxICeN`.
Generated design reference:
`C:/Users/DDistefano/.codex/generated_images/01a11181-6f92-75e3-87b8-2a6413df8ab0/exec-4121f447-faad-46fd-981f-245f6291677e.png`.

The reference and implementation were inspected with view_image. Comparison:
charcoal palette/purple accents; persistent sidebar/topbar; attention-first hierarchy;
two-column runtime/health and requests/activity layout; sans-serif typography;
restrained borders/icons; responsive rail and mobile rows. A CSS import-order defect
and primary-link text contrast were corrected during visual QA. Intentional
reference deviations: actual API task labels/statuses/dates replace sample values;
pending approval link and count expose real records; task rows omit unavailable
provenance metrics; health rows have no decorative chevrons that imply row actions.
The full reference image has a slightly different native ratio; 1440x1000 was the
implementation comparison viewport. Browser screenshot evidence covers all five
acceptance sizes. The new layout is verified against this reference with truthful
merged-main data substitutions, not hardcoded design mock data.

## Remaining campaign work

1. Publish the validated shell/overview; request exact-head
   review and check external gates at the next meaningful checkpoint.
2. Improve native agent operations while preserving existing lifecycle/capability
   controls and shared identities. This can proceed independently from merged main.
3. Build a focused native goal/task workspace from existing decomposition and
   coordinator records; maintain accessible graph/list alternatives.
4. Approval and system areas still contain older demo-first copy and controls;
   separate real workspace plan authorization and runtime evidence clearly.
5. Talk to Jarvis should reuse task creation/recovery rather than pretend natural
   language can perform unsupported actions. Command/search can use current data.
6. History/analytics require evidence windows and provenance; avoid seed totals.

No backend authority, migrations, new socket, scheduler, or alternate domain store
is introduced by the first milestone. Read-only run visibility remains limited to
the authorized fetched page. Full model reasoning/tool execution acceptance is
outside this shell change and is not claimed.

Existing planning/worker browser golden path also passed: real API plus separate worker, fixture inference, lost-acknowledgement reload/recovery, corrections, original-result preservation and Office navigation. Evidence: C:/Users/DDistefano/.codex/visualizations/2026/10/06/01a11181-6f92-75e3-87b8-2a6413df8ab0/planning-acceptance. Fixture inference is transport/recovery evidence, not real reasoning evidence.
