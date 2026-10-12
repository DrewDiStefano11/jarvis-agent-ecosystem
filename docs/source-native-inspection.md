# Fenced native source inspection

The internal `MutationInspector.inspect` consumes the same exact persisted mutation request as the adapter. Original workspace actor, exact operator approval, lease token, worker, active runtime attempt, current permissions/policy and emergency-stop checks remain required. It supports completed, acknowledged mutations only. It introduces no route, model tool, shell/code execution, checkpoint writer, lease adoption or workspace creation.

Unlike recorded recovery history, native inspection reads the checkout. History remains available after stop; inspection refuses without live execution fencing. The inspector never calls the mutation adapter's apply/prepare/transition/acknowledge methods or the creation-capable namespace/lock helpers. It opens only existing directories and files, including the native target with `create=False`.

Native owner/claim device-inode identity, exact configured and actual Git observer identity, generated registration/branch/HEAD/base index and unchanged creation marker are verified. Existing source and metadata read pins plus namespace notifications span the bounded scan and final live authority/history checks. Retained originals must match the approved before digests and stages must be absent. Unknown or ambiguous artifacts are preserved and refuse; native absence probes accept only FILE_NOT_FOUND, never a swallowed access or long-path error.

The result contains the current whole-checkout CodeState, selected-source observation digest, immutable operation/plan/checkpoint references, an aware measurement time and a deterministic inspection fingerprint excluding time. `matches_completed_mutation` compares the measured CodeState and selected-source observation against recorded completion. Later ordinary source drift produces a false match without repair; unsafe topology, ownership, artifacts, metadata or lost authority safely refuse. No source text, private nonce, lease token or raw native/database errors are returned.

Every result reports `execution_authorized: false`. A measurement is ephemeral after native pins release and proves neither validation success nor future filesystem freshness. It cannot authorize resumption, cleanup, rollback, another batch, staging, commits or publication. No signed collector evidence or durable validation receipt is manufactured.

Windows and the existing approved fixed Git interface are required. Unsupported or overlong native Git paths safely refuse; tests use short isolated fixture roots rather than weakening the production checkout boundary. File/aggregate/count/node limits remain those of the approved creation and source contracts.

This increment explicitly depends on the protected history PR and mutation adapter. It changes no shared checkout driver, runtime, migration, registration or CI manifest. Remaining limitations: complete single-lineage operations only, current original lease/attempt/approval required, no stopped native read, and no artifact cleanup/chaining.

## Retained artifact boundary

Completed mutations retain one `.before` file for each change with a before identity, including deletes. Its exact sibling basename is `.jarvis-edit-` plus SHA-256 of `operation_id:index` plus `.before`; `index` is the immutable source plan change index. Additions have no retained original. The corresponding `.after` file must be absent after completion. Derive the exact bounded list from the protected complete record and original source plan, and verify original byte count/digest; never use glob exclusions.

The mutation adapter's namespace lock is under `.jarvis-owner-<workspaceId>/.jarvis-workspace.lock`, outside the source checkout. It does not create or allowlist a checkout-root `.jarvis-workspace.lock`. An unexpected root lock remains a protected foreign artifact and is preserved on refusal.

Retained-byte verification does not establish deletion or rollback authority. Original backup inode provenance is not a durable cleanup contract, and matching bytes alone must not authorize artifact removal. No reader or inspector grants cleanup, chaining or commit authority.
