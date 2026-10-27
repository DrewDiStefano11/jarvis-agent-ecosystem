# Permanent-host acceptance and operator runbook

Use the existing Python 3.12 API environment and locked frontend dependencies. No script here installs/starts Ollama, downloads a model, enables cloud inference, provisions identities, changes permissions or promotes software. Do not run live-model options on the development computer.

## Start and operate

From the configured installation, build the frontend once with `pnpm --dir apps/web build`. Run `scripts\jarvis.ps1 doctor`, then `scripts\jarvis.ps1 start` and `scripts\jarvis.ps1 status`. Open the loopback dashboard shown by status. Use Planning for explicitly admitted goals and review exact workspace actions before approving them. Use `scripts\jarvis.ps1 backup` before approved maintenance and `scripts\jarvis.ps1 stop` for graceful shutdown. Follow [supervisor configuration](runtime-supervisor.md) for the existing opt-in worker.

The supervisor's optional autostart is **current-user logon**, not a boot service. Ollama is installed and operated separately by the owner. No production application data is changed by the offline acceptance command.

## Automated acceptance entry point

Run from repository root with the API environment's Python:

```powershell
# Offline preparation only: no subprocess or provider requests.
python scripts/permanent-host-acceptance.py

# Development-safe labelled deterministic controls: real isolated native ledger,
# scripted inference; these do not prove live model quality.
python scripts/permanent-host-acceptance.py --fixture-controls

# PERMANENT HOST ONLY: read-only installed-model inventory and runtime checks.
python scripts/permanent-host-acceptance.py --discover --runtime-diagnostics

# PERMANENT HOST ONLY: actual inference through existing configured local router.
python scripts/permanent-host-acceptance.py --local-model --provider ollama --model EXACT_INSTALLED_MODEL

# PERMANENT HOST ONLY: continue an existing explicitly queued authorized goal.
python scripts/permanent-host-acceptance.py --local-model --provider ollama --model EXACT_INSTALLED_MODEL --task-id EXISTING_TASK_ID --worker-id AUTHORIZED_WORKER_ID
```

Configure local-only provider settings and worker authority using the existing [worker setup](autonomous-worker.md) before live checks. The runner never sets those trusted flags itself. Model preflight checks health and exact model availability. Evaluation covers classification, decomposition, assignment, planning, specialist output, review, correction, synthesis and trust/structured-output cases. This is a bounded model case suite; it does not by itself prove production coordination. The optional task uses the production harness and durable checkpoints; it requires existing context/team/decomposition and refuses missing authority. It is skipped if model evaluation is not passing. A pending/backoff production result is nonpassing; resume the same task after eligibility rather than create duplicate tasks.

Each invocation creates a fresh run directory under ignored `.local/permanent-host-acceptance`, containing `acceptance.json`, `acceptance.md`, per-check logs and nested model/autonomy evidence. Stale files cannot supply a passing new run. Live evidence requires installed-local provider/model identity and the exact complete case set, with no duplicates or failed cases. Logs may contain authorized task/report information; keep evidence under operator control.

Statuses are `PASS`, `FAIL`, `BLOCKED`, and `NOT TESTED`. Exit 1 means an executed check failed. Exit 2 means acceptance remains incomplete or blocked. Individual green controls cannot produce full readiness; all six operational criteria retain `NOT TESTED` until their complete host evidence is evaluated independently. This initial runner does not aggregate external attestations or substitute assertions for actual host security evidence.

## Remaining host checklist

Record tested source SHA, configuration/safety fingerprints, timestamps, exact commands, artifact hashes, and actual outcomes for each item. Use FAIL for observed defects, BLOCKED for missing prerequisites, NOT TESTED for unexecuted checks. Never convert missing evidence to PASS.

| Criterion | Required host evidence |
|---|---|
| A Runtime | Supervised bounded soak with repeated goal cycles; API/worker simultaneous interruption; no duplicate effects; retained checkpoint recovery; logs/disk budgets; restore a verified backup into a disposable database and migrate/check integrity. Test current-user autostart after real logon/reboot separately. Record sleep/power availability without changing host settings automatically. |
| B Autonomous work | Multiple ordinary goals through the actual dashboard and configured model; inspect selected skills, durable dependency ordering, agent outputs, independent review, retries and final artifacts. Seed a defect only in an approved disposable repository, independently approve exact source mutation/validation, compare before/after tests and prepare a reviewable change. Record sequential production specialist limitation. |
| C Remote | Follow [direct TLS remote setup](remote-operation.md) with owner-managed certificate/secret and private-network access. From a separate authorized device, verify submit/status/result, cancel/pause, revoked/expired authority, malformed inputs, wrong Host/origin, denied controls and emergency stop. Existing mode is certificate-verified CLI; browser remote sessions/dashboard are still missing. Do not expose the local listener or bypass Origin rejection. |
| D Self-debugging | Inject a known failure only in disposable data; record Doctor diagnosis, deduplicated evidence/proposal, independently approved correction workspace, reproducer, failed-before/passed-after tests and independent review. A textual diagnosis alone does not pass repair. |
| E Isolation | Run `scripts/probe-validation-isolation.ps1` and review actual host capability results. Provision only explicitly authorized containment using existing host-specific instructions; prove filesystem/process/network/resource limits, secret exclusion, stale/forged receipt refusal, source identity and interruption recovery. Ordinary subprocesses/worktrees do not establish sandbox isolation. |
| F Improvement | Evidence-backed regression proposal, measured independent candidate comparison, duplicate/failed experiment retention, emergency stop/revocation/cancellation/restart handling and bounded scheduled cycles. Persistent improvement scheduling is a known software gap; do not certify this criterion using the advisory CLI alone. |

Actual CPU/memory/model capacity, cancellation/timeouts against the installed model, sustained workload, separate-device remote transport and isolation must be measured on the host. No fixed concurrency recommendation is supplied without those measurements. Keep autonomous native execution, cloud activation, publication credentials, and production promotion disabled unless their separate owner approvals and security prerequisites are satisfied.

## Update and recovery

Use reviewable feature PRs and existing required CI. The owner merges and promotes a known reviewed source SHA. Preserve source/configuration revision, independent validation/review receipts, compatible migration path and a verified backup before maintenance. Stop the runtime before an approved update; build/start and check health afterward. On failure, stop and preserve evidence; use the verified source/backup recovery procedure with migration compatibility checks. Do not blindly downgrade populated append-only history or overwrite the active database. Automatic production promotion and rollback are not implemented by this runner.

The wrapper reads fresh typed production evidence to distinguish pending/backoff and operator-blocked tasks (BLOCKED) from actual failed execution or identity mismatch (FAIL). Missing/malformed/mismatched evidence cannot pass; process timeout/crash cannot be reclassified from a partial file.
