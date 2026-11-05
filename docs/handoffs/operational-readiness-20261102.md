# Operational readiness campaign — 2026-11-02

Branch: `codex/operational-readiness-20261102`; base: `d8c90576f9fa04a56ff938b7deee005dbff32f72`.
Workspace: `.worktrees/operational-readiness-20261102`. Preserve the primary checkout's staged changes and validation artifacts.

The supplied campaign brief is the execution specification. Work proceeds inline; user explicitly requested implementation without another design/plan approval round. No pushes, merges, cloud activation, billing changes, startup registration or uncontained candidate execution are authorized.

| Priority | Task | Dependency | Status / next action | Evidence / blocker |
|---|---|---|---|---|
| 1 | Integrate #156, #157, #158 | Current main | Applied in that order, uncommitted; review and test combined source | GitHub confirmed exact heads f70632c, e80c9cf, c7cdaaa; no inline review threads; first two system shards failed, third CI passed historically |
| 1 | Repair native Git CI performance | Profile affected test | Profiling isolated reproduction before proposing fix | CI stacks show repeated authority/journal work during metadata scans; sandbox default pytest temp setup failed before test execution |
| 2 | Cloud-only supervised startup | Existing Campaign D gates | Add supervisor regression, then reuse settings validation for eligible free_only workers | Config currently unconditionally requires local_only plus a local provider |
| 2 | Provider configuration and hosted development | Integration, independent evidence | Inspect/reuse native provider configuration and proposal boundaries | Live activation requires separate operator approval and independently verified nonbillable account/model capacity |
| 3 | Real dashboard tasks, concurrency/recovery | Eligible provider | Verify deterministic contracts first, then live acceptance after approval | No live provider acceptance claimed |
| 4 | Software factory / containment | Hosted proposals, host containment | Preserve exact source approval and native validation gates | Untrusted generated code must not execute on host |
| 5 | Remote control, self-improvement, deployment | Core execution | Reuse recovered remote branch and integrated scheduler | Local remote implementation found at 8fccb752a607f6f120b5a917097a498386380c7f; not yet applied |

Required checkpoint checks: backend Ruff format/lint, pytest and exact shard collection; migrations; frontend typecheck, ESLint, Vitest, build; focused runtime/provider/recovery acceptance. Historical PR evidence does not validate combined source. Do not commit until required local checks run.

Resume from this worktree. Python dependencies currently available through the primary checkout's `apps/api/.venv/Scripts/python.exe`; set `PYTHONPATH` to this worktree's `apps/api` so imports use the integration source. Use isolated temporary test databases and distinct temporary roots. Do not poll unchanged external runs.

## Verified checkpoint

- Remote `main` reverified via `git ls-remote`: `d8c90576f9fa04a56ff938b7deee005dbff32f72`. Exactly three open draft PRs (#156–158), with heads listed above. No inline review threads at inspection time.
- Combined three PR commits applied without conflicts or a new commit. No push or merge.
- Supervisor now accepts `free_only` enabled workers without local providers. Existing Settings validation rejects activation without account-specific allowances. Five configuration/doctor regressions pass, including disabled-Ollama probe rejection and default-disabled/local-only controls.
- Native metadata measurement now bounds database-backed scan authorization to the established 200 ms read cadence while retaining unconditional opening/final checks, file handles, namespace watches and all mutation fences. RED regression: 92 callbacks for a 44-node tree; bounded implementation requires two with frozen time. Added final/during-scan revocation tests and moved transient-name injection to the actual byte-read boundary.
- Single bounded 60-second diagnostic profile retained in `.local/native-baseline.prof`: 12,850 resource permission checks and 93,693 SQL executions before fixture completion. This intentionally interrupted profile is not a passed test. Earlier sandbox temporary-directory/hardlink errors are environment failures, not production acceptance.
- Combined focused suite: 251 passed, one explicit live-model skip, two subprocess-import failures because the command ran from repository root. The two exact scheduler crash-recovery cases then passed from `apps/api` (9.92 s). No product fix was needed for those invocation errors.
- Full backend Ruff lint/format passed (548 files); `git diff --check` passed. Full backend collection/shard-file integrity passed: 3,966 cases. `.local/backend-ci/check` retains node collection; execution union verification remains pending.
- Blank SQLite upgrade/downgrade/re-upgrade passed through `scripts/backend_ci.py migrations`.
- Frontend typecheck and ESLint passed; all 234 Vitest tests across 33 files passed; production build passed. Sandbox Vitest initialization failed on temporary-file rename permissions before tests, then native execution passed.
- Real Windows supervisor smoke passed with no model inference or startup registration: API/web healthy, owned API child crash/restart, duplicate-start handling, SQLite backup and integrity, restart and graceful stop. Report: `C:/Users/DDistefano/AppData/Local/Temp/jarvis-supervisor-acceptance-dyqudrft/supervisor-smoke-report.json`; retained log `.local/supervisor-smoke.log`. Its temporary frontend ports must be restored to defaults after the smoke build.
- Independent read-only review of the combined diff reported no actionable findings. It did not run tests or attest live inference, protected generated-code execution, or permanent-host readiness.
- Source provenance: `.local/test-source-manifest.json` records SHA-256 for 705 tracked source/test/build-contract files; manifest SHA-256 `6328de2b1b71add6bc2e82578e8b2c058e986364fabe3938aa8e82b3e6526942`. HEAD remains the base while staged/unstaged integration changes are tested.

The reported native Git regressions and metadata security tests finished: **10 passed** in 585.45 s, without changing timeouts. Reported test bodies took 43.23 s (foreign-command recovery renewal), 36.19 s (lost final acknowledgement), and 67.57 s (native intent acknowledgement recovery). Fixture setup remains expensive at 129.79–155.91 s per case: CI performance is improved but not fully resolved. No whole-system shard pass is inferred.

The complete runtime shard finished: **402 passed** in 353.52 s; harness exit 0 in 357.20 s. Logs and exact node IDs remain in `.local/runtime-ci.log` and `.local/backend-ci/runtime`.

Frontend default endpoint build restored after the supervisor smoke (`127.0.0.1:8000`, WebSocket `/ws/events`). Tested source manifest comparison reports no changes. Final Ruff format/lint and diff whitespace checks passed.

Read-only containment discovery reconfirmed Windows 11 Pro 10.0.26200, an active hypervisor, 24 logical CPUs and about 68 GB physical RAM. Sandbox executable, Hyper-V module, vmms and vmcompute are absent; optional-feature states require administrator discovery and remain unknown. `.local/isolation-discovery.json` is a diagnostic, not validated containment. No Windows features or guests were enabled.

Next: commit this source checkpoint after required local checks, then run remaining model/autonomy/system shards against that exact commit. Preserve the runtime evidence and compare the final node-ID union; do not duplicate successful unchanged checks. Push/remote CI remain behind owner approval. Provider UI, hosted development, live inference acceptance and remote browser integration remain unfinished.
