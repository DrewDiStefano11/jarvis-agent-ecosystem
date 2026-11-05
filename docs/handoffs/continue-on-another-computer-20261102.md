# Continue the Jarvis integration on another computer

## Start here

Continue from **`codex/hosted-runtime-hardening-20261102`**, not `main` and not
the original dirty checkout. This branch contains the integrated readiness,
provider setup, Groq bootstrap, Ollama preservation, hosted/hybrid coordination
and runtime hardening work. It is a review draft, not a completed live acceptance.
The GitHub PR records the exact pushed head. Nothing has been merged or configured
for permanent startup.

```powershell
git clone https://github.com/DrewDiStefano11/jarvis-agent-ecosystem.git
cd jarvis-agent-ecosystem
git fetch origin
git switch --track origin/codex/hosted-runtime-hardening-20261102
```

For an existing clone, preserve its own changes before switching. Do not copy
old `.venv`, `node_modules`, builds, runtime databases, SQLite sidecars, encrypted
credentials or generated validation directories into the new checkout.

## What is preserved

- Readiness and local integration of PRs #156, #157 and #158: `1c0ea8e`.
- Guarded hosted development proposals: `2c44f4f`.
- Protected provider setup and remote browser controls: `8586920`.
- Separate one-request Groq acceptance and four-limit tracking: `8a8c90d`.
- Local/cloud/hybrid coordination and original Ollama behavior: `c4d09a3`.
- The current hardening branch adds local browser boundaries, a bounded 1–4-slot
  native worker pool (default 1), cancellation cleanup and atomic late-result
  fences, including the context-assembly escape identified in review.

The original checkout also had separate improvement-candidate work. Its seven
source/test/handoff files are preserved at **`81cd1a2da94ce13481e925e5085c41b8476c71ef`**
on **`codex/improvement-candidates-handoff-20261102`**, based on `577458f`.
That is a separate unvalidated WIP snapshot; do not merge it blindly into this
integration. Its original staging area was left byte-for-byte unchanged. The
1535 generated validation artifacts in that index were deliberately excluded.

## Validation: distinguish completed evidence from unfinished runs

Completed earlier full baselines:

- `8586920`: exactly 4045 collected; **4041 passed, 4 skipped**.
- `8a8c90d`: exactly 4081 collected; **4077 passed, 4 skipped**.

Latest source:

- Core first full run: 4129 tests, 4123 passed, 4 skipped, 2 failures. Both were
  fixed: local development clones must stay `local_only`, and the manual recovery
  fixture must disable competing background recovery. The affected 72 tests passed.
- Core rerun and current 4149-test hardening run **did not complete**. All eight
  batches in each ended with inactivity code 124. JUnit files were incomplete or
  missing, and the ad hoc aggregate failed on missing JUnit. Elapsed logs were
  approximately 11130 and 10512 seconds. No precise hang/suspension cause is proven.
  Do not count these runs as passing, and do not increase timeouts to hide them.
- Current final focused run: **50 passed** across hosted coordination, worker pool
  and existing team selection. All model responses were intercepted.
- Earlier security/compatibility subset: 127 passed. Worker/coordination subset:
  96 passed. The late-result fix passed its three focused regressions and review.
- Backend Ruff passed. Frontend typecheck, ESLint, **253 Vitest tests in 36 files**
  and production build passed. Migration roundtrip passed on the integration;
  hardening adds no migration.

Four earlier environment skips concern explicit local-model evaluation, a live
Ollama tool model, Windows file-symlink privilege, and an operator-enrolled Hyper-V
execution VM. Real junction tests ran. Do not enable models or OS features merely
to remove these skips.

The native two-task test uses real migrated temporary storage, task/context/runtime
services, distinct workers and leases. Both intercepted model calls overlap, both
tasks and runtime runs complete once, both workers reach idle without further
inference, and shutdown stops them. It proves native lifecycle integration with
intercepted models; **it does not prove live Groq inference**.

## First work on the next machine

Install Python 3.12, Git, Node 22 and pnpm 11 as described in README. Create a fresh
API virtual environment, install `apps/api` with `[dev]`, and install the locked
frontend dependencies. Use a new migrated runtime database; Alembic is authoritative.

