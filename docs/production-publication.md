# Production publication orchestration

Campaign C composes native exact commits, guarded authenticated HTTPS push, draft
PR reconciliation, protected publication checkpoints and exact-head observation.
The default application initializes native commit services and exposes typed
contracts but installs no validation consumer, credential target or publication
profile. Unconfigured publication APIs return `GITHUB_PUBLICATION_UNCONFIGURED`.
Native execution separately refuses a missing trusted commit-validation consumer.

## Trusted host configuration

`github_publication.deployment.configure` accepts a fixed approved repository/base,
known publisher identities, optional Windows credential target, Campaign B's
protected validation service and verified-boundary guard, a hosted-CI cost guard
and a native commit continuation consumer. These are trusted host adapters, not
model inputs or enrollment APIs. Configuration installs no key, runner or VM and
does not enable GitHub workflows. Missing evidence or guards fail closed.

Replacing the trusted profile revokes captured publication/native consumer
references. Current profile identity is checked before and after validation and
at publication authority/approval boundaries. Omitted native consumers are cleared;
they are not inherited from an earlier profile. Invalid replacement inputs leave
the prior profile intact. An already attempted remote effect is reconciled through
the protected journal by the current authorized profile, never blindly repeated.

Publication requires the unassigned `self_build.github.publish` task permission,
an independent authenticated operator's exact-plan approval, current runtime
attempt/worker/lease, workspace ownership and emergency-stop clearance. The plan
binds protected native/source identities, repository/base/branch, committed SHA,
validation journal/plan/state digests, task metadata, publisher identities and the
complete producer implementation digest. A changed implementation invalidates
the approved policy. Approval expires; recovery cannot grant authority itself.

The optional `source_base_sha` identifies the source-validation/native parent
separately from `approved_base_sha`, the approved GitHub PR base. Preview derives
the source base from protected native history; a supplied value must match the
native parent and is included in the plan hash. Legacy plans omit the field and
retain their original serialized shape/hash. B assessment and the source-link
reader compare source evidence to the effective source base. This does not enable
advanced-head correction publication: protected original branch/PR lineage and
A's independent admission/approval remain required integration work.

`correction_link.read_correction_link` now resolves a protected historical direct-
child reference: original source/publication link, child native history and final
checkpoint, same task/actor/repository, complete source selection, and unchanged
original observation. It preserves the original remote branch/PR while identifying
the child's distinct local branch and source base. Siblings rooted at the original
main base are rejected. `publication_authority` and `lineage_admission_verified`
remain false; the reader does not dispatch, approve or publish a correction.

## Durable phases

The operator-facing state contract provides `preparing`, `committing`, `pushing`,
`pr_created`, `validation_pending`, `review_pending`, `blocked` and `completed`.
Native status establishes the actual commit position. Publication snapshots are
historical receipts; the status endpoint rechecks native position, observation age
and genuine B evidence before reporting current manual-merge readiness.

The append-only publication ledger uses existing audit records, transactional
outbox events and actual runtime checkpoints. Stable operation identities bind
task/workspace/command. Contiguous digest-linked entries seal push and PR request
digests before their network effects. Acknowledgement validates checkpoint run,
attempt, digest, cursor, state reference and metadata. An audit row alone is not
an acknowledged effect. Original source/metadata pins remain held during effects.

Each advancement is bounded. Before a push, remote API and Git advertisement
must agree on the approved previous ref. Only one exact generated feature ref is
sent, without force. A remote ref already at the exact commit is adopted only
when protected acknowledged write intent proves this operation attempted it.
Fresh remote state confirms publication after the response.

Draft PR creation uses a stable task/workspace/repository/branch marker and exact
metadata digest. Existing matching PRs are independently fetched and reconciled
before creation or update. Foreign, ambiguous, closed, merged, non-draft or
changed-head PRs refuse. Lost creation acknowledgement recovers the known PR;
metadata updates are restricted to title/body on that exact owned PR.

Rate-limit failures persist bounded retry deadlines. No retry occurs before the
deadline, and there is no internal polling loop. Authentication, stale base,
foreign state, authority changes and incomplete validation produce truthful
blocked/pending results. No new source plan is inferred from a changed remote.

## Observation and evidence

One explicit observation request uses the existing bounded read-only collector
for actual checks, protection rules, reviews and findings. It verifies the head
before and after collection and preserves evidence identity. Queued, running,
failed, cancelled, skipped and stale checks cannot satisfy required checks.
Missing independent reviews and active findings prevent readiness. Passing CI
cannot replace Campaign B's independently protected committed-head receipts.

`completed` describes publication policy readiness for human review/merge, not a
merge operation. Current status rejects observations older than five minutes,
future timestamps, changed native HEAD and invalidated validation. Current manual-merge
readiness also requires a new bounded provider collection under retained native
source/metadata pins, current profile/task authority and acknowledged journal
identity. A changed remote head, newly failed check or new review request clears
readiness even while an older passing observation remains recent. Provider retry
deadlines and credential/profile errors are reported without sleeping or polling.
The status read performs no audit mutation, workflow rerun, source dispatch or
merge. Fresh genuine B validation is checked again after collection. Automatic merge
is always false. The system neither pushes main nor requests billable workflows.

## API and integration boundaries

Workspace APIs expose publication preview, advancement, historical receipt,
single observation and current status. Remote-operator APIs approve exact plans.
OpenAPI is authoritative. Routes delegate to services with explicit transactions;
there is no route SQL or separate frontend domain state.

Campaign A retains correction capture/rehydration and source editing ownership.
The protected source-link reader resolves an acknowledged observation and native
final checkpoint internally. It returns the immutable parent CodeState/hash,
applied-source mutation/proof identities, exact parent/tree/commit, checkpoint
references and observation digest. A must match its separately captured precommit
baseline; the link is historical and grants no current-source, mutation or
validation authority. Changed observations or missing checkpoints refuse.
Correction dispatch awaits A's supported development contract; no competing
editing engine or automatic correction authority is introduced here.
The protected correction-triage GET classifies the linked observation into
waiting checks, investigation, current review correction, no actionable evidence
or blocked. Unknown check failures are not fabricated product defects or retries.
Old unresolved reviews, ambiguous evidence, stale observations and unverified
publishers remain explicit blockers. More than 32 references blocks the candidate
and preserves the original full observation, rather than silently truncating it.
The result binds task/head/link/observation hashes and grants no dispatch or
mutation authority. A must separately enforce original task scope, budget,
independent workspace/lineage approval and new exact source plans.
Campaign B retains isolation, protected collection and post-commit continuation ownership.
No mocked evidence, ordinary subprocess test execution or state-reader assertion
can activate production publication.

## Verification and remaining acceptance

Developer tests use actual native Git and isolated runtime/SQLite checkpoints
with explicitly fixture-only remote and validation interfaces. They demonstrate
lost PR acknowledgement recovery without a second push or PR POST. Deterministic
failure tests cover transport authentication/revocation/rate limits, remote CAS,
PR reconciliation, ledger/checkpoint failures, stale-head CI, absent guards and
current status. These fixtures never constitute real authenticated acceptance.

Real acceptance requires Campaign B's independently verified runner and receipts,
supported native continuation, configured credentials and verified hosted-CI cost
policy. It also requires real exact-head CI and independent review. Source
dependency baseline test failures are documented in the native milestone; they
now pass after A's exact published fixture repair, without runtime changes or a
duplicate long full-suite run. No main push or automatic merge is allowed.
