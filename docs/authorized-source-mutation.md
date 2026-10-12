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
acknowledged by durable pending intent. Partial owned staging resumes only the
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
