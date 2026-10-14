# Campaign C handoff — 2026-10-14

Campaign C is unfinished. The goal remains the complete authorized source-to-PR
workflow, including corrections and real authenticated acceptance. Published
developer PRs and fixture tests are not that acceptance.

## Saved review increments

All implementation changes are pushed. The latest implementation head before
this documentation update is `0fc3ed116f933da4f58cc3312865578e1743a3df`.

| Draft PR | Scope | Integration relationship |
| --- | --- | --- |
| #126 | Bounded exact-head read-only GitHub observation | Existing dependency |
| #129 | Bounded Git wire protocol | Existing dependency |
| #140 | Guarded HTTPS push and draft PR reconciliation | Transport increment |
| #143 | Native exact commit execution/checkpoints/recovery | Native increment |
| #144 | Protected publication orchestration, APIs, source link | Deliberate dependency integration |
| #146 | Bounded read-only correction triage | Targets #144's branch |
| #147 | Trusted profile replacement revocation | Targets #146's branch |
| #149 | Fresh provider evidence before current readiness | Targets #147's branch |

The reviewed integration destination remains main. No automatic merge, main push,
force push or branch deletion has occurred. Main was last verified at `5e11d60`,
which includes source dependency #119. Source-history fixture repair #141 was
integrated explicitly; no source-runtime invariant was weakened.

## Evidence and its limits

- Actual native Git and isolated runtime/SQLite tests verified exact object/index/
  ref effects, preserved original evidence, protected checkpoints, interrupted
  index installation, approval renewal and acknowledgement-loss recovery.
- Actual native/runtime publication composition recovered a lost PR creation
  response with one push and one PR POST. Provider and B interfaces in that test
  were explicitly fixtures; no genuine protected test receipt was produced.
- The historical commit/source/observation bridge passed actual native/runtime
  integration. Its later explicit provenance fields also passed deterministic
  interface/refusal tests. It grants no freshness, mutation or validation authority.
- The prior full backend baseline had 2,993 passes, three skips and two source
  regressions. Both regressions now pass in focused native verification after
  A's exact #141 fixture repair. The long full suite was not repeated.
- Focused triage tests passed 16 cases; deployment/policy/lifecycle/consumer tests
  passed 34 cases; current readiness/reader tests passed 15 cases. Ruff passed.
  Frontend source is unchanged and prior frontend gates passed.
- Current readiness uses a new bounded provider collection with native pins,
  current profile/task authority, protected checkpoint identity and fresh B
  assessment. GET reporting does not alter audit history or run workflows.
- Hosted CI remains unverified for the latest heads. Skip-ci commits preserve
  the user's cost constraint; required checks and independent review are not
  waived. These branches are not declared merge-ready.

## Remaining C implementation

1. Complete publication across correction workspaces. Initial preview currently
   derives the destination branch from the native workspace and sets
   `expected_remote_sha=None`. It does not yet bind a new corrected commit to the
   original published branch/PR through protected correction lineage.
2. Preserve fast-forward history. A's original-base correction path in draft
   #148 is published but unmerged; a sibling commit based on the original base
   cannot be forced over a published commit. Advanced-head admission must be
   independently approved and proved before C publishes a child on the original
   remote branch. Do not reparent an approved native plan or use CLI rebasing as
   a substitute for that proof.
3. Separate source-validation base from approved GitHub PR base when advanced-head
   correction is supported. The current consumer compares B's CodeState base to
   `approved_base_sha`; the fresh source workspace may legitimately start at the
   previous published commit while the PR still targets the original main base.
   Preserve both identities explicitly rather than weakening either check.
4. Add durable bounded correction requests and consume A's authoritative admission
   interface. Current triage is read-only and always grants no dispatch authority.
   Preserve original task/admission scope, deadline, budget reservations, source
   plan approvals, operator independence and historical evidence.
5. Provide validation-evidence renewal for a long-running publication/review
   lifecycle. Original publication plans seal B journal/plan/state/digest identity.
   New evidence must not silently replace that identity or cause an already
   published branch/PR to be adopted without protected prior publication proof.
6. Verify retry/restart and changed-head behavior through those complete correction
   and renewal paths, then expose truthful correction-attempt lifecycle records.

## External acceptance dependencies

Campaign A owns source capture/rehydration, correction admission/effects and root
budget continuity. Published #145/#148 code is not merged/main authority. Its
advanced-head bridge and protected consumer contract remain integration work.
Consume protected readers; do not import HTTP/model history payloads as authority.

Campaign B owns independently verified isolation, protected guest measurement,
receipt collection and native commit continuation. No genuine receipt production
or P-to-C continuation is configured. State readers, ordinary subprocesses,
fixtures and host counters cannot replace that evidence.

Real acceptance also needs trusted credential configuration and verified hosted-CI
cost policy. Keep default native execution/publication unavailable until genuine
dependencies exist. Required exact-head CI and independent review must pass before
human integration; the operator alone decides whether to merge.

The goal must not be marked complete from this handoff, draft PR presence or
passing fixture tests. Continue independent implementation without repeated CI
polling or duplicate full-suite testing, and push reviewable increments.
