# Campaign A: development agent

## Current audit

Authoritative base: origin/main `5749a41bbe2f5a6f3f4f396ab11e5662fc91e047`,
fetched October 13, 2026. The independent campaign branch is
`codex/campaign-a-development`. No open prerequisite code is imported.

Merged implementation provides native runtime attempts/checkpoint recovery,
task leases, identity/RBAC, emergency stop, transactional audit/outbox,
Context Assembler, local-only ModelRouter, structured model requests,
qualification, team selection/decomposition/specialist planning, independent
planning verification, workspace reservations, native Git inspection and Windows
checkout materialization. Source-change contracts, append-only source-edit plans
and protected review interfaces also exist. Some older documents still describe
pre-checkout capabilities; the implementation is authoritative.

The audited main baseline does not implement a development inference loop, durable
development iterations, repository search context or source mutation. At that audit, PR #112 was open
against main with the mutation adapter; PR #114 was open against #112 with
recovery/artifact history. PR #118 merged into #114's feature branch, not main.
Its native source inspection is consequently unavailable on this baseline.
PR #119 separately owns Git preparation/authorization/publication provenance.
Campaign B owns validation execution/journal/runner; this campaign changes none
of those modules.

Task creation and Planning currently queue planning advice or marked report
workspace actions. Mission Control reads native domain state through
`apps/web/src/state`; it does not expose a development task admission flow.

## Implemented source-context/proposal milestone

`RepositoryContextReader.inspect` is an internal service composed with the
existing native Windows ChangeVerifier. Its caller supplies an existing owned
workspace and the exact original creation authority, including the live lease.
Native ownership, creation checkpoint, current policy, independent operator
approval, runtime attempt/executor, permissions and emergency stop are checked
before reads, during observation and immediately before returning evidence.
No HTTP source importer or model tool is registered.

Source/metadata/ownership handles and namespace watches remain held while
selected file bytes are read and compared with the independently measured full
native inventory. Contents preserve UTF-8 bytes, CRLF and final-newline state.
Topology contains all measured ordinary directories/files and explicitly measured
absent requested leaves. Source contents are limited to selected files; other
files must be requested in another bounded read. The selection allows at most
64 paths; each selected file is at most 64 KiB, the source observation at most
1 MiB and 4,096 entries. Binary selected files, hardlinks, unsafe namespace,
dirty index, missing ownership or checkpoint and stale context refuse.

The context digest binds task/run/attempt/workspace/creation lineage, exact
complete inventory identity, selected contents/topology and the original CodeState.
A digest describes observed state, never mutation or validation authority.
It changes when an unselected source file changes, preventing mixed snapshots.
Literal reference search returns only locations in selected source and explicitly
reports unread files and result truncation. Existing test-name discovery returns
heuristic paths from the measured topology; it does not certify test coverage.

`DevelopmentProposal` is untrusted model data: summary, measurable completion
criteria, context digest and bounded add/modify/delete file contents. It contains
no actor, repository identity, approval, executable command, filesystem root or
mode grant. The compiler supplies identities and modes from native evidence,
verifies claimed original digests, checks exact selected paths and runs the
existing source safety planner. Deletion requires a trusted explicit opt-in.

`DevelopmentProposalService.persist` re-measures source after inference and
compiles/persists only if the exact context digest is still current. Persistence
runs inside native pins and the existing fenced source-plan transaction. Existing
source-plan command replay, protected reads, audit/outbox and restart semantics
are reused; source contents do not enter audit payloads. It proposes multi-file
changes and derives real validation requirements, but applies no filesystem edit.

## Provider decision

The operator selected independent OpenAI API key provisioning. Secrets are saved
only to the explicitly confirmed ignored environment file, never this document.
The existing runtime provider policy remains disabled/local-only. Provisioning
a credential alone does not enable network traffic, cloud fallback or source tools.

