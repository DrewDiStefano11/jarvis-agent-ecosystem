# Campaign D implementation and validation evidence

## Acceptance boundaries

- Implementation: six opt-in hosted integrations, private eligibility records,
  shared durable quotas, bounded switching, safe pause and recovery evidence.
- Local acceptance: mock transports, signed OAuth fixtures and isolated temporary
  databases. No cloud sign-in, inference or provisioning was performed.
- Remote CI: deliberately skipped; account-specific Actions storage safeguards
  are unverified. Skipped checks do not establish CI acceptance.
- Live provider acceptance: pending independent account evidence and separate
  bounded-test authorization. All cloud connections remain disabled.
- Independent PR review: pending. No paid or credit-consuming review is requested.

The original publication base was `dc63ed220541adfa6ba3fb11fd665e4c4a52370d`
(`origin/main` inspected during finalization). The draft PR records the published
head and full changed-file list. Raw logs, fixture credentials and temporary
databases remain in ignored local directories and are not committed.

## Finalization review findings

Corrected formatting failures and integrated the quota migration after main's
`20261012_source_mutation`, preserving a single Alembic head. Hosted completions
now reject credential echoes and returned models outside the verified allowance.
Native Codex rejects absent/unknown credit status as well as positive credits,
and accepts a completed final message even when no delta event arrived. Expired
OAuth sessions cannot query the account model catalog. Every failed physical
attempt is audited even if a subsequent retry succeeds. Free-only doctor output
cannot probe cloud accounts or claim account verification.

Every registered workflow trigger and job was inspected. Push events exclude only
`codex/campaign-d-zero-cost`; every job, including the `always()` aggregate, fences
that branch for PR events. `[skip ci]` is additional protection, not the sole gate.
Other branches and PRs retain all checks. A regression test rejects an unfenced
workflow, trigger or job. No repository-wide Actions setting was changed.

Unrelated handoff files, validation directories and other worktrees were preserved.
A concurrent unstaged edit to `apps/api/app/self_improvement/router.py` was left
untouched and excluded from the Campaign D commit and validation claims.
The Campaign D diff was inspected against the integrated main base. Credential
pattern checks, tracked-artifact checks, `git diff --check` and Git connectivity
checks passed. These checks complement manual review; they cannot attest live
account billing policy.

## Local validation commands and results

The final integrated backend results are **2,793 passed, 3 skipped**. Exact
coverage was verified for all **2,796 unique collected tests** across four shards.

| Gate | Result |
| --- | --- |
| Backend runtime shard | 402 passed |
| Backend autonomy shard | 503 passed |
| Backend models shard | 902 passed, 2 skipped (affected shard rerun after final audit fix) |
| Backend system shard | 986 passed, 1 skipped |
| Full collection and exact shard union | 2,796 unique tests; passed |
| Ruff lint and format check | Passed |
| Frontend typecheck and ESLint | Passed |
| Frontend Vitest | 234 passed across 33 files |
| Frontend production build and office assets | Passed |
| Blank SQLite upgrade/current/history/downgrade/re-upgrade | Passed |
| Local browser planning/recovery smoke | Passed |
| Local persisted office/activity/emergency-stop smoke | Passed |
| Original office collision/registration evidence | Passed |
| Git connectivity, diff whitespace and tracked-artifact checks | Passed |
| Remote CI, Linux-specific hosted checks | Skipped/pending; no remote execution authorized |
| Real provider/account billing safety | Unverified; no live requests |

From the repository root:

