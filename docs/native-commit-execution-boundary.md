# Native commit execution and recovery

Campaign C extends exact commit preparation with composed native execution,
durable recovery and typed HTTP contracts. Execution is disabled by default:
no trusted Campaign B commit-validation consumer is configured. The execution
route refuses with `GIT_COMMIT_VALIDATION_UNAVAILABLE` before effects.

## Immutable authority

`NativeCommitService` resolves acknowledged workspace creation, separately
approved preparation, complete applied-source history and the artifact manifest.
Its plan binds original source identities and strong hashes, original index inode
and bytes, compiled objects, candidate index, prospective commit/tree, pinned Git
executable, implementation policy and complete Git metadata snapshot. History
capacity is reserved before approval or effects.

The task-scoped `self_build.git.commit.create` permission is never self-assigned.
An independent authenticated operator approves the exact persisted plan hash.
Current worker, attempt, lease, inspection approval, workspace ownership, RBAC and
emergency-stop checks remain mandatory throughout execution. Model text grants no
authority. The trusted validation interface must return normally or raise;
boolean and asynchronous callbacks are rejected.

## Effects and recovery

`commit_runner` compiles bounded exact blobs, trees and commit bytes and a complete
deterministic Git index v2. Native Git independently verifies installed objects.
No caller-supplied Git command or hook is executed.

Append-only journal intent and a real runtime checkpoint precede acknowledgement.
Transactional outbox records accompany audit transitions. Stage inode claims are
durable before payload writes. Interrupted writes resume only when the retained
inode and payload prefix match exactly. Unknown files, foreign inodes and missing
claimed artifacts refuse and remain untouched. The index installer preserves the
original index and reconciles an interrupted owned rename gap.

Complete source and Git metadata pins span effects. Changes are limited to exact
generated object, index, feature-ref and reflog names and contents. Unrelated
metadata and the primary checkout HEAD/index remain immutable. The feature ref
compare-and-swap accepts the approved parent, or reconciles the already verified
exact commit after acknowledgement loss. It never force-updates a ref.

Final completion measures actual objects, index and ref before recording a stable
native runtime checkpoint. Replay verifies run, attempt, integrity digest,
cursor, state reference and source/plan metadata. A journal cursor alone cannot
establish completion. Lost acknowledgement recovers the existing exact checkpoint.

Recovery resolves protected history internally and recompiles original source
under pins; it does not replan against changed HEAD/index. Independent operator
renewal binds the measured recovery hash and retains original approval, source
and plan identity. Historical receipts grant no current execution authority.

## HTTP contracts

Workspace routes expose native-commit preview, execution, protected journal read
and recovery inspection. Authenticated remote-operator routes expose exact-plan
approval and recovery-bound renewal. OpenAPI defines the typed envelopes. Routes
delegate to services and contain no SQL.

## Campaign B interface

`NativeValidationStateReader` uses a trusted host command binding to measure
actual parent/committed HEAD and complete source hashes/modes under recovery pins.
Partial source selection is refused. It exports no capsule, executes no tests
and signs no receipt. Changing HEAD changes B's CodeState; parent-state receipts
cannot become committed-state publication receipts.

Campaign B owns independent isolated execution, protected collection and the
commit continuation contract. A production continuation consumer remains
unavailable. Fresh genuine committed-head evidence is separately required for
publication. Fixture gates prove developer integration only.

## Verification and limits

Tests exercise actual native Git, isolated SQLite/runtime checkpoints, original
index preservation, foreign-stage refusal, metadata fences, ref CAS, interrupted
index installation, approval renewal, stale workers and lost acknowledgements.
The composed recovery scenario passed within the unchanged 300-second per-test
timeout. Final checkpoint and parent/committed state-reader scenarios passed.
API tests verify the default missing-consumer refusal.

The earlier full backend baseline passed 2,993 tests, skipped three and had two
source-dependency failures. Both failures now pass in focused native regression
verification after integrating Campaign A's published fixture repair from PR
#141. No runtime invariant was weakened and the long full suite was not repeated.
This is not a fresh full-suite or final-acceptance claim. Frontend checks passed
with unchanged frontend source.
Hosted CI and independent review remain required before merge.

No main push, automatic merge, arbitrary shell, credential access, source replay
or cleanup of unowned artifacts is introduced.
