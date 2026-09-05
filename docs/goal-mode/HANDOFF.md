# Next Codex handoff — active implementation checkpoint

## 1. Mission and authority

Turn this existing Jarvis Agent Ecosystem into a reliable, usable local AI Hub:
real local-model planning, explicitly authorized useful tools, durable workforce
execution and recovery, an interactive office reflecting actual state, and practical
operator workflows. Passing CI alone is not completion. Continue this repository;
do not create a replacement application or another orchestration engine.

The final-reset user directive prioritizes preserving and integrating current work,
then real autonomous tools, actual local-model end-to-end acceptance, workforce,
functional office, corrections, and a thin Business Lab. Prefer functional vertical
slices over repeated infrastructure hardening. While CI/review runs, do independent
implementation; do not consume the session polling unchanged external state.

The user explicitly authorizes commits, pushes, PR updates, review requests and
normal gated merges. Historical prohibitions on pushing/merging and Phase 1 tools
in earlier handoffs are superseded within this mission. No force push, branch
protection/admin bypass, deletion of valid work, or merging known defects is allowed.
Before merge require relevant local validation, green CI on the exact head, fresh
applicable Codex review, no remaining actionable findings, correct target/lineage,
and preservation of unrelated work. Merge qualifying PRs and continue; the final
implementation must eventually reach main and be launched from that integrated state.

## 2. Exact repository snapshot

Repository: `DrewDiStefano11/jarvis-agent-ecosystem`.
Snapshot fetched **2026-09-05 17:15 UTC**, before writing this handoff:

| Reference | Exact observed remote SHA / relationship |
|---|---|
| main | `96e32d2b204b04069b11fd91d0b3eef8e699221e` |
| continuation | `astra/ultra-ai-hub-completion` at `76921ea696d98d6e30f3a19d955f5ed8e2f581d3` |
| #57, draft | continuation → main; integration vehicle, not yet merge-ready |
| #56, ready | `astra/goal-complete-ai-hub` at `c428216aedccd1f09258addbf105ed73d2158b3e` → `arena/01a025b0-jarvis-agent-ecosystem` |
| #55, draft | `arena/01a025b0-jarvis-agent-ecosystem` at `a7124c66fe302aee339efe7ea34e7a7d4bbc0a48` → main |
| #53, draft | `codex/windows-runtime-supervisor` at `d4ebe8476d3936e43ac0ba31f5943550205ce41d` → main |
| #54, merged | merged to main as `96e32d2b204b04069b11fd91d0b3eef8e699221e` |

The handoff-bearing commit is necessarily a descendant of this observed implementation
checkpoint. Fetch before acting and obtain the current head with `git rev-parse HEAD`
and `gh pr view 57 --json headRefOid`; do not assume a document's older snapshot is
still the live head. The final PR description records the handoff commit externally.

Recommended integration path: finish #56 review/CI and merge it into #55's branch;
then validate/review/merge #55 into main with its recovery fixes included; reconcile
#53's supervisor against that main and integrate it through normal gates; finally
validate/review/merge #57's remaining office/workforce/tools/product changes. #57
already contains the supervisor and planning ancestors. **Do not independently merge
duplicate snapshots or cherry-pick the same implementations again.** Use ancestry and
tree differences, not titles alone, to decide whether a PR has become redundant.

Never check out `astra/goal-complete-ai-hub` in the primary worktree. Its original
worktree is protected and still checked out at `6e73df61837e5a7016863d5499875877c49a791b`.
The continuation branch was created at exactly that SHA and its initial HEAD verified.
The #54 → #55 → #56 lineage has been preserved without deleting that worktree.

## 3. What actually works

