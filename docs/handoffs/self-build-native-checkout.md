# Approved native checkout creation - October 9, 2026

Worktree: C:/Users/DDistefano/.codex/worktrees/self-build-native-checkout/jarvis-agent-ecosystem.
Branch: codex/self-build-native-checkout. Parent: #93 at 5172bc1, stacked on #94
reconciliation at a16bc13. #91 was human-merged into a feature branch rather than
main; #94 must reach main before #93 is retargeted there. No agent merges PRs.

## Implemented production path

Application state and SQL-free routes expose immutable creation preview, separate
HTTPS bearer operator approval, runtime-actor creation and historical readback.
Self-Build remains disabled by default. Both principals need the existing task
RBAC permission self_build.workspace.materialize. Exact approved plan hash,
original worker/lease/native attempt, current operator lifecycle/expiry, measured
inspection/current implementation policy and emergency stop fence every phase.
Fresh exact-plan approval can resume while retaining original operation/approval
and intent evidence; acknowledgement events record the current authorization ID.

The private driver creates only the generated codex/ branch and owned worktree
at the approved exact base, using fixed native Git argv, pinned real executable,
bounded outputs/inactivity supervision and native descendant cleanup. Primary
checkout files/index remain unchanged. No force, removal, branch replacement,
network, filters/hooks or arbitrary user-selected command/path is exposed.
Registration verifies dedicated Git pointers, common/admin directory identities,
reverse pointer, branch/base and native worktree porcelain. Uncertain/foreign
artifacts remain intact; an empty target preceding unacknowledged Git effects
requires explicit recovery rather than guessed adoption/removal.

Raw blobs are checked against the approved Git inventory/object identities and
8 MiB file/64 MiB total/4096-file budgets. Pinned directory handles and exclusive
atomic non-replacing moves publish verified staging bytes. Partial owned stages
resume only an exact expected prefix. Existing conflicting files are preserved.
Only the dedicated index is initialized/read back. Final physical inventory,
source bytes, marker, index and registration are remeasured before finalization.

Prepared, registration and each monotonic file prefix persist before their native
runtime checkpoint acknowledgement. A finalizing projection binds the canonical
ready digest; ready is published only after the complete-source checkpoint is
verified. Crash gaps preserve operation/nonce/digest and deduplicate native
checkpoints. All audit events enter the transactional outbox; private nonce and
lease tokens are excluded. Historical #93 prepared schema remains readable using
its original serialized digests; changed policy cannot silently resume it.

## Platform confinement and validation

Mutation is Windows-only. Other platforms reject it before effects; Linux native
inspection and process coverage remain required. This choice reflects Windows
namespace handles that deny replacement; Linux directory FDs alone do not provide
write confinement. The object store remains immutable during every fixed command:
existing object files are deny-write/delete pinned, and a native subtree name watch
rejects transient objects/alternates even after timestamp restoration. Reinspection
receives the same live authority callback. This is native Git confinement, not a
sandbox for development code; arbitrary code/test execution remains unavailable.

Latest parent preparation suite: 18 passed. Native creation/recovery suite: 25
passed, plus empty-base creation and contract checks: 9 passed. Driver/raw
materialization including object-store transience: 24 passed. Earlier combined
creation/contracts/driver/materialization/filesystem check: 102 passed with one
existing filesystem environment skip; no new skip or weakened assertion was added.
Three real filter-exclusion/partial-stage cases also pass. Ruff/format and frontend
typecheck/ESLint/194 Vitest cases/build pass. CI shard collection and exact-head
hosted CI/review are required before declaring this increment merge-ready.

A private owned draft snapshot remains recoverable at Git stash
2bb1255b3779dfabfcf3cc7f8784b02ed4b2b15a; it was reapplied after the parent update.
The test insertion conflict was resolved preserving both native acceptance and
reviewed renewed-approval regressions. Ignored backups/dependencies/build outputs
are never committed; unrelated worktrees and primary validation-pr63 are untouched.

Next capability: separately approved bounded source edits with protected paths,
expected content hashes, durable operation journals and native checkpoint recovery.
Then actual confined development execution, Git/GitHub publication, source-bound
validation/review repair, Mission Control and a useful production Jarvis self-build
plus improvement audit. The software-factory campaign remains incomplete.


## Final publication repair (2026-10-10)

