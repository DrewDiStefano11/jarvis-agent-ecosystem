# Approved native checkout creation - October 9, 2026

Worktree: C:/Users/DDistefano/.codex/worktrees/self-build-native-checkout/jarvis-agent-ecosystem.
Branch: codex/self-build-native-checkout. Parent: #93 at 9b4b923, stacked on #94
reconciliation at 087840a. #91 was human-merged into a feature branch rather than
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
atomic non-replacing moves publish verified staging bytes. Partial private stages
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
