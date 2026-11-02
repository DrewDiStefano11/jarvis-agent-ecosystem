# Provider setup — 2026-11-02

Branch `codex/provider-setup-20261102`, base `2c44f4f83d8d8cbf0db0b6399c0080c2a51ad188`.
Operator selected **Groq / openai/gpt-oss-120b**. A protected credential is saved locally.
The Personal Free plan and four model limits were independently observed; no admissible
remaining-quota grant exists. No live inference or activation is approved. Do not ask for keys in chat.

## Final baseline validation

- Fresh exact coverage: **4,041 passed, 4 skipped, zero failures/errors**, covering all 4,045 collected backend tests.
- Evidence: ignored `.local/full-combined-20261102-fixed/`; source SHA-256 `d50cbc221a51dd3a95332dec46c555df9680c6a1bcd590938b58ed79d8430e9b`, unchanged throughout all eight isolated batches. Per-test timeouts remain 300 seconds, without retries.
- Skips require two explicitly configured local models, Windows file-symlink privilege (real junction checks pass), and an operator-enrolled Hyper-V VM. No substitute live-capability claim is made.
- The initial full run found three failures. SQLite migration commands now use a real transaction so a retention guard rolls back the entire downgrade, preserving original revision, schema and evidence. Eighteen migration regressions pass; four legacy expectations now require stronger whole-command rollback. Noncanonical injected development settings fail closed before admission/router access.
- Backend Ruff, frontend typecheck/ESLint, all **250 frontend tests**, and production build pass. Vitest required native execution because sandbox transform-cache renames failed before test collection.
- Existing real TLS browser/supervisor acceptance remains recorded below. Cloud inference, ongoing activation and permanent startup remain disabled; no push or merge is authorized.
- The separately approved one-request Groq bootstrap and independent durable RPM/RPD/TPM/TPD implementation are ongoing in `codex/groq-acceptance-rate-limits-20261102`; this baseline does not claim that newer work is complete or fully validated.

Prior checkpoints: `1c0ea8e94254c89587b490e1301d0805b63dd9bd` integrates #156/#157/#158,
cloud-only supervisor startup and bounded Git metadata authorization reads. Full
runtime402/autonomy503 passed; models1345 passed/3 explicit skips; system1713 is
still running in the frozen operational-readiness worktree, exec session47488,
`.local/exact-1c0ea8e`. Do not duplicate or modify its source. `2c44f4f` adds guarded
hosted text code proposals, explicit capabilities and native hosted route admission;
153 broad +57 final targeted cases passed, full collection3984, Ruff550 passed.
Frontend234 tests/typecheck/lint/build and migrations passed on byte-identical parent
frontend/schema. Hosted fixture success is not actual Groq inference.

Continue inline under the campaign specification; no further design approval round
was requested. Implement a complete backend-controlled provider setup panel using
existing native services and shared frontend state. Prioritize Groq while retaining
the existing fixed compatible HTTP integrations and optional loopback providers.

Implementation decisions to preserve:

- Alembic migration owns persisted provider settings and encrypted credential bytes;
  no create_all. Windows user-bound DPAPI reuses the existing tested credential
  protection; unsupported platforms fail closed for secret storage.
- Configuration is distinct from execution activation. Saving a key/evidence/model
  never activates inference, upgrades billing or enables a worker. Current Campaign D
  activation, exact account evidence, credential fingerprint and durable quota remain
  mandatory. Missing models must not block application management.
- Use a dedicated model-configuration permission on an administrative resource,
  active operator identity and existing native write fences. Local browser actions
  require the configured Origin; external origins and remote routing cannot reach
  credential endpoints. Explicit local operator provisioning can follow existing
  task-actor setup but grants no runtime/admin/tool permissions and never reactivates
  suspended identities or removes denials.
- Save/replace/disable use compare-and-swap revisions and the existing append-only
  audit/transactional outbox. No credential or full private evidence is returned in
  API views, errors, events, diagnostics or frontend store. Password inputs remain
  transient and are cleared; no browser persistence or logging.
- Persist provider/model priorities and implemented text-capability assignments;
  wire those into real router model selection instead of displaying unused roles.
  Explicit request/provider/model choices and native development admissions win.
- Current configuration changes must revoke stale provider snapshots before any
  physical request; sidecar startup and supervisor configuration must load the same
  validated persisted policy. No idle inference and no automatic connection test.