P1 4235841586: final source, marker, worktree pointer, common Git metadata/index
and private owner files remain deny-write/delete pinned through native ready
checkpoint and workspace projection commit. Recursive source/metadata name watches
remain armed; their callbacks run inside the existing commit transaction while
that transaction revalidates authority, avoiding nested SQLite writers. Recovery
of an unacknowledged finalizing state rechecks physical bytes before adopting an
existing native ready checkpoint. A tampered crash-gap source remains finalizing.

P2 4235841589: sized inventory allowance includes a derived 32-byte-per-approved-file
margin above the original 1 MiB inventory bound. A real 4,096-file Git tree below
the unsized bound but above the former sized bound passes; count/byte/hash limits
remain unchanged. Fixed mutation commands also receive the same 512 MiB Windows
process/job memory limit as the parent inspection repair before initial resume.

Current driver/materialization/process/creation regression set: 61 passed. New
publication tests prove writes to all five final artifact categories are denied
inside native ready publication and later released. Ruff/format and frontend
typecheck/lint/194 tests/build pass. A broader full-backend run is still in flight
and must be reported honestly; it began before the final memory parent changes.
Integrate the final #93/#94 parent heads before requesting exact-head CI/review.


## Ownership, directory and capacity repair (2026-10-12)

P2 4238334869: final source verification now derives the exact parent-directory
set from approved Git paths and rejects unapproved empty directories, preserving
those contents for operator recovery. File and directory equality both gate ready.
P2 4238334875: the approved plan reserves 32 Git metadata nodes, with an initial
ceiling of 16,352 and final ceiling of 16,384. Driver preflight rejects insufficient
capacity before target, branch or registration creation. Actual near-boundary
fixtures cover acceptance/materialization/replay and rejection before target effects.

The #93 marker-replay repair is integrated. Both registration and materialization
provide native validation of existing targets before namespace replay yields them:
exact generated branch/base, Git forward/reverse administrative pointers and unique
registration; marker text alone never grants target ownership. Legacy prepared
readback coverage removes all later plan fields and preserves original raw hashes.

Hosted autonomy CI exposed an invalid test fixture: core async service tests and
a TestClient lifespan dispatcher shared a broker across different event loops.
The core fixture now creates the already-migrated app without a parallel HTTP
lifespan; HTTP integration keeps its own TestClient lifecycle. All 34 remote
authentication/goal/HTTP cases pass with all cancellation/lease/audit assertions
preserved. Production broker code is unchanged. Local older-head full pytest
completed: 2,143 passed, two existing skips, one outdated source-size assertion
that was already corrected and passes in final publication regressions. This
older run is not final-head validation. Request current exact-head CI/review after
these concrete repairs, and do not merge.


Final ownership/directory/capacity native set: 56 passed (650.59 seconds, including
two actual 16K-node fixtures); remote integration set: 34 passed; strengthened
legacy-plan readback: one passed. The new large bounded metadata fixture has a
600-second per-case hang guard for Windows CI; production limits and existing
test deadlines are unchanged. Full shard collection verifies 2,153 cases. Ruff
and formatting pass; frontend source is unchanged from passing typecheck/lint/194
Vitest/build gates. Independent exact-head review/CI remains required.

## Recovery integration with reviewed #93

The atomic target claim from #93 is retained through Git registration and raw
materialization. Registration uses the explicit fresh-claim callback rather than
target absence. Metadata headroom and component-by-component Windows inventory
collisions are checked before claiming the target. Existing targets still require
complete branch/base/pointer/registration verification; incomplete registration
remains uncertain and preserves artifacts for operator recovery.

The root's deny-write/delete pin prevents cross-directory MoveFileEx acknowledgement.
Verified staging now uses reserved `.jarvis-stage-<content digest>` names in the
destination's pinned parent, followed by native same-parent FileRenameInformation
with replacement disabled. Staging handles deny concurrent writes and deletion;
partial stages resume only an exact approved prefix. The approved inventory cannot
contain these reserved names, and final readback rejects residual staging files.
No sharing permission was widened. Runtime authority, checkpoints, publication
pins and original approval/lease/attempt lineage remain mandatory.

Disabled remote operator service returns `SELF_BUILD_APPROVAL_UNAVAILABLE` before
creation intent, checkpoint or native effects, including persisted approvals.
