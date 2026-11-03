# Open PR recovery campaign

Updated 2026-10-12. Controller: chat `01a13012-3002-7593-90de-25be09e50769`.
Human merges only; pushes and review requests authorized by pasted campaign instructions.

## Live inventory and scheduler

Fetched main: `c3939851504ab1ddd0eb0a17a139836d858bf161`; #94 is merged.
Dependency: #94 -> #93 -> #99. Other five PRs are independent.
All recommendations currently HOLD; no exact final head has satisfied every gate.

| PR | Head | Base | State | Evidence / next action |
|---|---|---|---|---|
| 93 | `5172bc117368c8a7328509948cf0ad948045be1f` | `codex/self-build-inspection-reconcile` | REPAIR_REQUIRED | Current review 4247796810: atomically claim target before yield. Main includes parent a16bc13; reconcile base with main after ancestry/diff audit. Latest CI running. |
| 95 | `d1b661f65a7e13b4372c7e748950a37423ffec89` | `main` | REPAIR_REQUIRED | Repair misleading approved lane, vanished status option, overlapping refresh. CI 38010109424 fails `test_restart_finalizes_durable_result_without_model_recall`: expired lease count 0 vs 1. Reproduce before diagnosis. |
| 96 | `8664705965bc14bf3171393b74807c5977358fe9` | `main` | ACTIONABLE | Live mergeable (snapshot conflict obsolete). CI 38065467659 passed; no independent review. Inspect verifier confinement, manifest, SQLite and negative cases. |
| 97 | `f779583617a4bbac95500f19c07eaf8d45bc7def` | `main` | ACTIONABLE | Jules updated after review of 00d6b49. Verify effective resource policies, intervals/revocations and metadata redaction on latest code; fresh review needed. |
| 98 | `6059e087bef2252c14b96b82a94e8e30f3fa66fe` | `main` | REPAIR_REQUIRED | Conflicts with main. Verify repairs after 95a4ff9 review: advisory gates, raw thresholds, secret-safe metadata; preserve all shard tests on integration. |
| 99 | `f1e663c9529cf315260da47a05d84c0fe9b0dbfd` | `codex/self-build-checkout` | REPAIR_REQUIRED | New review 4247928544: component case collisions before writes; review body also missing remote operator service. Latest CI running. Keep stack until human merge of #93. |
| 100 | `6f194458b70c2b8a6bcabc88bd229ec1795f1bc3` | `main` | REPAIR_REQUIRED | Match authoritative temporary name 60 / role 100 limits and unique demonstration heading ID; fresh validation/review needed. Latest CI running. |

## Ownership and recovery

- Primary checkout remains on main `4758891354e69c54d2765623aed5234672d4543a`; unrelated untracked `validation-pr63/` preserved. This handoff is local and uncommitted.
- Existing Self-Build worktrees belong to active chat `01a112bf-4784-7433-abcb-a365f36942b4` ("Read pasted task instructions"). User authorized ownership coordination; sent current #93/#99 findings and ownership proposal. Do not edit these worktrees concurrently.
- This controller owns independent repairs #95/#96/#97/#98/#100. Managed frontend worktree creation pending operation `aaa22b0e-4444-4dce-b647-7777e0e164b4` from `origin/grok/workforce-agents`.
- No test results claimed yet for this controller. Audit gathered PR files, commits, reviews, full review threads and failure log; evidence under `C:/Users/DDistefano/AppData/Local/Temp/jarvis-pr-recovery-01a13012`.
- Sandbox blocks .git writes/network and existing external worktree reads by default. Approved elevated fetch/read succeeded; use narrowly scoped elevation where needed.

## Scheduling

Start #100's limited frontend repairs while Self-Build owner repairs critical path. Then audit #97's P1 and #96 read-only security boundary; repair #98 conflicts and #95 behavior. Request one review only after known fixes and validation. Never duplicate pending exact-head CI/review. Do not open replacement or bookkeeping PRs.
