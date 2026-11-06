# Software readiness and permanent-host handoff — November 5, 2026

## Scope and resume

Main audited at `f4cad147bde5243c9f0073c1204852af52a403cb` (merged #159).
Owner authorizes isolated development, validated pushes and CI inspection; no
merges, Ollama installation here, paid inference or production startup activation.
Original dirty checkout/index, runtime data and every previous worktree remain intact.

Managed checkouts:

- `jarvis-readiness-20261105/jarvis-agent-ecosystem`: unchanged main, branch
  `codex/readiness-candidates-20261105`, under `C:/Users/DDistefano/.codex/worktrees`.
  Evidence `.local/readiness/baseline`; full system shard still running.
- `jarvis-candidate-repair-20261105/jarvis-agent-ecosystem`: discovery replacement,
  branch `codex/candidate-discovery-20261105`, commit `df84a76`, [PR #161](https://github.com/DrewDiStefano11/jarvis-agent-ecosystem/pull/161).
  Evidence `.local/readiness/candidate`; full system shard still running.
- `jarvis-offline-acceptance-20261105/jarvis-agent-ecosystem`: this independent
  memory/acceptance/host-guide repair, branch `codex/offline-operation-readiness-20261105`.
  Evidence `.local/readiness/operation`.

Always use explicit checkout paths. Installed original Python 3.12.14 dependencies
are read-only; PYTHONPATH points to the checkout being tested. Baseline process
smokes use a fresh venv with declared dependencies. pnpm 11.28.5 locked frontend
install used existing offline cache. Local Node is 24.17; hosted CI targets Node 22.
No runtime databases, sidecars, dependencies, logs or builds are committed.

## Reproduced issue and correction

Three separate native tasks completed through actual worker/runtime/lease/SQL
services with only model responses intercepted. Each cycle reopened the database
in a fresh application graph, retained checkpoints and finished results, and made
exactly one planning call; three idle polls per cycle made no additional calls.
The before-fix test then failed because a fresh context assembly omitted the
persisted result. ContextEnricher inspected attributes absent from the actual
repository; the execution repository lives on app.state.

The API now passes that existing execution repository explicitly. A bounded SQL
query retrieves only the same task's two most recent completed/human-review
results. Tests prove task isolation, exclusion of newer failed results, ordering,
limits and retained `prior_model_output` trust in the assembled model request.
Summary length, injection/redaction and context budgets remain authoritative.
This is task-scoped result recall, not cross-task learning or model authority.
No migrations, event versions, response envelopes or permission policy changed.

## Observed development evidence

| Check | Result |
| --- | --- |
| Main Ruff, full collection and exact shard union | PASS, 585 files / 4154 nodes; union is collection only |
| Main runtime / autonomy / models | 437 / 509 / 1355 passed; 3 explicit model-environment skips |
| Main and discovery full system execution | IN PROGRESS at publication; no pass inferred |
| Discovery replacement | Ruff, migrations, frontend pass; 42 focused, 437 runtime, 509 autonomy, 1368 models passed; 3 explicit skips; 4167-node union |
| This branch Ruff / full collection | PASS, 586 Python files / 4155 nodes |
| This branch runtime / autonomy / models | 437 / 510 / 1355 passed; 3 explicit skips |
| This branch migrations | PASS, blank upgrade/downgrade/re-upgrade |
| This branch frontend | Typecheck, ESLint, 253 Vitest tests/36 files, production build PASS |
| Native-service operational acceptance | 229 passed in 91.73 s; JUnit `native-acceptance.xml` |
| Focused memory/context/pool | 40 passed; strengthened repeated-cycle regression also passed separately |
| Real Windows supervisor, unchanged main | PASS: doctor, healthy API/web, owned API-child crash/restart, singleton, backup, restart, graceful stop; worker/inference disabled, no startup registration |
| API + separate worker + frontend transport | PASS using loopback HTTP model double |
| Dashboard browser | PASS: submit/correct, lost acknowledgement and reload/recovery, one result per task, desktop/mobile/office controls |
| Real TLS/browser security | 2 passed using fresh isolated environment |
| Finished workspace report | PASS: exact persisted-plan UI approval, real native list/read/report, hash-bound browser artifact and API restart; 2 fixture model calls |
| Backup restore | PASS: verified SHA-256, copy into fresh disposable database, Alembic upgrade, integrity/foreign keys and 64 table row counts preserved; original backup unchanged |
| Permanent-host fixture runner | Fixture PASS, expected exit 2, overall NOT TESTED and production_ready=false |

The native acceptance set covers bounded parallel **different tasks** with distinct
workers/leases, independent node critique and correction, checkpoint/lost-ack and
lease recovery, scheduling/cycle claims/revocation/emergency stop, independent Groq
rate dimensions/429/unknown outcomes, and supervisor/backups/Task Scheduler contracts.
Coordinator specialists within one decomposition remain sequential. These controls
do not prove arbitrary software generation or protected generated-code execution.

Process evidence on this development computer:

- Supervisor report: `C:/Users/DDistefano/AppData/Local/Temp/jarvis-supervisor-acceptance-uo9yfe_n/supervisor-smoke-report.json`.
- Workspace report/receipt/artifact: `C:/Users/DDistefano/AppData/Local/Temp/jarvis-workspace-tools-a76izawk`.
  Report hash `e409e04599d97b1ae12e155606bc2f77da3d7ce217e8b74d737a8507f3b3e7ea`.
- Restore report: baseline `.local/readiness/baseline/backup-restore.json`.
- Fixture host report: baseline `.local/permanent-host-acceptance/20261105T181154Z-b6436ab29c16`.

An initial restore-inspection helper left SQLite connections open during temporary
cleanup on Windows; the corrected helper explicitly closed them and passed. Initial
repeated-cycle test setup attempts collided with existing permission stable keys
and reversed worker name/instance arguments; corrected setup reached the missing
memory assertion before production code changed. These invocation/setup failures
are retained in ignored evidence and are not counted as product acceptance.

## Repeatable commands

Use the API interpreter from the chosen checkout, with PYTHONPATH set to its
`apps/api`. Full required validation and permanent host installation/provider/
Task Scheduler/update procedures are in [Windows setup](../windows-host-setup.md).
The development-safe native pytest command is recorded there. Other root commands:

```powershell
& ./apps/api/.venv/Scripts/python.exe scripts/smoke-supervisor.py
pnpm --dir apps/web build # restore default endpoint bundle after supervisor smoke
& ./apps/api/.venv/Scripts/python.exe scripts/smoke-local-planning.py
$env:JARVIS_SMOKE_BROWSER='true'
& ./apps/api/.venv/Scripts/python.exe scripts/smoke-local-planning.py
Remove-Item Env:JARVIS_SMOKE_BROWSER
& ./apps/api/.venv/Scripts/python.exe scripts/smoke-workspace-tools.py --provider fixture
& ./apps/api/.venv/Scripts/python.exe scripts/remote_browser_acceptance.py -q --basetemp (Join-Path $PWD '.local/tls-temp') --junitxml (Join-Path $PWD '.local/tls.xml')
& ./apps/api/.venv/Scripts/python.exe scripts/permanent-host-acceptance.py --fixture-controls
```

Do not run the supervisor and other build-changing smokes concurrently. Root build
commands use locked dependencies. Preserve 300-second per-test and 1500-second
inactivity limits; keep the computer awake/on power without changing OS settings
implicitly. Interrupted/incomplete JUnit is never a pass.

## Next and host-only acceptance

Inspect exact-head CI for both new PRs; finish retained baseline/discovery system
runs, check successful exit metadata and JUnit, then record final results. The
initial #161 push workflow was cancelled by the newer pull_request workflow's
normal concurrency policy, not by a code failure. Do not rerun unchanged cancelled
work needlessly. Keep original WIP #160 until the replacement passes; its branch
must remain preserved. Do not merge any PR automatically.

On the permanent host, install only an owner-reviewed full SHA. Separately accept
Ollama installed-model quality/cancellation/resources; privately re-enter Groq
credentials and verify actual nonbillable account/model capacity before any
bounded request; accept local/cloud routing independently before hybrid mode.
Planning has no automatic cross-provider fallback. Workers/remote/scheduling and
startup stay default-off until their separate native/operator gates are met.

Actual reboot/logon/Task Scheduler retry exhaustion, sleep/power availability,
sustained bounded workload, separate-device TLS transport, protected VM isolation,
real model calls and provider capacity remain NOT TESTED here. Follow all six
criteria in [host acceptance](../permanent-host-acceptance.md), with exact source,
configuration/safety fingerprints, timestamps, commands, exit codes and artifact
hashes. No unattended 24/7 or production-readiness claim follows from fixtures.
