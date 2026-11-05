# Permanent Windows 11 host: setup and staged acceptance

This procedure is for the separate permanent computer. Development evidence is in
[the integration handoff](handoffs/final-integration-20261105.md). It does not
establish live model quality, protected execution or 24/7 host acceptance.

## Install a reviewed revision with safe defaults

Install Git, Python 3.12, Node 22 and pnpm 11. Use a local disk and an ordinary
Windows user. Choose an owner-reviewed full commit SHA from PR #159 until it is
merged; `main` does not yet contain this integration. In PowerShell:

```powershell
git clone https://github.com/DrewDiStefano11/jarvis-agent-ecosystem.git
Set-Location jarvis-agent-ecosystem
git fetch origin
# Replace REVIEWED_FULL_SHA with the reviewed 40-character commit, not a moving branch.
git switch --detach REVIEWED_FULL_SHA
git rev-parse HEAD
py -3.12 -m venv apps/api/.venv
& ./apps/api/.venv/Scripts/python.exe -m pip install -e './apps/api[dev]'
pnpm --dir apps/web install --frozen-lockfile
pnpm --dir apps/web build
Copy-Item .env.example .env
```

Use a fresh installation directory. Never copy the development computer's `.venv`,
`node_modules`, builds, validation trees, runtime databases/sidecars, or encrypted
provider credentials. DPAPI credentials must be re-entered privately under the
permanent host's Windows user. Never paste keys or private evidence into Git/chat.

Review `.env` locally. Keep workers, inference, tools, improvement scheduling,
remote listeners and startup disabled. The checked-in defaults do this; do not
inherit enabled `JARVIS_*` process variables from another installation. Keep API
and web bindings loopback-only and retain exact Host/Origin checks. Choose local
absolute database/data/runtime paths if overriding defaults; do not use a network
share. The supervisor resolves the default database relative to `apps/api`.

```powershell
Push-Location apps/api
& ./.venv/Scripts/python.exe -m alembic upgrade head
& ./.venv/Scripts/python.exe -m alembic current
Pop-Location
& ./scripts/jarvis.ps1 doctor
& ./scripts/jarvis.ps1 start
& ./scripts/jarvis.ps1 status
# Inspect the loopback dashboard and health URLs printed by status.
& ./scripts/jarvis.ps1 backup
& ./scripts/jarvis.ps1 stop
```

Alembic head is `20261102_groq`. Investigate doctor failures before starting.
Management, dashboard and persistence must work without Ollama or cloud inference.
Do not start multiple installations on the same ports. Never replace a populated
runtime database with a fixture database.

## Offline verification before activation

From the root, use the fresh API interpreter for all backend commands. Leave the
computer awake and connected to power for the entire run. Closing a laptop lid
caused the historical simultaneous batch interruption; elapsed inactivity limits
must remain unchanged. These commands use disposable databases and model doubles.
The system shard can take over two hours; retain logs and phase evidence.

```powershell
$python = (Resolve-Path apps/api/.venv/Scripts/python.exe).Path
& $python -m ruff format apps/api scripts --check
& $python -m ruff check apps/api scripts
& $python scripts/backend_ci.py check --artifacts .local/backend-ci/backend-collection
& $python scripts/backend_ci.py runtime --artifacts .local/backend-ci/backend-runtime
& $python scripts/backend_ci.py autonomy --artifacts .local/backend-ci/backend-autonomy
& $python scripts/backend_ci.py models --artifacts .local/backend-ci/backend-models
& $python scripts/backend_ci.py system --artifacts .local/backend-ci/backend-system
& $python scripts/backend_ci.py migrations --artifacts .local/backend-ci/backend-migrations
& $python scripts/backend_ci.py verify --artifacts .local/backend-ci
pnpm --dir apps/web typecheck
pnpm --dir apps/web lint
pnpm --dir apps/web test
pnpm --dir apps/web build
& $python scripts/permanent-host-acceptance.py --fixture-controls
```