| Subsystem | Evidence and limits |
|---|---|
| Control plane | Durable identities/RBAC, runtime ledger, attempts, leases, outbox/audit, checkpoints and recovery are implemented and extensively validated. Trusted local loopback boundary, not internet authentication. |
| Planning | UI creates context and explicitly queues bounded planning through an actual API and separate worker. Stable command IDs survive lost acknowledgements and reload. Deterministic review/revisions and preserved results work. |
| Actual model | Installed Ollama `qwen3.5:0.8b` produced an accepted plan through the real submission function/API/worker. One request, 524 input/443 output tokens, about32.2s CPU latency; exact result/task survived API restart. This established planning, not tools. |
| Providers | Local-only adapters, explicit structured output and disabled reasoning for the supported Ollama path; canonical Pydantic bounds remain enforced. No cloud fallback. Earlier small-model failures were correctly paused/reported. |
| Supervisor | Windows startup/status/doctor, owned child restart, stop/restart, backup integrity and port release validated on #53's source. Integrated into continuation in `fc26ac6`; combined backend suite passed. Final integrated launch remains required. |
| Office renderer | Original immutable8K prototype floor, camera, pan/zoom, candidate inspection and original sprite sheets.15 measured landmarks: RMS0.618px, max1.183px. |
| Office movement | Durable identity placement, assignment/move/stop/continue/release, motion versions, occupation and aisle reservations, real runtime activity, emergency/inactive freeze. Six approved stations and six directed routes inside Models/FocusC. Full-floor/cross-room navigation remains unfinished. |
| Workforce | Durable registration/profile/activate/suspend/reactivate/enable controls, effective-capability filter, shared planning targets, stable-key lost-ack recovery. Browser and restart verified. Demo agents explicitly distinguished. Not a general multi-agent decomposer. |
| Tools | Backend work is being integrated on the tool checkpoint branch. Real list/read/report and artifact persistence passed isolated fixture end-to-end plus interruption/revocation tests. At this snapshot it is not yet in continuation; do not claim the visible UI executes tools until integration/acceptance is complete. |
| Business Lab | Thin durable objective list using existing tasks' `projectId=business-lab`, shared planner/workforce/history links. Tool authorization and artifact UI implemented; complete integration acceptance pending. |
| Human correction | Creates linked new queued tasks preserving prior input/result/review/project. Corrected task creation acknowledgement/reload retry and actual worker resubmission are browser-tested. No second workflow engine. |
| Demonstration | Seeded simulator, demo agents, some dashboard metrics and demo artifacts remain simulated and labeled. They do not establish autonomous capability. |

## 4. Implementation carried forward and completed during this reset

- `8562e0a`: targeted lifecycle bookkeeping preserves separate-worker state; new
  autonomous CREATE/QUEUE checks durable task eligibility; terminal form retries blocked.
- `6254f1f`: integrated corrections, task creation idempotency and planning reload
  recovery from source `d44121d`. Important files: `services/task_creation.py`,
  `state/planningRecovery.ts`, `state/taskCreation.ts`, `TaskCreateForm.tsx`, Runtime/Tasks.
- `c428216`: integrated real installed-model output compatibility from `56981c9`.
  See `models/agent_runtime.py`, `autonomous_worker/service.py`, provider contracts/adapters,
  `docs/goal-mode/local-model-evidence.md`.
- `149cb6f`: integrated original live office source `e1da20e`: `app/office`,
  migration07, canonical frontend office state, original sprites and geometry evidence.
- `a0883b0`: integrated workforce source `62faac5`. See `IdentityWorkforce.tsx`,
  `api/identities.ts`, `state/useRuntimeState.ts`, `docs/WORKFORCE.md`.
- `fc26ac6`: merged supervisor `d4ebe84`, plus corrected browser identity selection,
  scenario-specific inference counts and canonical placed-identity office totals.
- `7b2110d`: merged UI checkpoint `3ff3292` and follow-up `ab6c1e6`; Business Lab,
  optional project preservation, workspace proposal mode, exact-content/scope review,
  canonical tool progress/artifact history, stable authorization ID across reloads.
  A reviewed acknowledgement is bound to the exact current scope and plan hash.
- `0ff43f4`: integrated statically checked acceptance harness source `a2e4565`.
  `scripts/smoke-workspace-tools.py/.cjs` distinguish fixture and actual Ollama modes.

## 5. Incomplete work — resume here, do not hide it

### Completed since last handoff:

