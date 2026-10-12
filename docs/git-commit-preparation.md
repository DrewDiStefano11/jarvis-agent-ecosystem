# Exact prospective commits

This increment depends explicitly on PR #107's owned change verifier. It adds a
pure `propose_commit` foundation for milestone 2. It does not yet provide an
authorized durable preparation service, checkpoint writer or native commit
executor. `CommitPreparationPlan.state` is always `proposed`, and
`commit_creation_supported` is always false. There is no execution endpoint or
model tool. Plan hashes and provenance references cannot grant authority.

Inputs are the complete measured Git observation, original source observation,
immutable source-change plan, source record/checkpoint references, exact file
selection, and explicit author/committer/message metadata. All contracts are
revalidated against serialized values to reject tampered `model_copy` instances.
The merged source planner rederives its original source plan, policy, preview,
fingerprints and original-plan/authorization references. Its repository/base/HEAD
must match the native Git observation. Every planned before/after identity must
match measured native SHA-256, byte count, presence and Git mode. A partially
applied source plan cannot be concealed by selecting only its successful files.

Only changed paths in both the verified observation and source plan can be
selected. Selection is deterministic, bounded to 64 files, protected against
case/component aliases, and exact. Other measured changes remain unselected;
their complete observation still contributes to the approval plan hash so a later
change invalidates the proposed authorization. Dirty or ambiguous indexes cannot
be used. HEAD must remain the original generated development branch/base.

The planner independently reconstructs the original Git tree from the complete
inventory and refuses a tree SHA mismatch. It applies only the chosen deltas in
memory, preserves all unselected original entries, and serializes actual Git tree
objects with byte ordering that treats directories as names ending in `/`.
Prospective tree and commit SHA are real Git object identities, cross-checked
against native Git in isolated tests. The exact commit payload also has SHA-256.
Computing those values creates no object or ref and does not stage any file.

Commit metadata is explicit and contributes to the plan hash. Names/emails cannot
inject Git headers; timestamps and timezone offsets are bounded. Messages retain
exact UTF-8 bytes, require a final LF, reject NUL/CR, and have an 8 KiB limit. No
identity, signing, hooks, filters, credential helper or configuration is read from
Git. The plan retains original ownership, tool and verification/preparation policy
digests and immutable source references without persisting raw source content.
Returned JSON is bounded to 64 KiB. Changed metadata changes the prospective
commit SHA and exact plan hash while preserving the selected tree.

The approval/request contracts name an exact plan hash, separate approval ID,
original worker and private lease token. They deliberately have no authority
implementation yet. A subsequent native integration must resolve source record
and checkpoint references from authoritative merged interfaces, independently
verify their immutable provenance, and recheck active actor/run/attempt/lease,
registered workspace, operator approval, RBAC, emergency stop and pinned tool.
It must append durable intent through the transactional audit/outbox and obtain
the existing runtime checkpoint before any Git effects. Uncertain effects may
never be replayed blindly. Creation remains unavailable until that full boundary
is verified.

The source-mutation campaign has been notified, with explicit operator approval,
of the source-record/checkpoint dependency and marker compatibility boundary.
No unmerged source-mutation implementation is copied or imported. No database
migration is introduced before shared schema coordination.

Local independent checks compare computed trees (including empty trees, binary
blobs, executable modes, nested directories and ordering edge cases) and commits
with native Git. Tests verify that planning leaves the index, refs and source
bytes unchanged, and reject malformed selections, altered original lineage,
coherently rehashed stale policy/tree/source evidence and injected metadata.
Remaining review risk is native durable authorization and recovery integration;
this pure increment does not claim those capabilities are implemented.