Supported options were checked against official documentation:
[Codex SDK](https://learn.chatgpt.com/docs/codex-sdk) exposes thread run/resume,
and [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
supports schema-guided proposals. A Codex executor needs a separately reviewed
execution boundary because its native tools can run commands. A proposal-only
Responses integration can avoid that capability, but needs a dedicated explicit
provider policy rather than weakening the existing local-only worker.
Neither option is implemented by this milestone.

The existing Context Assembler redacts source data before prompt construction,
can exclude injection-bearing sources and can decline over-budget context. The
model-facing development bridge must require every selected source and refuse
full-file replacement generation when source was redacted or truncated. It must
not make redacted bytes authoritative, silently omit required files or promote
repository content into instruction roles. The native context retains exact
original evidence separately from any model-facing view.

## Remaining campaign requirements

A durable iteration ledger must bind admitted objective/criteria, repository
context, provider dispatch/accounting and persisted response/proposal. An uncertain
inference dispatch must not be blindly repeated. Cancellation and stop must
prevent late response commits. A separately enabled proposal-only provider must
retain identity, bounded physical requests, usage, timeouts and failure handling.
Deeper symbol/module relationships and a model-facing context bridge must use the
existing Context Assembler trust/redaction boundary. Current literal reference
search and test-name hints retain scope/completeness facts; they do not claim a
complete semantic dependency graph.

Once #112/#114 reach main, synchronize and integrate exact mutation-plan review,
independent approval, native execution and acknowledged recovery history.
Repeated development cannot silently bypass the one-mutation-per-workspace limit.
A new lineage policy requires adversarial recovery/provenance tests, including
stale approval, prior verified work, new modules and restart boundaries.

Validation handoff must bind task/run/attempt/workspace, development iteration,
independently measured post-mutation CodeState, source checkpoint/history and
requested validation plan. Projected source state from this milestone cannot be
submitted as verified source or passing validation. Campaign B diagnoses must
bind their exact original source-state and durable journal identity before a
repair iteration can use them.

Publication handoff additionally needs repository/base identity, full development
lineage, complete validated source-state evidence and explicit publication
readiness. Campaign C owns publication effects. No model success flag is evidence.

Final acceptance still requires a capable live model to implement a real feature,
actual native source mutation, durable restart readback, stale authorization
rejection and validation handoff. Deterministic model fixtures only test contracts
and recovery; this milestone is not campaign completion.

## Campaign integration notes

Campaign B's proposed repair diagnosis must retain journal ID/final digest,
original operator approval, execution-plan hash and exact original CodeState hash.
A later correction iteration must load those protected native records and verify
its source context against that same state; caller/model diagnosis text alone is
insufficient. B's runner does not receive source mutation authority.

Campaign C requires the protected applied-source record ID, complete mutation
journal/final checkpoint, task/run/attempt/workspace ownership, exact source
observation and retained-artifact manifest digests, and checkpointed prospective
commit preparation. A projected source plan from this milestone supplies none
of those post-mutation attestations. Preserve the existing mutation/history and
artifact readers once their prerequisites reach main. C owns publication and
native commit modules; A owns development context, inference and proposals.


## Correction baseline after publication

The initial workspace baseline remains immutable: merged GitChangeObservation
requires its head to equal the original base, ChangeVerifier measures the
creation plan's original base, and the dependent source mutation service binds
source head/base to the reservation base. A successful commit that advances an
owned branch invalidates reuse of that initial baseline for another edit.

The next iteration must either use a separately authorized fresh correction
workspace rooted at the published commit, or consume a protected final native
commit checkpoint receipt that independently proves the current commit/tree
while preserving original creation ownership and complete development lineage.
No rebaseline adapter is implemented or enabled by this milestone. Model/caller
head overrides, changing the creation record or relaxing existing head/base
checks are not valid corrections. Design the protected baseline interface with
Campaign C before enabling automatic post-publication corrections.


## Local validation checkpoint

All 2,637 collected backend cases are assigned exactly once to the existing four
shards. Complete Windows shard results: runtime 402 passed; autonomy 503 passed;
models 849 passed/two intentional skips; system 880 passed/one intentional skip.
Total: 2,634 passed, three intentional skips, no failures. The new development
context/proposal/navigation package also passes all 22 focused cases, including
native checkout reads and application recreation against the persisted database.
Backend Ruff lint/format, blank migration upgrade/downgrade/re-upgrade, frontend
typecheck/ESLint/234 Vitest tests/build and Git whitespace checks pass.
These are local checks; fresh hosted CI and human review are still required.


## Merged mutation integration update

Origin/main advanced to dc63ed2 on October 13 after #112 merged. That merged
implementation is integrated into this feature branch; #114 remains a separate
recovery-history dependency. The initial audit above describes the earlier base.

The source-plan compiler now derives the native adapter's exact immediate-sibling
observation from the larger repository context. It retains verified root marker
and Git-pointer topology without reading or exposing their bytes, includes all
siblings at affected ancestor levels, includes content only for proposed changes,
and excludes unrelated absent candidate leaves. The complete native CodeState
still retains unrelated source changes. Full repository context identity is
rechecked before that narrower mutation observation is derived.


Integrated dc63ed2 validation: all 306 development/native-proposal/source-planning
cases pass. The two subsequently added private-metadata cases pass in the final
five-case selection package; the final restart-strong native two-file case passes
again. Final collection assigns all 2,768 tests. Integrated frontend typecheck,
ESLint, 234 Vitest tests/build, Ruff lint/format and blank migrations pass.
The full 2,634-pass/three-skip checkpoint above belongs to the earlier baseline;
it is not represented as an uninterrupted full run on this integrated revision.

The real native integration derives a multi-file proposal from measured source,
retains unrelated nested context, rejects foreign approval, applies add/modify
through the merged service and preserves source, completed mutation record and
native checkpoint across API recreation. Its model output is a fixture and does
not establish live AI coding quality. Provider/iteration/admission work remains.

#114 is now merged into codex/source-mutation at 7d802481, but its reviewed head
0481329a is not reachable from the freshly fetched origin/main dc63ed2. Recovery
history/source inspection remains unavailable on main and is not imported here.

The stacked provider and durable dispatch milestones are documented in
[development-provider.md](development-provider.md) and
[development-dispatch.md](development-dispatch.md). Neither wires production
inference or completes Campaign A acceptance.