- `5129b7c` and `6535b66`: integrated durable tool execution backend with workspace authorization.
- `c00588e` and `76921ea`: formatting fixes and unicode console fixes for smoke testing.


1. ~Integrate the tool backend checkpoint.~ DONE: It was integrated, and smoke tests have passed.
   `codex/ultra-tool-execution`, based on provider569 plus integrated149. It owns
   `models/tool_execution.py`, `app/tool_execution/{filesystem,repository,service,router}.py`,
   migration`20260905_08`, worker dispatch/result support, and focused tests. Source
   and recovery tests have passed; aggregate impacted tests are still running. It
   must be committed/pushed before this session ends even if final validation fails.
2. ~Run the workspace acceptance harness against the combined backend/frontend~ DONE: Smoke script `smoke-workspace-tools.py` successfully completed., then
   the actual installed Ollama model. Its checkpoint has static checks only. Record
   actual failures; never label fixture output as real inference.
3. Tools execute a fixed reviewed plan of at most8 steps. Reads return observations;
   later steps are not synthesized from new observations. Code execution, Git/GitHub,
   browser research, unrestricted shell/cloud access and adaptive delegation are absent.
4. Cross-room office navigation is not approved. Existing geometry diagnostics found
   endpoint connectors dropping allowed-door IDs, D01 threshold/room attribution,
   disconnected D42 strokes, RM10 incorrectly matching RM1, and raw-point route limits.
   Repair canonical geometry and validate real door/collider clearance; never waive all
   doors/colliders just to make a screenshot. A bounded cross-room follow-up is active
   in `codex/ultra-hub-integration`; publish any useful unfinished patch remotely.
5. Workforce profiles/capabilities/target assignment work; autonomous selection of an
   appropriate multi-agent team and adaptive task decomposition are not implemented.
6. Documentation such as older `CODEX-HANDOFF.md`, continuation/local-setup boundaries
   predates these integrations. This file and latest source take precedence. Update
   operator documentation when the tool integration acceptance is complete.
7. PR integration is unfinished. Main still lacks #55/#56/#53/#57 product work at the
   recorded snapshot. Do not call the Hub finished or use main as proof of these features.

Known concrete review findings: #56's terminal pending-form issue is fixed and its
thread resolved at c428; fresh c428 Codex review still required. #55's two recovery
threads have fixes in #56 but are not yet resolved on #55 because the fixes have not
reached that branch. #53's four actual defects are fixed/resolved at d4; fresh final-head
review is still needed. Tool read-only review found queued emergency-stop handling,
inactive-target transitions, final completion fencing and candidate starvation; fixes
and five real-database regression cases passed before aggregate validation.

## 6. Highest-value next tasks and acceptance

1. **Complete tool integration and real model-to-artifact acceptance.** Depends on
   tool08 + UI7b2110d. Read tool contracts/service/repository/worker, run
   `test_tool_execution.py`, `test_tool_execution_recovery.py`, `test_tool_filesystem.py`,
   then `smoke-workspace-tools.py`. Accept only UI-created objective → actual model →
   reviewed fixed plan → explicit scope → real tools → visible durable report, with
   source preserved, permission/emergency fences and restart recovery intact.
2. **Finish a verified cross-room office connection.** Use `scripts/vendor/office-navigation.ts`,
   `build-office-network.cjs`, `app/office/catalog.json` and the pinned original prototype.
   Acceptance requires measured registration, preserved restricted geometry, collision
   checks and real browser movement/stop/reload/emergency behavior. Current six-station
   behavior must remain intact. Tests: `test_office.py`, office-live and smoke-office.
3. **Make bounded workforce assignment practical.** Reuse identity capability assignments,
   runtime targets and canonical office state. Add explicit selection/decomposition only
   where it can use existing authorization and durable runtime primitives. No pretend
   agent capabilities or separate domain state in pages.
4. **Finish gated PR integration and launch main.** Reconcile the stack above, validate
   the exact final branch, fresh Codex reviews, merge coherent milestones, then test
   actual startup, model/tool browser path, office, restart and emergency stop on main.

## 7. Worktree and remote transfer inventory