Run the committed CI inventory and shards, retaining their artifacts:

```powershell
python scripts/backend_ci.py check --artifacts .local/backend-ci/backend-collection
python scripts/backend_ci.py runtime --artifacts .local/backend-ci/backend-runtime
python scripts/backend_ci.py autonomy --artifacts .local/backend-ci/backend-autonomy
python scripts/backend_ci.py models --artifacts .local/backend-ci/backend-models
python scripts/backend_ci.py system --artifacts .local/backend-ci/backend-system
python scripts/backend_ci.py migrations --artifacts .local/backend-ci/backend-migrations
python scripts/backend_ci.py verify --artifacts .local/backend-ci
python -m ruff check apps/api/app apps/api/tests scripts
```

Use the new virtual environment's Python. Each shard owns fresh temporary test
databases. Investigate inactivity using its durable pytest/progress/process logs;
do not reuse a killed process's databases or silently retry uncertain operations.
Then run `pnpm typecheck`, `pnpm lint`, `pnpm test`, and `pnpm build` in `apps/web`.
The committed workflow should also run on the published PR; inspect actual results.

Review against the actual current `main` before merging. Do not automatically
merge, delete feature branches, or enable permanent startup.

## Groq setup, evidence and explicit approval gates

The selected first model is **`openai/gpt-oss-120b`**. The old machine's personal
profile was saved with its protected credential and remained **disabled**. No live
inference or one-request activation approval was given or executed. Its encrypted
Windows DPAPI credential is machine/user bound and is **not in GitHub**. Configure
the key privately on the new Windows setup page; keep it out of chat, commits and
evidence uploads. A non-Windows credential store is not implemented by this UI;
do not replace encryption with plaintext as a workaround.

Keep worker, verified-cloud activation, remote access, workspace tools and startup
disabled. Preserve local Ollama support; no Ollama installation is required for
hosted operation. Hybrid routing honors explicit model/provider choices and local
preference without granting automatic inference fallback.

For the first Groq request, save the profile **disabled**, without inventing a
legacy quota window. Use the separate native acceptance preparation flow with
fresh independently observed personal workspace Free/nonbillable evidence,
credential association and exact model limits. Public documentation currently
lists 30 RPM, 1000 RPD, 8000 TPM and 200000 TPD, but exact private limits may differ.
Check the console's TPM hover for any separate input/output limits too. Public
limits and a successful metadata listing do not prove private billing state or
which account owns a key.

The [schema/example directory](groq-evidence/) contains the exact proposed
FreeAllowance and GroqAccountEvidence JSON schemas, public model limits and an
**intentionally invalid UNVERIFIED null template**. Do not turn unknown fields into
verified claims or renew old observation timestamps. The old private observations
are stale. Evidence must be at most 15 minutes old for native plan operations;
the exact plan itself expires after five minutes.

Only after preparing a concrete native plan should the owner separately approve
its exact hash and **one fixed request** with a $0 paid ceiling. The request has
no tools, fallback or automatic retry and cannot enable ongoing inference. A
successful response initializes RPD/TPM header observations; RPM and TPD remain
independently enforced through local tracking without asserting unused account-wide
capacity. Respect 429, Retry-After and reset times. Unknown outcomes are not replayed.

After that, actual cloud task submission, assignment, inference, execution,
independent review, reporting, recovery and shutdown still need a separately
approved bounded live demonstration. Do not describe the intercepted tests as
that demonstration. Generated-code execution additionally requires the existing
operator-enrolled protected VM; host shell/generated-code execution remains off.
No model requests should be generated while idle.

## Remaining review and environment limits

Browser QA of the local acceptance page remains pending. Automatic approval review
rejected opening the synthetic page because its service hit a usage limit; the
browser action was not executed and no alternate UI automation was used. ASGI,
Vitest and build checks are separate completed evidence. Repeat browser QA when
available, using isolated synthetic data before private account actions.

The broad original mission includes operational concurrency, contained software
factory execution, supervisor/remote operation and improvement scheduling. Much
existing implementation is integrated and tested, but do not call all nine phases
operationally accepted: latest full CI, actual cloud lifecycle, protected VM proof,
browser acceptance and final deployment review remain open. The original API/UI
on the old machine were not upgraded or enabled by this handoff.