- A user-requested account connection check must perform actual bounded metadata
  access and report its limits; it is not an inference acceptance test. Actual task
  acceptance uses the existing dashboard/runtime/checkpoints after verified activation.

Implemented, not yet committed: Alembic `20261102_models` encrypted profile table;
native CAS/RBAC/audit/outbox service; explicit narrow operator setup; uncached
loopback/Origin API; persisted startup/supervisor policy with read-only preflight;
actual model/category/priority routing; stale-provider revocation before physical
dispatch; AppStore provider projection and `/models` dashboard. Groq metadata check
uses a bounded fixed-endpoint catalog GET, separate from inference/eligibility.
Other hosted integrations have no metadata-check capability. Global mode/activation
remain operator environment controls; no activation or account proof has been supplied.

Verification: 228 focused provider/router/free-capacity/supervisor tests passed
74.13s; final complete provider boundary file21 passed15.60s after metadata bounds
and pre-dispatch guard changes. Full frontend typecheck/ESLint/Vitest237 tests35
files/build passed. Ruff562 files formatted. Blank Alembic upgrade/downgrade/
re-upgrade passed. Independent review found aggregate priority/evidence startup
limits were bypassed on saves; shared full Settings validation fixed it, two RED
then GREEN regressions preserve earlier profiles. Follow-up review confirms the
fix and metadata/dispatch guards, no remaining findings.

Headless installed Edge on `http://127.0.0.1:18786/models`, desktop1440x1000 and
mobile390x844, provisioned a temporary operator and saved a synthetic disabled
Groq profile against an isolated temporary database. Correct title, meaningful
content, zero console errors/inference requests/overflow; key field cleared and
key absent from DOM/browser storage. Browser plugin unavailable; Playwright used.
Screenshots/report outside repository in the chat visualization directory:
`provider-desktop.png`, `provider-mobile.png`, `provider-ui-check.json`.
This is configuration acceptance, not real Groq or inference acceptance.

Full frozen integration suite still runs in the separate1c0ea8e worktree;
runtime402/autonomy503/models1345+3skips passed; system shard was62% at last
meaningful checkpoint. No full-backend success claim for this changed feature.
Do not start duplicate suites or commit without completing required checks.

Next: inspect/apply the already implemented remote-browser commit, retain strict
local-only configuration routes; finish capability/source-contract checks and
full integration verification. User was asked asynchronously whether Groq account
is Free Plan vs Developer/Paid; answer/evidence and separate activation still
answered: personal Groq free account. This is user-supplied account context, not
independently verified billing/model evidence or activation approval. Never infer
approval or eligibility from silence. The remote-browser patch has now been
applied to this worktree; four context conflicts resolved manually, preserving
provider startup validation, new docs and all test shard assignments. Remote
features remain disabled; combined validation is next. The earlier collection
check hit the known sandbox temporary-directory ACL failure; use native elevated
collection after the combined source stabilizes.

Combined remote/provider verification: 51 backend tests passed45.39s; added exact
remote denial of all local provider/setup/metadata routes passed3.42s. Full combined
frontend typecheck/ESLint/250 Vitest tests36files/build passed. Actual-process
standalone and supervised TLS/browser acceptance both passed21.91s, including
native controls, session restore/logout and supervised API/TLS restart. Reports
outside repository under chat visualization `remote-browser/`. Native elevated
manifest/collection passed4039 cases before the six new pin tests. Native development
admission invalidation + hosted proposal/replay package19 passed110.02s.

Persistent user-facing setup is running at `http://127.0.0.1:18788/models`, API18787,
sessions89481 and63931 (API PID54808). Database is ignored
`.local/operator-runtime/jarvis.db`, Windows DPAPI keys only. Modefree_only, cloud
activationfalse, allowancesempty until saved evidence; worker/Ollama/generic
compatible provider/tools/remote explicitlydisabled. Browser IAB tab1 is marked
deliverable; user instructed to configure/select model operator, enter Groq key
there, and save with profile enabled checkboxoff. Never inspect password DOM or
replace real credentials with fixtures. Operator views now include only SHA256
fingerprint so evidence can bind the saved key without reopening it. Focused
fingerprint contract passed and final typecheck/lint/3 UI security tests passed.
Temporary UI fixture API50612/Vite27248 were positively identified and stopped;
its dummy DB remains separate. Do not stop the persistent setup while user enters key.

