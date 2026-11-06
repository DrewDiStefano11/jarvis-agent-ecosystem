# Software readiness — November 5, 2026

## Resume context

Owner requests offline readiness, repair of PR #160, actual services with intercepted inference, unattended-operation checks, and Windows deployment preparation. Validated pushes and CI inspection are authorized; merges, live paid inference and installing Ollama here are not.

- Fetched main: `f4cad14` (PR #159 merged). PR #160: `81cd1a2`, conflicts in self-improvement router, invalid escaped f-string prevents backend import/collection.
- Original dirty checkout/index and all prior worktrees preserved.
- Immutable baseline checkout: `C:/Users/DDistefano/.codex/worktrees/jarvis-readiness-20261105/jarvis-agent-ecosystem`, branch `codex/readiness-candidates-20261105`. Evidence: `.local/readiness/baseline`. Full baseline shards running; Ruff and collection (4154 nodes), blank migration cycle passed. Do not edit baseline while tests run.
- Repair checkout: `C:/Users/DDistefano/.codex/worktrees/jarvis-candidate-repair-20261105/jarvis-agent-ecosystem`, branch `codex/candidate-discovery-20261105`, based on main. Do not merge WIP #160 wholesale or copy historical handoffs into the new change.
- Baseline shards use original Python 3.12.14 interpreter read-only with PYTHONPATH selecting baseline source. Fresh baseline venv installed declared dependencies for process smoke; frontend installed locked dependencies offline. Offline Python cache was incomplete, so declared dependencies fetched into fresh venv.

## Candidate review

The WIP uses nonexistent SessionFactory import; treats scalar baseline ID strings as rows; compares entry IDs against admitted proposal IDs; omits required selected_by; manufactures pending tasks/selection timestamps for work never admitted; scans unbounded history; does not suppress duplicate candidate scopes; diverges from native ordering; and tests use inconsistent duplicate assumptions and an unknown actor for permission denial. Preserve only the read-only discovery intent. Reuse native ordering, RBAC, bounded scans and admission state; represent proposals without fabricated task/admission authority. Existing selector remains authoritative and rechecks at admission.

## Candidate publication checkpoint

- Based directly on merged main; only discovery source/contracts/tests/docs plus shard registration and this resume record are included.
- Ruff format/lint and whitespace: pass, 587 Python files.
- Focused discovery + backlog: 42 passed. Full collection: 4167 nodes; exact four-shard union passes (not proof of execution).
- Runtime: 437 passed. Autonomy: 509 passed. Models: 1368 passed, 3 explicit environment skips. Blank migration cycle: pass.
- Frontend: typecheck, ESLint, 253 Vitest tests in 36 files, production build pass.
- Full local system shard is still running; do not infer a pass. Baseline system is also running. Preserve both worktrees/evidence.
- Baseline runtime/autonomy/models pass (437/509/1355 plus 3 explicit skips), 4154-node union pass. Baseline supervisor process smoke, API/separate-worker transport, dashboard browser and actual TLS/browser acceptance (2 tests) pass. Fixture host runner returned expected exit 2 with fixture PASS and production_ready=false.
- A separate readiness branch `codex/offline-operation-readiness-20261105` in managed worktree `jarvis-offline-acceptance-20261105` fixes a reproduced missing durable-result context dependency. Three native cycles/restarts, zero idle recall, task isolation and bounded untrusted memory pass. Its broader checks are in progress; see its software-readiness handoff.

## Next actions

Read the new discovery PR's exact-head CI and complete both local system checks. Keep #160 intact until its replacement validates; no merging. Continue independent readiness checks and publish the memory/host-guide change separately. Final host-only tests (live providers, model quality/resources, reboot/logon, protected VM, separate-device remote transport, sustained soak) remain NOT TESTED. No Ollama installed, paid inference activated, startup registered or production data changed.


Review follow-up: discovery now explicitly refuses non-SQLite dialects using the
existing unsupported-database error, so the promised consistent read snapshot
cannot silently degrade on another isolation mode. Added one regression; focused
suite is 43 passed. Recollect and rerun the models shard for the new 4168-node
union; existing runtime/autonomy/system test selections are unchanged. The next
push supersedes the first #161 head and restarts hosted checks intentionally.