Use `git worktree list` before touching a checkout. Never delete a worktree to obtain
its checked-out branch. Paths below are relative to the primary repository except
the protected earlier worktree. Unique source commits that were cherry-picked are
already preserved in the continuation's equivalent implementation.

| Worktree / branch | Transfer state at snapshot |
|---|---|
| primary / `astra/ultra-ai-hub-completion` | pushed through0ff43f4, integrated planning/correction/provider/office/workforce/supervisor/UI/harness |
| `.local/business-lab` / `codex/ultra-business-lab` | clean, pushedab6c1e6; merged into7b2110d; exact-scope acknowledgement fix is in continuation |
| `.local/identity-workforce` / `codex/ultra-identity-workforce` | cleana2e4565, pushed/verified; workforce and acceptance harness integrated |
| `.local/hub-integration` / `codex/ultra-hub-integration` | e1da20e source integrated as149; new cross-room work in progress, must checkpoint remotely |
| `.local/local-model-planning` / `codex/ultra-local-model-planning` | clean56981c9; equivalent implementation integrated as c428 |
| `.local/planning-recovery` / `codex/ultra-planning-recovery` | cleand44121d; equivalent implementation integrated as6254f1f |
| `.local/supervisor-hardening` / `codex/ultra-supervisor-hardening` | cleand4ebe84; pushed as original#53 branch, merged into fc26 |
| `.local/tool-execution` / `codex/ultra-tool-execution` | unique useful implementation and pending merge; remote transfer mandatory before final handoff |
| protected earlier worktree / `astra/goal-complete-ai-hub` | local6e73df6 deliberately untouched; remote#56 advanced toc428 |

Ignored QA screenshots/logs/databases are evidence, not application dependencies.
Repeatable committed scripts and source/validation summaries preserve the useful
knowledge; no database, sidecar, build, dependency tree, environment file or secret
belongs in a checkpoint. Final transfer status must supersede this active snapshot.

## 8. Validation ledger

- Combined tool execution backend integrated: Ruff format and lint passed; **1042 backend tests passed** (including tool execution). Ruff format
 135files and lint passed; **976 backend tests passed**,524.99s, Python3.12.10.
- That frontend: typecheck, ESLint,88tests and production build passed; subsequent
  office count fix passed5focused tests/typecheck/lint.
- UI integration: Ruff136files/lint;24project/correction tests passed; frontend
  typecheck/lint/**94tests**/build passed. Exact-scope follow-up:5focused tests,
  typecheck/lint passed. A final combined suite remains required after tool08.
- Installed-model source checkpoint:764backend/49frontend gates passed; actual
  Ollama planning and restart evidence described above. Correction source had
 780backend/65frontend plus actual browser recovery/correction acceptance.
- Office source:795backend/56frontend,40focused office tests; blank/packaged catalog
  validation and measured registration reproduced. Root regular and office browser
  scenarios both passed after the two integration harness fixes.
- Supervisor source:868backend/57frontend gates; actual Windows child crash/restart,
  backup integrity, normal stop/restart and release of owned ports passed.
- Tool filesystem:51passed,1 Windows symlink-privilege skip; real junction and hardlink
  containment did run. Core tool7tests plus recovery5cases passed; aggregate pending.
- Migration head before tools is`20260905_07`; tool08 descends07. CI#56c428 blank
  upgrade/downgrade-to04/re-upgrade passed. Final tool08 combined migration round trip
  and downgrade guard must be recorded after integration.

Run validation from `apps/api` (venv may be shared across worktrees):

```powershell
.\.venv\Scripts\ruff.exe format . --check
.\.venv\Scripts\ruff.exe check .
Remove-Item Env:JARVIS_DATABASE_URL -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe -m pytest -q --basetemp (Join-Path $env:TEMP ('jarvis-check-' + [guid]::NewGuid().ToString('N')))
```

Then from repository root:

```powershell
pnpm --dir apps/web typecheck
pnpm --dir apps/web lint
pnpm --dir apps/web test
pnpm --dir apps/web build
$env:JARVIS_SMOKE_BROWSER='true'
& .\apps\api\.venv\Scripts\python.exe scripts/smoke-local-planning.py
$env:JARVIS_SMOKE_OFFICE='true'
& .\apps\api\.venv\Scripts\python.exe scripts/smoke-local-planning.py
Remove-Item Env:JARVIS_SMOKE_BROWSER, Env:JARVIS_SMOKE_OFFICE -ErrorAction SilentlyContinue
```

An earlier long local test process overlapped adding migration07:820passed and3health
tests failed from mixed schema discovery. All50lease/office tests passed in a clean
process; the later976test integrated suite passed. Do not mutate migrations while a
test process is running. Another test invocation set a global database URL that
overrode isolated migrations and failed collection; removing that process env override
resolved it. Pytest handles its own temporary application; standalone app imports must
set isolated DB/data env before importing `app.main`.

## 9. CI and Codex review snapshot

- #56 c428: run`33981104161` frontend, runtime-browser, integrity and blank migration
  checks passed; backend pytest still running at17:55UTC. Superseded run33981101905
  was cancelled by the workflow's concurrency policy, not treated as a code failure.
  Latest completed Codex review was b541, **not c428**. Its one P2 was fixed; thread
  `PRRT_kwDOTeMOVc6fk-s1` resolved, reply3941455017. Request fresh review after CI.
- #53 d4: run33978196030 all4gates succeeded. Four original actionable threads fixed
  and resolved; no fresh final-head Codex review yet. Integrate only after stack review.
- #55 a712: unresolved recovery threads`PRRT_kwDOTeMOVc6bszTi` and
  `PRRT_kwDOTeMOVc6bszTq`; fixes await merge from#56. Do not dismiss them prematurely.
- #57:149cb6's browser check failed on the stale identity label; root fixed that and
  the office scenario count, both actual browser cases passed. The latest0ff43f4
  checks are new/in progress; no fresh exact-head Codex review. Remains draft.
- #54: exact reviewed c4f70b4 tree had green CI/clean Codex/no threads; merged96e32d2
  has the same tree. No other PR was merged during this active snapshot.

Refresh immediately before final transfer/merge using `gh pr checks`, `gh pr view`
and review-thread GraphQL. Never present an older review as proof for a new head.

## 10. Run the system now

Prerequisites: Windows, Python3.12, Node22, pnpm11, Git; an installed local Ollama
model for real inference. Use continuation for the implemented product; main is older
until the stack is merged. Do not overwrite an existing `.env` or runtime database.

```powershell
# Repository root, fresh install
python -m venv apps/api/.venv
& .\apps\api\.venv\Scripts\python.exe -m pip install -e './apps/api[dev]'
Push-Location apps/api
& .\.venv\Scripts\python.exe -m alembic upgrade head
Pop-Location
pnpm --dir apps/web install --frozen-lockfile
pnpm --dir apps/web build
.\scripts\jarvis.ps1 doctor
.\scripts\jarvis.ps1 start
.\scripts\jarvis.ps1 status
```

Open`http://127.0.0.1:5173`. Create a task (or Business Lab objective), open Planning,
select it, and click **Prepare local planner for this task**. Copy the returned actor
ID. Preparation grants the existing runtime permissions for that task only; selecting
an identity or enabling the worker does not grant arbitrary tools.

Configure the same existing DB and settings for API and worker, through an untracked
`apps/api/.env` or the supervisor's process environment. Stop before changing config:

```powershell
.\scripts\jarvis.ps1 stop
$env:JARVIS_AUTONOMOUS_WORKER_ENABLED='true'
$env:JARVIS_AUTONOMOUS_WORKER_ACTOR_ID='ACTOR_ID_RETURNED_BY_PREPARE'
$env:JARVIS_AUTONOMOUS_WORKER_INSTANCE_ID='local-worker-1'
$env:JARVIS_MODEL_EXECUTION_MODE='local_only'
$env:JARVIS_MODEL_OLLAMA_ENABLED='true'
$env:JARVIS_MODEL_OLLAMA_BASE_URL='http://127.0.0.1:11434'
$env:JARVIS_MODEL_OLLAMA_MODEL='qwen3.5:0.8b'
$env:JARVIS_MODEL_OLLAMA_TIMEOUT_SECONDS='180'
$env:JARVIS_MODEL_PROVIDER_PRIORITY='ollama'
$env:JARVIS_MODEL_ALLOW_REMOTE='false'
.\scripts\jarvis.ps1 start
```