Additional CI performance work now in progress: bounded60s existing cProfile
showed12850 RBAC calls/93693 SQL operations and repeated pin preparation. Added
200ms read-only cadence to checkout_driver pin_read_tree/pinned_object_store,
with unconditional entry/final fences and unchanged file/namespace pins and
physical mutation guards. Added checkout_driver and commit_metadata to development
admission source dependencies. Six regressions cover frozen-clock call bounds,
write-denying file handles, final/mid-scan revocation. Initial11 metadata tests
passed; final13 plus the same3slow native recovery tests run in session67511,
`.local/native-cadence-comparison.log`, unchanged300sec timeout. Independent focused
review found no mutation-authority regression. Inspect terminal timings before
claiming a performance improvement. Do not duplicate the benchmark or baseline suite.

Latest user steering: first model `openai/gpt-oss-120b`; requires exact allowance
schema/example based on independent current account/model facts, no invented
evidence; inference remains disabled until separate activation approval. User
saved Groq profile (revision2), protected credential configured, enabledfalse,
stateevidence_required. Model field set via scoped CUA locator without reading
password fields. Do not reload/rewrite user's form or inspect credential DOM.

Read-only Edge verification found existing user Groq keys tab937869623 (never
claimed/read). Agent research tab937869627: Personal workspace, Free$0 Current
Plan, actual organization ID read from General; GPT120b limits30RPM/1000RPD/
8000TPM/200000TPD; DefaultProject has no custom overrides. Usage page explicitly
says data delayed up to15minutes, so do not infer current remaining quota from it.
Exactly one real catalog-only GET through native authorized/audited metadata
service succeeded200 and listed requested model at2026-11-02T16:16:53Z. Only
Date header returned; no quota headers. No inference. Script refuses repeating
completed observation. It uses protected key internally; never prints key values.

Artifacts outside repo in chat visualization directory: exact live OpenAPI
`groq-allowance.schema.json`, `groq-allowance.UNVERIFIED-template.json` (nulls for
unverified required values; must not upload as verified), partial account
`groq-personal-account-observations.json`, sanitized
`groq-catalog-observation.json`, redacted local profile projection. Template binds
actual observed account ID and stored key SHA256, but account/key association,
current remaining requests and actual quota window/reset remain unverified.
No complete allowance created/uploaded. Base schema marks fingerprint optional
generically, but runtime requires it for Groq. Upload is one object, no wrapper;
no extra fields; timestamps offset-aware; exact model/profile/capability matching;
four safeguards must be true/false/false/false on actual verified evidence.
Current schema cannot represent RPM/TPM/TPD independently; disclose this limitation.

Performance terminal evidence: same3native recovery tests +13metadata/pin tests
all16 passed421.34s versus585.45s earlier (about28% less wall time), timeout
unchanged300seconds. Setups113.88/115.20/85.47s still expensive; no claim full CI
performance resolved. Review found no effect-authority regression.

Fresh full combined backend verification now running ONCE in session47625,
`.local/full-combined-20261102`, eight isolated batches, exact4045-node plan.
Source base2c44f4f dirty-source manifest
`aecd93004a264ffcb42bd7f1e9214163c98b55aed389d5024f20609730e9536f`.
Unchanged300s per-test/1500s inactivity guards; no retries. Frozen source must
remain unchanged during this run; handoff docs excluded from byte manifest.
Runner checks exact unique batch/JUnit coverage and source bytes at completion.
Do not start another suite. Old frozen1c0ea8e system shard separately still runs
(68% last meaningful checkpoint); other3shards complete. Do not claim either
full suite passes before terminal evidence. API/user setup remain running.
Groq official billing and organization-wide rate-limit docs were rechecked at
https://console.groq.com/docs/billing-faqs and https://console.groq.com/docs/rate-limits;
these do not establish this account's tier or capacity.

Recovered remote-browser work remains at `8fccb752a607f6f120b5a917097a498386380c7f`.
Windows containment discovery found no Sandbox executable/Hyper-V module/services;
optional feature state needs admin discovery. No untrusted generated code may run
on the host. Push, merge, provider activation and startup registration still need
their specified owner approval. Original primary checkout/staged user files preserved.
