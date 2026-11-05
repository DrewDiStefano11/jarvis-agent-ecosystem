# Final integration takeover — November 5, 2026

Work starts from PR #159 head `7136cefbb26b02d624eb0356bbbbcc8da942992a`,
not main. Fetched remote main remains `d8c90576f9fa04a56ff938b7deee005dbff32f72`.
Isolated branch: `codex/final-integration-20261105`. Original checkout/index,
runtime data and other worktrees remain untouched. No merge, live inference,
permanent startup, model installation or external access activation is authorized.
The owner authorized validated fast-forward pushes to #159 for this task.

## Findings and corrections

[CI run 41558163562](https://github.com/DrewDiStefano11/jarvis-agent-ecosystem/actions/runs/41558163562)
completed after the previous handoff. Models, system (1849 passed/1 skipped),
migrations, Linux Git, frontend and actual TLS/browser checks passed. Static failed
on 20 unformatted files; runtime failed only on CORS; autonomy failed only on
maximum concurrency. No newer remote commits or unresolved review threads were
found at takeover. Historical CI evidence does not validate new source.

- Apply Ruff formatting to the exact 20 failing files; no behavior change intended.
- CORS fixture now sends a loopback Host header. Its handwritten HTTP/1.1 ASGI scope
  previously omitted Host, which the new DNS-rebinding boundary correctly refuses.
  Keep rejection intact; five new cases prove loopback IPv4/localhost/IPv6 preflight,
  hostile Host rejection and hostile Origin refusal without handler dispatch.
- Replace the obsolete concurrency=2 rejection with the documented native pool
  contract: default 1, integer values 1–4 accepted, 0/5/negative/fraction/text denied.
  Native pool tests prove distinct workers/leases, overlapping calls, cancellation,
  completion and idle behavior. Do not return to a single-slot-only validator.
- Update stale README/host claims about schema head, cloud/hybrid routing, remote
  browser availability and persistent advisory scheduling. These features still
  require separate activation/authority and host acceptance.
- [Windows setup and staged acceptance](../windows-host-setup.md) records fresh
  installation, provider modes, exact approval gates, containment, logon startup,
  backup/recovery and a bounded initial soak. Paid inference remains unavailable.

## Historical inactivity diagnosis

Retained evidence was read from the old integration worktree's ignored
`.local/full-hardening-20261102`. All eight batches stopped at different test
setup/call phases between elapsed 634–748 seconds; there was no common test hang
or watchdog stack. Batch 0 began November 2 at 17:03:24 EST, last phase write
17:14:51, timeout JSON written 19:58:36. Every process reported about 10512 seconds
elapsed and code 124. No incomplete JUnit is counted as passing.

Read-only Windows System event query:

```powershell
Get-WinEvent -FilterHashtable @{
  LogName='System'; StartTime=[datetime]'2026-11-02';
  EndTime=[datetime]'2026-11-04'; Id=42,107,506,507
} | Select-Object TimeCreated,Id,ProviderName,Message
```

Kernel-Power event 506 at **17:15:11 EST November 2**: entering Modern Standby,
reason **Lid**. Event 507 at **19:58:38**: exiting Modern Standby, reason **Lid**.
The simultaneous loss of both per-test and parent watchdog progress, different
active test phases, and resumed timeout writes tie the common interruption to
machine standby. Event/write ordering differs by seconds, so no exact per-process
suspend instant is asserted. This explains the historical interruption without
increasing timeout budgets or excusing a future active-machine hang. Preserve the
300-second per-test and 1500-second inactivity limits. Keep unattended validation
awake/on power; no OS power setting was changed. System fixtures are separately
slow: prior CI completed in 7527.51 seconds, with some native setup/call phases
near 95 seconds. Slow progress is distinct from the standby interruption.

## Validation at the first repair publication checkpoint

Source: `7136cef` plus this isolated worktree's format/test/doc changes. Backend
shards use the original installed Python 3.12.14 environment read-only, with
PYTHONPATH pointing to this checkout and isolated temporary databases. A fresh
Python 3.12.10 venv was also created here and declared `[dev]` dependencies installed
successfully; process smoke uses that environment. Frontend: pnpm 11.28.5,
Node 24.17.0 locally; repository CI targets Node 22.

- Ruff format/lint: pass, 585 files. `git diff --check`: pass.
- Full collection: 4154 nodes (4149 existing + 5 preflight cases).
- Focused CORS/config/boundary/pool: 24 passed.
- Runtime: 437 passed, 250.92 seconds; harness exit 0.
- Autonomy: 509 passed, 215.14 seconds; harness exit 0.
- Models: 1355 passed, 3 explicit environment skips, 435.31 seconds; harness exit 0.
- System: running.
- Exact shard union: pass, 4154 unique nodes across four shards. System execution remains pending at this publication checkpoint; union alone is not a suite pass.
- Blank migration upgrade/downgrade/re-upgrade: pass, isolated SQLite database.
- Frontend typecheck, ESLint, 253 Vitest tests/36 files, production build: pass.
- Fresh Windows supervisor process smoke: pass; doctor, healthy API/web,
  owned API crash/restart, singleton start, backup integrity, restart, graceful stop.
  Ollama not required, worker disabled; startup was not registered. Evidence path
  printed in `.local/supervisor-smoke.log`; frontend rebuilt to defaults afterward.
- API + separate worker + frontend command replay + durable reviewed result: pass
  using an HTTP model double, no real Ollama. Dashboard browser smoke also passes
  submission/correction, lost-ack reload/recovery, desktop/mobile and office controls.
- `permanent-host-acceptance.py --fixture-controls`: fixture PASS; expected exit 2,
  overall NOT TESTED/production_ready=false for all six host criteria.
- TLS/browser acceptance: 2 passed (standalone and supervised), 17.18 seconds, fresh environment. Corrected absolute-path invocation passed.

Invocation failures are retained separately: initial focused pytest lacked its
basetemp parent (setup errors, not product failures), then corrected run passed.
Initial TLS invocation passed relative paths to an entry point that changes cwd;
its setup failed before application checks. Use absolute artifact paths.
The sandbox pnpm store symlink was denied; native offline locked install passed.

Exact commands (root; use the fresh interpreter on the permanent host):

```powershell
$python = 'C:/Users/DDistefano/Documents/jarvis-agent-ecosystem/apps/api/.venv/Scripts/python.exe'
$env:PYTHONPATH = (Join-Path $PWD 'apps/api')
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
& ./apps/api/.venv/Scripts/python.exe scripts/smoke-supervisor.py
# Restore default endpoint build after the supervisor smoke:
pnpm --dir apps/web build
& ./apps/api/.venv/Scripts/python.exe scripts/smoke-local-planning.py
$env:JARVIS_SMOKE_BROWSER='true'
& ./apps/api/.venv/Scripts/python.exe scripts/smoke-local-planning.py
Remove-Item Env:JARVIS_SMOKE_BROWSER
& ./apps/api/.venv/Scripts/python.exe scripts/remote_browser_acceptance.py -q --basetemp (Join-Path $PWD '.local/tls-browser-corrected-temp') --junitxml (Join-Path $PWD '.local/tls-browser.xml')
& ./apps/api/.venv/Scripts/python.exe scripts/permanent-host-acceptance.py --fixture-controls
```

Final CI and completed system evidence must be read from the PR #159 description and the latest check run for its exact head. This section records the first repair checkpoint, not a claim that an unfinished local system run passed. All 11 formatted production Python files were AST-identical to the integration base; only the three named regression test files have semantic Python changes.

## PR disposition and host limits

#156 (`f70632c`), #157 (`e80c9cf`) and #158 (`c7cdaaa`) are open drafts whose
implementation was incorporated in the #159 integration history. Recommend review
through #159; once the owner accepts that integration, close those PRs as
superseded rather than independently merge them. Do not close/merge automatically.
#160 remains separate at `81cd1a2`; its seven-file unvalidated improvement-candidate
snapshot needs its own diff/security/schema/test review against the accepted
integration. Its template PR body is not completed validation evidence.

Real Ollama/cloud requests, provider-side cancellation, private account capacity,
protected VM execution, actual scheduled logon/reboot, separate-device remote
access and sustained operation remain host-only/unaccepted. Existing process and
fixture results prove application boundaries under synthetic data, not those
capabilities. No model API tokens were consumed. No production readiness claim.
