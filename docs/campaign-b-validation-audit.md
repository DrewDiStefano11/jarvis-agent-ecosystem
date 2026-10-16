# Campaign B validation audit and integration

Audited baseline: `origin/main` at `5749a41` (2026-10-13), including the actual
merge commits for PR #111 and PR #113. Work is on `codex/campaign-b-validation`
in an independent worktree. No campaign source execution is enabled.

Integration update: `origin/main` advanced to `dc63ed2` when PR #112 merged.
Its native authorized mutation service and Alembic migration are now integrated
into this feature branch. The table below records the original audit; source
mutation is no longer an absent dependency, but connecting it to an approved
development proposal, verified validation snapshot and cross-source repair budget
remains unfinished. PR #114 recovery-history work is still a separate dependency.

## Implemented foundations and remaining connections

| Component | Baseline implementation | Remaining production connection |
| --- | --- | --- |
| Fixed impact/publication planning | `development_validation/planner.py` and `models/development_validation.py`; exact changed paths, Git modes, protected/generated rules, scope fingerprints, policy digest | Trusted native observer must supply complete source state to the journal |
| Fixed command catalog | `validation_runner/catalog.py`; Ruff format/lint, full/focused pytest, TypeScript, ESLint, Vitest, production build, migration roundtrip, integrity, browser and Office smoke | No launch-capable isolated adapter exists; integrity description needs a sealed native implementation |
| Authenticated receipts | `development_validation/evidence.py`; operator-controlled HMAC verifier, exact plan/command/state, sequence, freshness and replay checks | Collector outside candidate authority, independently measured tool/environment/isolation identity, and protected signing keys |
| Durable journal (#113) | `validation_journal/service.py` and `reducer.py`; audit/outbox intent, owner/lease/attempt checks, original operator approval, native checkpoints, uncertain reconciliation | Runner must consume acknowledged intents and independently measure actual effects; no production reader/collector wiring |
| Repair evidence (#111) | `development_validation/repair.py` and `self_improvement/validation.py`; authenticated exact-state findings and bounded advisory transient retries | Development-agent proposal/mutation/revalidation orchestration; never classify infrastructure as a product defect |
| Native source/Git | `self_build/git_observer.py`, `git_process.py`, inspection/change-verifier and checkout driver | Bind native inspected deltas to validation input snapshot; source mutation dependencies #112/#114 are not on this baseline |
| Runtime recovery | Existing agent runtime attempts, checkpoints, RBAC, workspace reservations and task leases | Reconcile external execution identity with durable intent; never relaunch an uncertain effect |
| Supervisor | Existing local runtime supervisor and owned-process lifecycle | Process ownership is not a security boundary for candidate test/build code |
| CI | `.github/workflows/ci.yml`, `scripts/backend_ci.py`, shard manifest/collector | Exact-head CI remains separate publication evidence; ordinary CI jobs are not verified isolated autonomous runner receipts |

## This integration milestone

`ValidationJournalService.diagnose` derives repair evidence from persisted journal
receipts and original independent approvals. It reads all same-workspace,
same-execution-plan journals, including older failed attempts, rather than accepting
a caller-selected history. Every contributing journal requires its native checkpoint.
More than 56 journals blocks diagnosis; this bound preserves the existing 512-receipt
limit without truncating away failures. Every supplied historical receipt is still
authenticated by the existing verifier. Unknown/missing/stale/uncertain work is an
evidence problem, not an invented code defect. Findings retain only fixed diagnostic
codes and exact reproduction references, never raw logs or model explanations.
Diagnosis selects receipts from the acknowledged journal's native runtime/attempt
identity, rather than ordering different attempts by collector completion time.
Earlier authenticated failures still consume retry budget. Missing current-attempt
receipts cannot inherit a previous attempt's outcome. Native ledger timestamp
checks and receipt-to-step time bounds continue to reject backward persisted time.

`record_verdict` records an exact acknowledged final journal assessment and diagnosis
in existing append-only audit storage with its outbox event in the same transaction.
It requires current original-owner authority, lease, live attempt, emergency-stop
checks, exact current approved source/policy and the expected final journal digest.
The journal identity supplies idempotency: a lost response is recovered by
`read_verdict`, not by rerunning tests. Rollback leaves neither audit nor outbox
evidence. No migration, HTTP importer, runner callback or competing scheduler is added.

A stored verdict is historical evidence. It cannot grant execution, mutation or
publication authority. Campaign C must perform a fresh `assess` against its final
exact source/head and current policy, and separately verify real collector/isolation
provenance before publication. Campaign A receives a diagnosis/proposal request only
for measured code/test/lint/build/migration failures; infrastructure/dependency/
watchdog issues retain reliability handling and operator-bounded retries. Its separate
approved source mutation path remains mandatory. Changing source requires a new
native observation, validation plan, independent approval, and revalidation.

## Isolation decision and exact blocker

The operator confirmed no dedicated runner is provisioned. Read-only discovery found
no `docker`, `Get-VM`, or `WindowsSandbox` command. Sandboxed CIM discovery was also
unavailable, so these observations do not prove the machine lacks virtualization.
Authorized read-only CIM discovery reports Windows 11 Pro `10.0.26200`, an active
hypervisor, and no `vmms`/`vmcompute` services. CPU virtualization-extension properties
report false under this running hypervisor; that is not treated as proof that the
physical hardware cannot support Hyper-V. A supported runner installation, guest
image and independently verified guest configuration are still absent.
There is no configured sealed guest image, VM/container identity, independently
verified security configuration, outside-guest collector or adversarial verification.
`DisabledValidationRunner` continues to return
`VALIDATION_EXECUTION_ISOLATION_UNAVAILABLE` before any effect.

Preferred provisioning direction: an operator-owned disposable Hyper-V VM (or
supported Hyper-V-isolated Windows container) on a dedicated runner host. Microsoft
documents a separate optimized VM and kernel for Hyper-V isolation:
[Isolation modes](https://learn.microsoft.com/en-us/virtualization/windowscontainers/manage-containers/hyperv-container).
Microsoft documents VM plus job-object resource controls:
[Resource controls](https://learn.microsoft.com/en-us/virtualization/windowscontainers/manage-containers/resource-controls).
This supports the architectural choice; it does not prove a Jarvis deployment.

Ordinary GitHub-hosted CI is useful independent test evidence but its clean runner
alone does not establish this brief's offline, no-credentials, bounded execution
boundary. GitHub warns that untrusted workflow code may access tokens/secrets and
that self-hosted runners lack clean ephemeral VM guarantees:
[Secure use](https://docs.github.com/en/actions/reference/security/secure-use).
No workflow or model result is accepted as an isolation attestation.

Before an execution implementation may be enabled, provision and independently
verify all of the following:

1. A supported disposable VM/image with pinned tool/dependency identities and no
   personal identity, host credentials, domain membership, collector key, mounted
   user directory or runtime database. Dependency preparation occurs separately
   from execution; candidate code gets no authenticated package/network access.
2. No connected external, host-loopback or management network from the candidate
   guest. Only guest loopback may serve browser acceptance. No clipboard, device,
   enhanced-session or writable host-folder redirection.
3. An immutable explicitly approved source snapshot whose complete bytes/modes
   independently match native source identity. Inputs/outputs have fixed count/byte
   bounds; no junction/symlink/Git-link escape or automatic host extraction.
4. Hypervisor-enforced CPU/memory/time/disk bounds, process accounting/limits inside
   the guest, bounded output streaming, and host-enforced stop/destruction. Existing
   command timeout/memory/output budgets are ceilings, not proof of those controls.
5. A sealed supervisor outside candidate authority. Candidate-controlled guest
   files/JSON/stdout must never attest successful execution. The collector measures
   process identity, exit, time/resources and output hashes through an independently
   protected channel and signs only those observations.
6. Durable VM/execution identity attached to acknowledged journal intent before launch;
   source/plan/lease/attempt/emergency-stop fencing at every effect and result commit;
   uncertain reconciliation for lost observation; no blind duplicate launch.
7. Adversarial filesystem, credential/environment, network, fork/process, disk/output
   flood, receipt-forgery, cancellation and host-restart tests. Then actual broken-code
   and corrected-code acceptance with new exact-state authenticated measurements.

Provisioning and proving this boundary is a remaining prerequisite. This milestone's
fixture receipt tests prove journal/authentication behavior, not real test execution,
secure guest isolation or autonomous repair. No end-to-end acceptance claim is made.

Other unfinished production work is explicit: a sealed source-snapshot transfer,
native integrity execution, outside-boundary resource/output measurement and receipt
signing, bounded redacted diagnostic artifact retrieval (codes/hashes alone do not
explain a traceback), Campaign A proposal dispatch and approved mutation, durable
cross-source repair iteration budgets, command interruption/reconciliation against
real guest identities, resource-bounded parallel gates and policy-proven component
reuse. The improvement adapter now routes infrastructure, dependency, CI, watchdog
and unknown failures to reliability observations even without a confirmed transient
code. Regression tests verify they produce evidence-gated retry-policy proposals
rather than product handler or test-coverage changes. A future automatic dispatcher
must preserve this distinction. Final publication needs real mandatory
exact-state measurements and Campaign C's independent authority, not this stored
historical verdict alone.

## Review and validation

The existing backend shard manifest covers additions to `test_validation_journal.py`
without changing shared shard ownership. Full collection, backend Ruff/pytest,
isolated migration roundtrip and frontend typecheck/ESLint/Vitest/build are required
before commit. No hosted independent review can be requested until an authorized
push/PR exists; local review is author-performed and is not independent review.

Developer validation completed on 2026-10-13: Ruff formatting/lint passed; all four
backend shards passed with 2,629 passes and 3 platform skips; the exact collected
node-ID union covers all 2,632 tests. The final journal module additionally passed
all 46 regressions, and the final renewed-grant audit/outbox regression passed.
Blank SQLite upgrade/current/history/downgrade/re-upgrade passed. Frontend
typecheck, ESLint, all 234 Vitest tests and production build passed. Tracked-artifact
integrity and `git diff --check` passed. These are developer checks, not authenticated
isolated-runner acceptance evidence. Logs and collection files remain ignored under
`.local/campaign-b-ci` and are not committed.

Author review checked original/current grant provenance, owner/attempt/lease fences,
transactional audit/outbox, receipt authentication, exhaustive bounded same-plan
history, checkpoint acknowledgement, stale-source denial, immutable historical reads,
and unconditional runner denial. Exact-head hosted CI and independent review remain
pending an approved push/PR. Secure execution, actual broken/fixed isolated-code
acceptance, and autonomous source repair remain unfinished.

Follow-up classification validation: 148 repair, self-improvement and evidence tests
passed, along with backend Ruff formatting/lint and frontend typecheck, ESLint,
234 Vitest tests and production build. These checks cover the reliability routing
change; the full-shard results above belong to the preceding journal milestone.

Integrated branch validation before the requested local commit: Ruff format/lint,
isolated migration roundtrip, frontend typecheck/ESLint/234 Vitest/build passed.
Backend runtime passed 402 tests, autonomy passed 503, and models passed 874 with
2 skips. The final system shard is still running; its result and the final exact
collection-union check are pending. Full collection contains 2,768 cases. Five
focused journal-lineage, missing-current-result and retained retry-budget
regressions passed. The local commit does not claim the final shard has passed
and does not enable execution or publication.

Subsequent result: that system shard completed with 986 passes and one skip,
bringing the integrated baseline total to 2,765 passes and three skips across
2,768 collected cases. The portable isolation contract/collector milestone is
documented in `validation-isolation-provisioning.md`; its focused local tests do
not prove real isolated execution.
