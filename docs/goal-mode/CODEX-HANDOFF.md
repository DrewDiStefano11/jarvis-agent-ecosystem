# Goal Mode transfer — 2026-09-05

## Read this first

The user explicitly requested a transfer/progress report. This is a handoff, **not a completion declaration**. Continue the original Goal Mode mission in this repository. Do not create another Hub, merge PRs, force-push shared review branches, drop commits, remove safeguards/tests, fabricate activity, or claim mock inference is a real model. User authorization covers normal engineering decisions, pushing the Goal Mode branch, and requesting fresh Codex reviews after exact-head CI is green (maximum five complete review loops). Zero review loops have been requested so far.

The latest checkpoint adds task-scoped planner preparation through the UI and visual fixes. Local validation passed, but these follow-up changes still need CI and browser verification. This document is included with that checkpoint; obtain its exact SHA with `git rev-parse HEAD` after fetching the branch. Its parent is `a964d028d3cbc507577e22d2afcae312e692401e`.

## Repository and history

- Repository: `DrewDiStefano11/jarvis-agent-ecosystem`.
- Work branch: `astra/goal-complete-ai-hub`.
- Draft PR: https://github.com/DrewDiStefano11/jarvis-agent-ecosystem/pull/56
- Local path in this session: `/workspace/scratch/878af0f74f1b/jarvis-agent-ecosystem`.
- Initial clean main: `784ec98b26d1d2070161acb5f498af998d6ea02a` (PR #52 replay/checkpoint fixes merged).
- PR #54, `codex/runtime-control-plane-hardening`: `c4f70b432bd5b51e65c7b963837368e008722175`, based on main.
- PR #55, `arena/01a025b0-jarvis-agent-ecosystem`: `a7124c66fe302aee339efe7ea34e7a7d4bbc0a48`, based on #54; deterministic planning review/revision cycles.
- **Exact Goal Mode starting SHA:** `a7124c66fe302aee339efe7ea34e7a7d4bbc0a48`.
- First published Goal Mode commit: `a964d028d3cbc507577e22d2afcae312e692401e`, tree `4ae2edc3c49067da4b44553b16b90f92fc7e2fc1`.
- PR #56 base is the #55 branch, preserving **main → #54 → #55 → #56**. Reinspect current remote state before rebasing or making overlapping changes; these observations can become stale.
- Independent draft PR #53, `codex/windows-runtime-supervisor`: `317866b575a0848982f9ccead74dfc28d8cdb6e9`, based on pre-#52 `534acdb...`. Inspected, not incorporated. About 24 commits/32 files/5k lines; Windows supervisor, worker main, startup scripts, Vite metadata. Preserve this work. Review concerns include SIGBREAK, detached console, shutdown status cache, and longest-first secret redaction. Do not replace it with another supervisor.

No PR has been merged or closed. No existing regression coverage was removed.

## Intended product and actual architecture

The goal is one integrated AI operations environment with durable autonomous execution and an interactive office reflecting real workforce state. The repository currently supports a narrower, local planning product:

- FastAPI + SQLAlchemy + SQLite; Alembic six revisions, head `20260729_05`.
- Durable demo/simulation task and agent service, audit and outbox events.
- Separate identity/RBAC and runtime ledger, fenced leases, worker registration, persisted commands, checkpoints, replay.
- Context assembly with provenance, hashes, redaction and explicit budgets.
- A separate, disabled-by-default autonomous worker. Local loopback Ollama and OpenAI-compatible adapters. The supported job is bounded `planning_review`.
- Typed durable model outputs and deterministic review. Accepted output completes the task; bounded revision or escalation follows failures. PR #55's review/revision behavior must be preserved.
- React 19/Vite 8/TypeScript 5.9 frontend. `AppStore.tsx` owns shared API/WebSocket state; runtime state is integrated through `useRuntimeState.ts`. No separate office polling/event system was added.
- The API is trusted-loopback only. Actor headers select local identities and permissions; they are **not internet authentication**. Do not expose this service through a public proxy.

General tool execution, unrestricted shell, cloud inference, general coding/research autonomy, Business Lab, and production office movement are not implemented/enabled. Do not present them as working. Existing architectural restrictions in `AGENTS.md` are relevant; read that file before new engineering.

## Work already published in a964d028

### Control plane and worker recovery

`apps/api/app/autonomous_worker/repository.py` fixes recovery pagination: historical execution rows no longer hide a new revision attempt after unblock/begin. Active stages/current-attempt rows still protect ownership.

`service.py` reconstructs an authorized revision recovery plan when a claimed revision has no resume checkpoint. Failure recovery preserves `review_revision_exhausted` from the integrity-checked durable review decision.

`tests/test_planning_review_orchestration.py` adds deterministic regressions for crashes after unblock/begin and after failure/task commit. Existing races, leases, command identities, and replay safeguards remain intact.

### Usable local planning flow

- `autonomous_worker/setup.py` originally provided an explicit repeatable CLI granting only task-scoped runtime permissions to a local identity. It refuses inactive identities, preserves denies, and does not enable models or tools.
- `apps/web/src/api/planning.ts` submits operator-approved task context, creates a run and queues it with stable command/context IDs. Repeating a partially acknowledged submission replays the same operations. Context requiring review is not queued.
- `types/runtime.ts`, `state/useRuntimeState.ts`, and `AppStore.tsx` integrate identity selection, authorized runtime history, outputs and stale-response protection.
- `pages/Runtime.tsx` exposes Planning, actual configuration readiness, explicit queueing, review/failure state and full persisted output. Readiness is not claimed as proven inference.
- Task details link to Planning and expose authoritative cancellation. Task creation and emergency controls now surface errors rather than silently failing.
- The selected submission is not persisted across page reload. The UI/docs instruct users to inspect history before submitting again after a reload. Clearing the form does not cancel backend work.

### Office integration

Reused original prototype artwork and DOM/SVG camera/overlay implementation. Imported renderer/support code is in `apps/web/src/office-reference`. The page uses Hub state, displays real worker health/identities without inventing desks, and explicitly separates simulation entities from actual workers. Random agents and development fixtures were not mounted.

The office supports pan/zoom/fit, candidate overlay inspection, selecting/focusing candidate geometry, layer controls, and mobile resizing. Candidate registration remains unverified; no production navigation or live sprite placement is claimed.

`scripts/office-assets.json` pins the original immutable floor and its digest. `scripts/sync-office-assets.py` downloads/checks it atomically. The 50 MB generated/cache file is ignored by Git. `pnpm office:assets` and the prebuild hook verify it. No duplicate 50 MB Git blob was added.

Original coordinate/gesture tests plus candidate-boundary tests were imported. Playwright 1.62.1 is the only new direct development dependency. The lockfile preserves baseline package versions; do not accidentally upgrade all the existing `latest` dependencies while installing.

### Runtime validation and setup

`scripts/smoke-local-planning.py` launches an isolated database, actual API subprocess, separate worker subprocess, and a real HTTP deterministic provider fixture. It uses the actual frontend submission function, checks replay creates one provider request, deterministic review, completion, and durable result references. This is real process/HTTP integration with **fixture inference**, not real model execution.

`scripts/smoke-browser.cjs` exercises Chromium against that running stack and captures screenshots. CI adds a Linux runtime-browser job without removing Windows backend/frontend/integrity jobs.

README and `docs/goal-mode/{mission,local-setup,office-source}.md` document setup, architecture, evidence and limitations.

## Follow-up checkpoint being handed off

These changes were uncommitted at the user's transfer request and are being preserved with this report:

1. Extract setup behavior to `autonomous_worker/provisioning.py`; CLI imports the same helper. After idempotent grants, explicitly check effective permissions and return `LOCAL_PLANNING_PERMISSION_DENIED` (403) if a deny still applies. A failed setup may leave allow assignments, but existing denies remain authoritative; do not claim the multi-service operation is one atomic transaction.
2. Add typed `POST /api/local-planning/setup` in `setup_router.py`, registered in `main.py`. Request contains only `taskId`; response includes actor ID and whether it matches configured worker identity. Execution is never enabled by setup. Existing trusted-loopback boundary applies.
3. Planning button **Prepare local planner for this task** calls that endpoint, selects its identity and target, and explains initial worker configuration if required. It does not queue inference automatically.
4. Add setup tests for repeatability, task isolation, configured identity reuse, suspended identities, missing tasks, extra-field rejection, nonloopback rejection, and explicit deny preservation. A second TestClient uses a fresh app instance to avoid reusing an event broker across event loops.
5. Browser smoke now uses the preparation button for its created task instead of the per-task CLI. Initial test bootstrap still uses the CLI to establish configured identity.
6. Visual fixes based on actual CI screenshots: dark control styling, readable links, danger cancel styling, mobile viewport sizing, screenshot scroll reset. Identity lifecycle is shown as enabled/disabled rather than stale operational-status `offline`; actual worker health remains separate.
7. CI adds per-branch concurrency and explicit 30-minute Windows/20-minute Linux timeouts. No validation gate was removed.
8. Update local setup, mission and office evidence docs.

Main follow-up files: `.github/workflows/ci.yml`; API `setup.py`, new `provisioning.py`, new `setup_router.py`, `main.py`, `models/autonomous_worker.py`, `tests/test_local_planning_setup.py`; web `Details.tsx`, `Office.tsx`, `Runtime.tsx`, `app.css`, `office.css`, `tests/app.test.tsx`; `scripts/smoke-browser.cjs`; Goal Mode docs.

## Validation evidence — distinguish versions

| Scope | Actually observed |
| --- | --- |
| Untouched initial baseline | Backend 739 passed, 2 deprecation warnings; frontend 25 passed; lint/typecheck/build passed |
| Published a964d028 local | Backend 746 passed, 2 warnings; frontend 45 passed; Ruff, frontend lint/typecheck/build passed |
| Latest follow-up local | **Backend 749 passed, 2 warnings in 120.42 s; frontend 46 passed; frontend production build, lint/typecheck, Ruff and browser-script syntax checks passed** |
| Focused follow-up | Setup + HTTP tests: 7 passed; real-process smoke passed with exactly one HTTP fixture inference |
| Migrations before follow-up | Blank DB applied all six migrations; downgrade to 20260729_04 then upgrade to head passed; no migration changed in follow-up |
| Integrity before follow-up | 209 tracked files checked, forbidden generated/runtime artifacts absent; `git diff --check` passed subsequently |
| Practical security scan | No credential-pattern candidates; no shell=True/eval/exec usage found in app/scripts scan. This is not an exhaustive security certification |

Warnings are Starlette/httpx TestClient deprecation and AnyIO BlockingPortal alias deprecation; baseline warnings, not new regressions.

From repo root, this session uses `.venv/bin/python` (Python 3.12.13). CI uses Python 3.12, Node 22, pnpm 11; session Node is 24.19.0 and pnpm 11.19.0. There is no root package.json.

```bash
# Backend, from apps/api
../../.venv/bin/python -m pytest -q
../../.venv/bin/python -m ruff format . --check
../../.venv/bin/python -m ruff check .
# Frontend, from apps/web
pnpm install --frozen-lockfile
pnpm office:assets
pnpm typecheck
pnpm lint
pnpm test
pnpm build
# From repository root
.venv/bin/python -m ruff format scripts --check
.venv/bin/python -m ruff check scripts
.venv/bin/python scripts/smoke-local-planning.py
node --check scripts/smoke-browser.cjs
git diff --check
```

Use an isolated temporary database URL when rerunning migration tests; do not downgrade user state. Read CI for exact Windows migration/integrity commands.

## CI and review status at transfer

Published commit a964d028, workflow run **33940781604**, `Phase 2A durable control-plane checks`:

| Job | ID | Last checked status |
| --- | --- | --- |
| repository-integrity | 101237655810 | success |
| runtime-browser | 101237655973 | success |
| frontend | 101237656019 | success |
| backend | 101237656032 | in progress, pytest step |

The Windows backend job was unusually slow. Logs requested while it was running returned BlobNotFound 404, so its cause was not diagnosed. Recheck; do not label it passed or broken without evidence. The follow-up checkpoint requires its own exact-head CI, irrespective of this older run. No fresh Codex review has been requested because all exact-head gates were not green.

After checkpoint CI is green, comment `@codex review` on PR #56 if supported. Address actionable findings with small fixes and appropriate regressions, push, await exact-head CI, request a new review. Maximum five complete loops. **Do not merge.**

### Continuation log: checkpoint validation and review loop 1

Workflow run **33941993004** completed successfully at exact checkpoint `c415b19050f22f4ce0b3156c300c73131c29326b`: backend, frontend, repository-integrity, and runtime-browser all passed. The exact-head browser artifact was downloaded and visually inspected (artifact **9962138836**, 1,994,664 bytes, archive SHA256 `58b47ac36d34f78ade0fbc3135988d825fad216a165cd2cb5a4e595463698b9a`). Desktop, 390 px mobile, and completed-planning evidence showed the follow-up control, link, danger-button, lifecycle/health, and fixture-disclosure fixes correctly. No horizontal mobile overflow or new visual defect was observed.

Codex review loop 1 was requested on PR #56 at exact checkpoint and completed as review **5119639019**. It found two actionable P2 issues in local-planning provisioning: setup checked assignment-only authorization instead of resource-aware policy, and a late incompatible runtime permission definition could leave earlier setup mutations. The continuation preflights every existing required definition before actor/grant mutation, evaluates final authorization through `check_permission_resource_access`, and adds regressions for both denial paths. Windows clock-resolution failures in the pre-existing revoked role/permission renewal tests also revealed that consecutive generated `starts_at` values can collide; generated role and permission assignment starts are now advanced by one microsecond on an exact key collision, with renewal assertions made explicit. Exact-head CI and review loop 2 remain required after this continuation commit is pushed.

Local continuation gates passed: backend Ruff and **751 tests**; frontend install policy, asset sync, typecheck, lint, **46 tests**, and production build; script Ruff, browser-script syntax, diff whitespace, and the real API + separate worker + deterministic HTTP fixture smoke.

Review loop 2 inspected exact commit `db9a4bd69d593ae6d0cab5fe9e3c8cae81eed140` after workflow run **33944374456** passed all four jobs. Review **5119806943** found that candidate inspection loaded geometry without enabling the viewport's review rendering, leaving most unselected shapes invisible. The follow-up wires the explicit candidate state to `reviewMode`, adds a UI regression for the candidate viewport class, and captures `office-candidate-review.png` in the real browser smoke. Frontend typecheck, lint, **47 tests**, and build pass locally. Exact-head CI, visual inspection of the new candidate screenshot, and another review remain required.

Workflow run **33945398040** then passed all four jobs at exact commit `0e3dca25506d68332da03b8100e9f8874bfd00bf`. Its browser artifact **9963170396** (3,055,403 bytes, SHA256 `6bf76995bf76554c93aecc217e9f1193b9ca9d1d2bf8d9d16db952eb6d30fcda`) was downloaded and visually inspected. The new candidate screenshot proves room overlays render over the canonical floor while the UI remains explicitly unverified; it does not approve registration.

Review loop 3, review **5119884926**, found that concurrent UI/CLI setup could race while creating the same missing definitions or actor. The follow-up reloads and validates the winning definition/actor after stable duplicate-key errors and adds a barrier-driven two-thread regression proving both calls converge on one actor, one definition set, and one grant per runtime permission. The Windows smoke harness also now decodes UTF-8 output safely and retries temporary-directory cleanup after child shutdown. Local browser replay still cannot launch because the installed Playwright cache is build 1228 while Playwright 1.62.1 requires build 1234; exact CI browser validation remains authoritative and no browser binary installation is claimed.

Local review-loop 3 gates passed: backend Ruff and **752 tests**, script Ruff and syntax checks, and the real API + separate worker + deterministic HTTP fixture smoke. The browser-mode failure path was also rerun and now reports the missing browser build without a secondary decode or cleanup failure.

Workflow **33947089245** validated frontend, repository integrity, and the real browser job at exact commit `254a94e5f33f379611e663ca7bc7bd5a8500f683`, but its backend job found a remaining concurrency defect after **751 passes**: concurrent setup could insert duplicate open-ended scoped grants because their generated start times differed. Browser artifact **9963677973** (2,985,908 bytes, SHA256 `2d5aa3ad1e75d6a1cc43a98e03aa3020c7e290e425cfd779560d0f4517cc076a`) was downloaded and visually inspected; selected-region focus, candidate rendering, and the explicit unverified boundary are visible.

The correction makes open-ended global and scoped permission-grant convergence a partial-unique-index database invariant in Alembic revision `20260905_06`. Finite intervals and revoked append-only history remain representable. Upgrade refuses any legacy duplicate open grants instead of silently rewriting authorization history. Local gates pass: Ruff across all 106 Python files, **753 backend tests**, frontend typecheck, ESLint, **47 frontend tests**, production build, browser-script syntax, and the real API + separate worker + deterministic HTTP fixture smoke. Exact-head CI and Codex review loop 4 remain required.

## Browser and visual evidence

The a964d028 Linux CI browser job actually ran the app and verified task creation, planning submission, completed output, Office floor load at 8192×5460, camera controls, candidate selection/focus, mobile width 390 with no horizontal document overflow, and no page/console errors or HTTP errors >=400.

The exact-head 254a94e browser artifact includes `office-desktop.png`, `office-mobile.png`, `planning-completed.png`, and `office-candidate-review.png`. The candidate view was visually inspected after the focus-wait fix and retains the unverified-registration warning. Source floor comparison used a diagnostic thumbnail only; no source artwork was modified.

Artifact: `jarvis-browser-evidence`, ID **9961748299**, run33940781604, 1,948,627 bytes, archive SHA256 `1dd8d570a10859c5c0b172217a2e49b22ab8d23f609ac3e45640819e1f842c39`.

This session's extracted evidence is `/workspace/scratch/878af0f74f1b/browser-evidence/`. A new environment should download the GitHub artifact rather than assume scratch persists. Supported GitHub artifact download returned file ID `file_0000000031dc81f5b043e590280f1d83`; Library prepare_materialize successfully placed it locally. Raw signed-URL download failed 403 and should not be retried as a workaround.

Local Playwright Chromium installation timed out five times. Cloud Browser localhost was blocked (`ERR_BLOCKED_BY_CLIENT`). CI provided actual browser validation instead. Do not claim local browser success. Local API and Vite processes used for that attempt were stopped. All known test sessions are finished at transfer.

## Visual contract and prototype source

User supplied a detailed pixel-art floor, a 30-agent roster, agent activity sheet, and Nexus tube animation references. More originals were found in **`DrewDiStefano11/jarvis-office-prototype`** (actual repository name; user called it agent-office-prototype).

Read-only clone: `/workspace/scratch/878af0f74f1b/jarvis-office-prototype`, main `7c5cd21cdce503f2e9ac94700c95b7faad8e1bfd`, no open PRs observed then. No source changes made.

- Canonical image: `public/assets/office/office-8192x5460.png`, 50,161,177 bytes, SHA256 `aa0ff821d5530e8ee1be3c1de733d0bff4bb74767a0ba27e48a141abf784d026`.
- Canonical office is DOM/SVG React; old Phaser README is stale.
- Candidate registration is **not production approved**, zero measured landmarks. Transform: scale 1.3333333333 from 6144×4096 to 8192×5460, offset (0,-0.6666). Do not turn approval flags on without verification.
- 907 candidate entities: 34 rooms, 167 paths, 82 walls, 178 objects, 47 doors, 144 lights, 44 computers, 205 positions, 6 interactive.
- Copied candidate JSON is under `apps/web/public/assets/office-candidate`; main repo integrity rules forbid tracked folders named `data`.
- Original navigation lives under `src/office/floor1/navigation/{candidateNavigation,continuousNavigation,prototypeRuntime}.ts`. Preserve its routing/collision/door logic and strict-vs-sandbox gates rather than replacing it with scripted movement.
- Red doors locked; blue requires explicit membership; yellow reserved; D47 elevator special. Do not infer access from colors beyond source contracts. Seat colors do not imply sprite pose.
- Original sprite sheets are under prototype `public/assets/office/sprites/generated`. They are not yet imported/bound to live identities in the Hub.

Critical office work remaining: measure/register geometry against the actual floor, validate routes and doors, establish durable worker-to-desk/spatial bindings and backend movement intent, then integrate the existing route implementation and original sprites. Current workers deliberately remain visibly unplaced.

## Prioritized remaining work

1. **Immediate transfer continuation:** fetch Goal branch, read this file/AGENTS/mission docs, inspect status and current PR chain. Preserve all checkpoint changes. Check new exact-head CI, investigate Windows backend if still slow/failing, retrieve new browser screenshots and verify setup button/mobile/control fixes. Then perform fresh Codex review.
2. **P1 validation:** run the configured pathway against an actual installed local model when available. No local model or suitable provider endpoint was available in this session. Fixture integration proves transport/state, not model output quality.
3. **P2 core product gap:** finish registered office geometry and durable spatial workforce integration using the original navigation, not random/scheduled motion. This is the highest-value substantial engineering continuation once checkpoint validation/review is resolved.
4. **P1/P2 broader autonomy:** investigate roadmap-backed next agent/tool workflows within existing security contracts. Current planning advice does not execute code or research tools. Do not enable dangerous capabilities merely to claim completeness.
5. **Reliability/product:** persistence of pending UI submissions across reload, operator review/resume UX, unified startup incorporating reviewed #53 rather than duplicating it, and additional failure/browser recovery checks where evidence shows gaps.

External constraints: no actual local model demonstrated; local browser hosting restrictions; no native Windows runtime in this container. These do not excuse unblocked implementation/CI work. Registration/navigation and tool breadth are unfinished engineering, not automatically external blockers. Business Lab and broad memory features must be justified from actual roadmap before creation.

## Practical environment and publishing notes

In this environment cooperating processes should be launched inside one shell/Python harness: separate tool commands appeared to have isolated networking even with shared filesystem. The smoke harness already does this. Do not spend another round assuming cloud Browser can reach container localhost.

Native Git read/fetch worked but `git push --dry-run` failed because no terminal GitHub credentials were available. Authorized GitHub tools published the branch/PR. If this remains true:

1. Stage intended changes and record `git write-tree`.
2. Use GitHub create_tree against the current head tree, changed text blobs only. Verify returned tree SHA equals local staged tree.
3. Create commit with current SHA as sole parent; update Goal branch ref with `force:false`.
4. Fetch the branch. Only advance the local ref after verifying old head is its ancestor and staged tree equals fetched tree. The previous session used `git update-ref <Goal-ref> <new> <old>` for this safe forward-only synchronization, not a reset or history rewrite.

Discover tool schemas from ALL_TOOLS; don't guess response wrappers. GitHub normalized responses generally put `jobs`, `sha`, etc. directly in structuredContent. Avoid printing full commit payloads; they include huge diffs. Fetch workflow jobs by run ID. Artifact download plus supported materialization works; raw downloads may be blocked.

Git-backed files should stay in Git, not be duplicated into Library. The user's original full Goal Mode instructions remain the mission contract. This checkpoint intentionally leaves PR #56 draft and records incomplete status. Finish with an honest A–N final report only when the mission reaches a valid stopping point, including exact SHA/CI/review evidence and explicit unfinished categories.