```powershell
.venv\Scripts\python.exe -m ruff format apps/api scripts --check
.venv\Scripts\python.exe -m ruff check apps/api scripts
.venv\Scripts\python.exe scripts/backend_ci.py migrations --artifacts .local/campaign-d-finalize-migrations
.venv\Scripts\python.exe scripts/backend_ci.py check --artifacts .local/campaign-d-finalize/backend-collection
.venv\Scripts\python.exe scripts/backend_ci.py runtime --artifacts .local/campaign-d-finalize/backend-runtime
.venv\Scripts\python.exe scripts/backend_ci.py autonomy --artifacts .local/campaign-d-finalize/backend-autonomy
.venv\Scripts\python.exe scripts/backend_ci.py models --artifacts .local/campaign-d-finalize/backend-models
.venv\Scripts\python.exe scripts/backend_ci.py system --artifacts .local/campaign-d-finalize/backend-system
.venv\Scripts\python.exe scripts/backend_ci.py verify --artifacts .local/campaign-d-finalize
git diff --check
git fsck --connectivity-only --no-dangling
```

From `apps/web`: `pnpm typecheck`, `pnpm lint`, `pnpm test`, `pnpm build`.
Local smoke uses `scripts/smoke-local-planning.py` with `JARVIS_SMOKE_BROWSER=true`,
then with `JARVIS_SMOKE_OFFICE=true`, separate ignored artifact directories and
the repository's temporary-database/loopback fixture infrastructure.

Focused regressions also exercise failed authorization, missing credentials,
credit uncertainty, evidence expiry, credential leakage, unverified returned models,
concurrent reservations/startup, shared exhaustion, restart, bounded fallback,
durable dispatch/failure events and no-capacity pause. An intermediate diagnostic
test assertion used a Pydantic method on a dataclass; it was corrected to inspect
the actual dataclass serialization and rerun. No safety control was weakened.
The successful-retry test also corrected an error-category assertion to the
existing `transient_provider_failure` contract. The refreshed models shard passed
all 53 Campaign D cases. Final provider/audit changes were additionally covered by
the worker/router regressions; unaffected prior gates were retained.

The models shard includes the Campaign D cases. Its two skips
are optional local-model evaluation (`JARVIS_EVAL_LOCAL_MODEL` is unset) and a
file-symlink race test requiring privileges this Windows account lacks. Real
junction tests run separately; these skips are not successful live acceptance. The system shard also skips a
live native-tool Ollama test because no real tool-capable model is configured.

## Remaining owner gates

See [the activation checklist](campaign-d-activation.md). The owner must identify
each account privately, provide independently verified free/included entitlement,
prove provider-enforced billing/credit/recharge exclusion, bind exact credentials
and models to actual quota/reset evidence, and approve a bounded live acceptance
test. Insufficient evidence means disabled; unenforceable cost prevention means
unsuitable. No provider has passed these gates.

For remote CI, the owner must separately establish a hard $0 Actions storage
boundary before removing the Campaign D branch quarantine. A successful local
suite or a skipped remote check never substitutes for that evidence.

## Conflict resolution against newer main, 2026-10-13

Merged `5e11d60545006eda9a0f5a77affcb5860e2c3926` into the Campaign D branch
in an isolated checkout. The shared checkout's unrelated branch and staged work
were preserved. Resolved both conflicts by retaining main's commit-operator router
registration and Campaign D's session-backed model router, and assigning both
the free-capacity and native-artifact test files to the models shard.

Conflict validation: **119 targeted backend tests passed**, including API startup,
all Campaign D guards, shard integrity and commit-operator OpenAPI/authentication.
Complete collection contains **3,046 tests**, with every file assigned exactly once.
Ruff lint/format, migration round-trip and diff whitespace checks passed. Frontend
typecheck, ESLint, **234 Vitest tests**, production build and office assets passed.
An initial test invocation lacked its temporary parent directory; setup was
corrected and the complete targeted selection rerun successfully. No test or
safety control was disabled. Commands used the existing repository venv with
isolated temporary databases; the target selection was:

```text
pytest tests/test_api.py tests/test_free_ai_capacity.py tests/test_backend_ci.py
  tests/test_git_commit_authorization.py::test_native_preparation_openapi_and_operator_authentication
  -q --basetemp=../../.local/conflict-tests-final --timeout=300
```

The earlier full-suite counts above are historical evidence from publication;
this conflict resolution reran applicable regressions and collection, not the
entire expanded backend suite. Remote CI remains intentionally skipped, and
live cloud acceptance and independent review remain pending.