Use the endpoint where your installed Ollama actually listens. This session verified
an isolated existing service at`127.0.0.1:11535`; another machine must not assume it.
The supervisor does not start/install Ollama. `ollama list` shows installed models;
choose an existing supported model. A new download is an environment prerequisite,
not evidence of code completion.

Select the configured worker actor and an active target, prepare each new task,
then explicitly queue its plan. Inspect Runtime history and persisted model results.
Revise eligible paused/failed/completed requests through **Revise task input**; prior
history stays intact. Emergency stop is in the shared topbar. Resume does not approve
new work. Office requires explicit identity placement and movement on approved routes.

After tool08 integration, follow `docs/WORKSPACE_ACCEPTANCE.md` and the tool guide for
marked-workspace configuration. It uses`JARVIS_TOOL_EXECUTION_ENABLED=true` and
`JARVIS_TOOL_WORKSPACES_JSON` mapping aliases to dedicated marked directories. Marker
schema is`{schemaVersion:'1.0',workspaceId:ALIAS,allowedTools:[...],readPrefixes:['inputs'],writePrefixes:['reports']}`.
Use actual JSON double quotes. Never mark a filesystem root or broad home directory.
Choose **Workspace actions and report**, wait for completed review, inspect exact
contents/scope, then **Authorize workspace execution**. Results appear in **Workspace
execution history**, including when viewing the linked execution task.

Manual fallback uses three terminals with identical DB/provider/actor configuration:
API from`apps/api`: `.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000`;
web from root:`pnpm --dir apps/web dev --host 127.0.0.1`;
worker from`apps/api`: `.\.venv\Scripts\python.exe -m app.autonomous_worker`.
Do not run manual components on the supervisor's occupied ports. Stop foreground
processes with Ctrl+C; use `jarvis.ps1 stop/restart/status/backup` for owned services.

## 11. Environment and operational notes

- No Browser plugin skill was available; Playwright Chromium was used per frontend
  testing skill. Browser scripts own isolated child processes and temporary databases.
- Pytest's default historical temporary root had access problems on this machine.
  Use a new GUID basetemp as above; do not delete/change permissions on old caches.
- One earlier standalone QA script imported the app before isolating the database,
  unintentionally migrating ignored`apps/api/data/jarvis.db` from03to06. Integrity and
  foreign keys were checked; four demo tasks/five demo agents remained, runtime tables
  were empty, lifecycle timestamps unchanged. No rollback/deletion was attempted;
  no verified pre-import backup existed. See continuation.md for full disclosure.
- The original prototype reference checkout contains unrelated staged user edits.
  Do not modify/reset it. Read immutable Git source at
  `7c5cd21cdce503f2e9ac94700c95b7faad8e1bfd`; asset hashes are pinned in scripts.
- Generated build/QA cleanup previously encountered automatic review restrictions;
  excluded evidence was retained/moved within its owned workspace. It is not source.
- GitHub CLI was authenticated here. A new account needs repository permission and
  working Git authentication; local-model tools require no cloud model credentials.

## 12. Definition of done for the next Codex

Launch the integrated main through the supported local supervisor. A user creates a
meaningful objective; an actual local model proposes bounded work; appropriate active
identities receive it under explicit operator authority; real permitted tools execute;
progress and reports become durable and visible; office state reflects actual work;
corrections preserve history; restart loses no authoritative state; emergency stop
prevents further advancement. Distinguish all remaining simulation, fixed-plan limits,
unavailable integrations and unapproved office geometry. Require clean final working
tree, exact final SHA, green exact-head CI, applicable clean fresh Codex review, no
unresolved real defects, and a final launch/acceptance report. If incomplete at the next
limit, checkpoint every useful patch remotely and update this file before ending.
