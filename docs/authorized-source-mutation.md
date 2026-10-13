# Authorized source mutation

This milestone explicitly depends on durable source planning (#103) and the
merged native checkout implementation (#99). It introduces a private
`SourceMutationService(app)` for one bounded editing lineage in an existing
Self-Build workspace. There is no HTTP/model mutation importer, shell execution,
code runner, autonomous worker, cloud fallback, Git commit, index update, or
automatic publication. Source proposals remain immutable, proposed and
unmeasured; a successful mutation receipt measures source bytes, not test results.

## Authority

`preview(actor, editing_record_id)` derives a deterministic `MutationPlan` from
the protected persisted editing proposal, current reservation, ready native
creation intent and its acknowledged checkpoint. The hash binds the editing
plan, repository/workspace ownership, original creation operation, original
runtime attempt and executor, and current implementation policy.

`approve(operator, ApproveSourceMutation(...))` requires the configured
authenticated remote-control operator, task access, native Git-inspection
permission and the additional `self_build.workspace.source_edit` permission.
The configured Git executable identity and SHA-256 must exactly match the tool
bound into the original creation plan; tool changes refuse before intent/effects.
The implementation actor and runtime agent cannot approve their own operation.
The operator approves the exact mutation-plan hash, not a model explanation or
the earlier workspace reservation/creation grant. Approval is an expiring
append-only audit/outbox event. The caller must provision that additional
permission through the existing identity/RBAC system; it is not implicitly
granted or enabled by this subsystem.

`apply(actor, ApplySourceMutation(...))` requires the original workspace actor,
original executor, original live task lease and token fingerprint, running
original active attempt, current policy and workspace version, ready creation
checkpoint, and current exact-plan operator approval. It rechecks those fences
before staging writes, original-file moves, destination publication and native
checkpoint commits. Emergency stop, abandonment, operator/worker permission loss,
expiry, successor leases or attempts all refuse continuation. A renewed exact
approval may resume the original lineage, retaining its immutable original
approval reference. An active lease cannot be adopted from another worker.

## File effects and containment

Only Windows native mutation is available. Existing generated workspace and
private owner/target-claim files must already exist and remain pinned; the editor
cannot create/adopt a workspace. Native directory handles prevent ancestor and
target replacement. The existing checkout verifier checks exact forward/reverse
Git pointers, generated branch, base and registration. Common Git metadata and
ownership/marker files remain pinned through publication. The creation marker
must exactly retain #99's approved read-only scope and original creation-plan
hash; altered markers refuse without repair.

Each selected directory receives a complete immediate sibling inventory. Paths
are canonical, bounded and checked for case aliases, symlinks, junctions,
reparse points, hardlinks and special files. The editor never creates missing
source parents. `.git`, workspace markers/locks and all `.jarvis-*` components
are unavailable as edit targets. Source contracts retain their stricter
credential/output/traversal protections and 64-file/64-KiB-per-file/1-MiB aggregate
budget. Whole-checkout reads also obey the original creation count/byte budgets;
projected final file count and aggregate bytes are checked before staging effects.

The raw approved base inventory is remeasured without checkout filters. Native
source hashes validate the original projected deltas, including unrelated files,
and the entire resulting `CodeState` must match the persisted expected state.
The adapter requires the dedicated index to match the approved base inventory;
foreign staged changes or unmerged index entries refuse before source effects.
It leaves Git metadata and the dedicated index unchanged. It preserves
inherited Git executable modes; Windows executable-bit changes and executable
new files refuse rather than claim an unenforceable mode mutation.

New content is written only into an exclusively created staging file. Its native
device/inode is durably checkpointed before content writes. Resumed staging must
retain that exact identity and an exact prefix of approved bytes. The staging
handle denies foreign writes/deletion; reopened publication handles recheck its
persisted identity and exact bytes. Original files are verified through a handle
that excludes foreign writes/deletion, then moved without replacement to their
operation-specific retained original. Publishing staged bytes is another native
handle rename without replacement. Adds cannot clobber a destination, and deletes
retain the verified original rather than irreversibly unlink it.

## Durable recovery

Alembic revision `20261012_source_mutation` follows the explicit planning revision
`20261012_source_plans`. `source_mutation_intents` stores one protected recovery
record per immutable proposal. A workspace admits only one mutation lineage in
this milestone; replay/recovery of that exact lineage remains available. A future
separately reviewed cleanup/chaining design must account for retained originals
before authorizing another batch. Different proposals cannot silently reuse its
staging or apply overlapping batches.

Preparation, per-file intent, staging identity, completed prefix and final
measurement each commit durable intent plus transactional audit/outbox before
advancing their native runtime checkpoint. No database transaction spans file
I/O. Runtime checkpoint integrity, cursor, metadata, attempt and state reference
must exactly acknowledge the corresponding durable record. A crash between
intent/checkpoint/acknowledgement is repaired by replaying the original identity,
not by inventing a new operation or trusting a model cursor.

On restart the editor verifies already completed files and retained originals.
For the pending file it accepts only the exact reviewed before state, its exact
retained original with an absent destination, or the exact reviewed after state
acknowledged by durable pending intent and proven to be the originally claimed
staging device/inode. An after-state without a claimed stage, or a foreign
same-content replacement of the published file, is refused and preserved.
Partial owned staging resumes only the
verified prefix. Unexpected destination/staging/original bytes or ownership
refuse and preserve artifacts. A crash after exclusive staging creation but
before durable identity acknowledgement leaves an ambiguous stage: safe refusal
and explicit operator recovery are required; it is never adopted automatically.
Power-loss durability beyond the operating system's acknowledged fsync/rename
semantics is not claimed.

Final whole-source and metadata pins and namespace notifications remain armed
through the native completion checkpoint and journal acknowledgement. A complete
replay remeasures and verifies the final source without rewriting it. Retained
originals and stages are not deleted by failure/recovery paths. Downgrade refuses
a populated mutation journal.

Events expose fixed operation/plan IDs, hashes, phase and bounded counters only.
Phase/acknowledgement events also identify the exact current approval used;
the journal retains the immutable original approval even after grant renewal.
Exact source remains in protected planning evidence. Lease tokens, private owner
nonces, source paths/content and raw native/database exception text do not enter
mutation events or public errors. The fixed `SOURCE_MUTATION_*` failure codes
distinguish authority, lineage, checkpoint, inventory, mode, ownership, conflict
and storage refusals without echoing secrets.

## Verification and remaining boundaries

Regression tests use disposable native Git repositories, dedicated temporary
control-plane databases and injected crash boundaries. They cover add/modify/
delete, exact replay, retained originals, staging-prefix refusal, same-content
foreign stage substitution, emoji/extended paths, final deny-write pins,
authority revocation, checkpoint gaps and transactional rollback/outbox parity.
No acceptance test edits configured production workspaces.

Mutation completion is not validation success. The persisted publication
validation plan must still receive independently measured validation evidence.
Human review and passing exact-head CI remain publication requirements. Native
source mutation and any later validation execution are separate authorities.

Preparation and application refuse unsupported native platforms before persisting mutation
intent or acknowledging a checkpoint. An unavailable execution platform cannot
consume the workspace's sole mutation lineage without performing an edit.

Journal progress transitions accept only mutable position fields. Original approval, operation identity, creation time and plan references remain immutable. Every journal read verifies the original independent operator approval against the append-only audit plan and task; expired original approvals remain valid provenance, while live execution requires a current exact-plan approval.
Published add/modify handles remain pinned against foreign writes and deletion
through per-file checkpoint acknowledgement. Recovery retains the handle whose
device/inode matches the claimed staging identity through that acknowledgement;
matching bytes never replace that ownership evidence. Final acknowledgement
continues to use the whole-tree read pins and namespace guard.

Per-file progress advances in the same journal transaction as its checkpoint
acknowledgement audit event. A crash before the runtime checkpoint or journal
acknowledgement retains the prior pending position and staging device/inode;
recovery must validate that identity before acknowledging the proposed progress.

The foundational mutation driver also includes the safety checks reviewed in
PR #122: deterministic publication stages must be absent for completed entries,
pending published after-states, deletes, and final completion under the namespace
watch. Unclaimed stages are preserved and refused, never hidden by measurements.
PR #122 retains additional adversarial regression coverage and documentation.

Modify/delete originals retain deny-write/delete read pins from their move into
the deterministic backup through file acknowledgement and final completion.
The rename-capable handle transfers to a read-only pin only after rechecking the
same device/inode and approved original bytes; replacement during that transfer
refuses before further publication or acknowledgement. Recovery pins and verifies
existing originals before accepting pending or completed positions. File and final
acknowledgement guards reverify those handles, bytes and exact backup paths.

The original device/inode for every modify/delete is recorded in the pending
journal position and native checkpoint while the original handle remains pinned,
before its rename. Recovery requires that durable identity for retained originals
and pending destinations; identical bytes in a foreign inode do not establish
ownership. Claims remain immutable through acknowledgement and completion and
grant no cleanup authority. Empty claims preserve legacy serialized records and
their hashes, but legacy pending originals without a claim refuse recovery.

Finalization checks both publication-stage absence and the absence of a retained
original for every add after arming the namespace watch. Foreign add backups
cannot be excluded from measurement and then acknowledged as completed.

Mutation also coordinates with approved workspace reads through the canonical
checkout-root `.jarvis-workspace.lock`. Only this exact root metadata path is
excluded from source observations; planned edits, nested locks, aliases and other
private artifacts remain prohibited. The adapter initializes/reuses the shared
lock, validates its single canonical byte, and retains a deny-write/delete pin
plus the byte lock through completion. Busy or malformed locks refuse before
file intent. This coordination file grants no source or cleanup authority.

Before a new mutation intent is persisted, a read-only native preflight verifies
the existing owner/target claim, creation marker, Git registration, exact staged
inventory and default `H` index flags. Metadata read pins remain held through the
intent transaction, preventing index replacement in the admission gap. Foreign
`assume-unchanged` and `skip-worktree` flags refuse without consuming a lineage;
clearing them permits the same approved request. The adapter rechecks flags before
fresh or recovered file effects. Preflight creates no marker, lock or workspace,
and its native reads finish before the database write transaction begins.
Read-only pins can coexist with whole-tree completion pins while preserving their
deny-write/delete guarantees. Pins release after the operation returns; this does
not grant cleanup authority or claim permanent native artifact ownership.

Whole-source measurement and final source pinning reject reserved directory
components case-insensitively, including empty `.jarvis-*` directories below
unselected source directories. Namespace absence cannot be inferred from file
bytes alone. Authorized root marker files and private ownership directories
outside the checkout retain their existing verification paths; ordinary source
directories remain valid. Late insertion after final pinning is still covered by
the namespace watcher, and refused directories are preserved without cleanup.
