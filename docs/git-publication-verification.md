# Trusted development change verification

The publication campaign builds on merged PRs #91 (pinned repository reads),
#94 (inspection reconciliation), and #99 (native owned checkout). The new private
`ChangeVerifier` measures changes in a ready generated workspace. It has no route,
model tool, staging, commit, push, PR, or network capability. A future native
service must separately authorize its exact plan before exposing it.

Its caller supplies the validated durable creation record and private nonce,
checks the acknowledged runtime checkpoint, and provides a live ownership,
attempt, lease, policy, permission and emergency-stop fence. Markers and model
text cannot substitute for that service authorization. The verifier independently
checks the original creation-plan hash, pinned tool identity, registered policy,
private owner, target inode claim, exact forward/reverse Git pointers and native
generated-branch registration. Missing namespaces are refused, never created.

The initial verifier requires HEAD to equal the immutable original base. A moved
branch, detached HEAD, main branch or foreign registration is refused. Later
commit recovery must explicitly establish any permitted new HEAD; this increment
does not infer that a branch move is authorized.

Observation uses fixed native `ls-tree`, `cat-file`, `ls-files --stage` and
registration reads already provided by the native checkout driver. It reads raw
Git blobs and filesystem bytes without status hooks, clean/smudge filters, shell,
external diff helpers, config aliases or network transport. Ignored files are
included through the complete filesystem walk; Git ignore configuration cannot
conceal changes. Existing protected-path rules also apply to deleted original
files and index entries. Private `.jarvis-*` paths cannot enter source evidence.
Only the exact verified root `.git` pointer and workspace marker are excluded.

Windows deny-write/delete file pins and directory identity handles protect every
source and metadata file. Namespace notifications detect new or missing names.
Ownership markers remain pinned through the final callback, which allows a
future service to publish evidence under its transaction fence while those pins
remain held. Callback failure publishes no successful return. Existing ownership
locking is reused; the verifier cannot adopt foreign directories or repair state.
Other platforms refuse this inspection until equivalent containment is implemented.

The original tree, dedicated index and current files are compared independently.
Complete original, current and index inventories remain inspectable, including
staged entries whose files are absent.
Added, modified and deleted paths carry before/after blob SHA, SHA-256, byte count
and mode, plus tracked identity. Dirty index state is reported without repairing
or staging it. Unmerged, truncated, sparse, assume-unchanged or unsupported index
entries are refused.
Windows file modes come from the Git index for existing entries and use `100644`
for additions; `mode_source=git_index_windows` states this limitation explicitly.

Case-insensitive checks cover every path component across all three inventories,
including aliased parent directories and file/directory collisions. Symlinks,
reparse points, hard links, device/traversal paths, credentials, runtime databases
and unsupported Git modes are refused. Source bounds remain 4,096 files,
8 MiB per file, 64 MiB total and 16 path components; directory/metadata walks and
native stdout/stderr retain explicit limits. Evidence contains no absolute root,
lease token, raw remote URL, command stderr, private nonce or source bytes.

`GitChangeObservation` verifies deterministic change and commit-preview
fingerprints against its complete measured payload. The preview is a fingerprint
of prospective changed-file selection and parent, not a created Git commit/tree
or operator approval. Empty-directory changes do not affect a Git commit;
their paths are still inspected for protection and collisions.

Validation uses isolated temporary repositories and runtime databases. Regression
coverage includes ignored/untracked additions, modifications, deletions, index
differences, hostile filters, foreign branches/markers, missing workspaces,
protected paths, hard links, pinned callback writes, malformed index records,
component collisions and tampered evidence. The local audit compares exact
before/after SHA-256 values against fixture bytes and the after blob SHA against
native `git hash-object --no-filters`, independently of the verifier's hashing
routine. Manual boundary review added the pinned registration recheck, complete
index inventory, protected-path validation before byte reads, index flag refusal
and Unicode/short-name alias checks. Codex review is not requested because its
quota is exhausted; a separate reviewer has not audited this increment. The main remaining
integration risk is the separately authorized native service and exact source-edit
lineage, deliberately outside this first private verification increment.