Check each exit code; stop dependent steps on failure. Coverage verification proves
collection union, not successful execution: require successful shard exit statuses,
complete JUnit and timing files too. Fixture acceptance intentionally returns
incomplete status for missing real host evidence. A skip is not a passed host check.

## Separately approved model acceptance on the host

| Mode | Prerequisites and bounded acceptance |
| --- | --- |
| Local-only | Owner installs/operates Ollama separately and records exact installed model identity. Follow [worker setup](autonomous-worker.md), using `local_only` and explicit loopback provider activation. Run the bounded installed-model evaluation in [host acceptance](permanent-host-acceptance.md), then one authorized dashboard goal. Record physical calls, result provenance and durable checkpoints. |
| Cloud-only | Ollama may stay absent/disabled. Privately save a disabled encrypted profile through [provider configuration](provider-configuration.md). Obtain fresh independently verified nonbillable account/key/model evidence. For Groq, prepare the native one-request plan, separately approve its exact hash and execute one request with $0 paid ceiling. No automatic retry or uncertain replay. Ongoing `free_only` activation and a bounded goal require separate approval; profile save or bootstrap success grants neither. |
| Hybrid | Complete local and cloud acceptance independently. Enable both only under their existing approvals. Demonstrate local preference and explicit provider/model selection on separately admitted bounded goals. Do not assume automatic fallback; planning routing disables cross-provider fallback. Missing quota or unavailable providers must block safely. |

Paid/credit-consuming models remain unavailable under the current policy. Do not
weaken that gate to obtain cloud-only operation. Start with one worker; the native
pool accepts integers 1–4, default 1. Raise it only after measuring actual host
memory/model capacity. Distinct workers/leases must own concurrent tasks.

For each mode, measure idle physical inference calls (must remain zero), cancel a
bounded in-flight goal, revoke authority, activate emergency stop, interrupt API
and worker, then restart and inspect existing task/run/attempt/checkpoint/dispatch
IDs. Completed or uncertain requests must not be redispatched. Restore capacity
without creating a duplicate task. Resume only through existing native recovery.
Do not claim a fixture proves provider-side cancellation or model memory release.

## Host capabilities and 24/7 acceptance

Follow all six criteria in [permanent-host acceptance](permanent-host-acceptance.md).
Record source SHA, configuration/safety fingerprints, Windows user, UTC times,
commands, exit statuses and retained artifact hashes. Mark each PASS/FAIL/BLOCKED/
NOT TESTED from observed evidence.

- Protected VM: use read-only isolation discovery first. Owner enrollment and an
  explicitly approved disposable VM must precede generated-code execution. Prove
  filesystem/process/network/resource boundaries, secret exclusion, stale receipt
  refusal and interrupted recovery. Worktrees/subprocesses do not prove containment.
- Remote access: keep disabled until separately authorized. Follow the dedicated
  TLS/browser guides with trusted certificates and private-network routing. Test
  from a second device, including wrong Host/Origin, CSRF, expired/revoked sessions,
  native permission denials and emergency stop. Never expose the local dashboard.
- Startup: only after accepted manual operation, separately approve the documented
  `jarvis.ps1 autostart install`. It is current-user logon, not a pre-login service.
  Test real reboot/logon, forced supervisor failure, bounded scheduled recovery,
  clean stop and retry exhaustion. Review sleep/power policy with the owner; no
  script here changes it or guarantees operation during sleep/power loss.
- Soak: agree an initial 24-hour bounded acceptance window and workload; inspect
  health, zero idle inference, resource/disk/log budgets, quota expiry, cancellation,
  backup hashes and one restore into a disposable database. Extend the observation
  before claiming reliable 24/7 service. No production database restore is automated.

Before updates, stop, back up and record the deployed source/configuration. Promote
only an owner-reviewed revision with compatible migrations. Populated append-only
history and encrypted credential state require an explicit recovery plan; do not
blindly downgrade or overwrite them. No automatic merge/promotion is authorized.
