# Expected source artifact contracts

`plan_expected_artifacts(completed_record, original_editing_plan)` is a pure contract builder. It validates the immutable mutation/editing hashes and lineage, original source plan/projection, complete prefix, final selected-source observation and exact checkpoint reference. It returns a closed expected artifact list and deterministic metadata fingerprint. It reads no workspace files and invokes no native commands, checkpoint writers or cleanup operations.

`ArtifactManifestReader(app).read(actor, editing_record_id)` derives the same list from existing protected history and planning reads. It requires task-read/workspace permission and validated creation/mutation checkpoint provenance, and remains available after emergency stop without granting live execution authority. History and expected artifacts remain separate from current native observations.

Each immutable change index contributes exactly two expected siblings from `staging_names(operation_id,index)`. For modify/delete, `.before` must retain the original byte count/digest/mode and `.after` must be absent. For add, both `.before` and `.after` must be absent. Descriptors bind the source path, exact derived artifact path and presence/identity expectation. A derived hashed sibling may be longer than a short source basename; its canonical source parent and fixed ASCII basename remain bounded. No glob, arbitrary prefix exclusion or unknown private artifact is permitted.

Bounds are 128 descriptors, the source contract's one MiB retained-original byte budget, and 512 KiB serialized metadata. `verify_expected_artifacts` reconstructs the complete manifest and rejects even coherently rehashed descriptor/reference substitutions. The manifest preserves original operation, mutation intent/plan, editing record/plan, approval and completion checkpoint references without source text, lease token or private ownership nonce.

All manifests explicitly report `native_artifacts_verified: false`, `execution_authorized: false`, `deletion_authorized: false`, and `checkout_root_lock_allowed: false`. A manifest describes expected files, not their current existence, byte identity, inode provenance or ownership. It is never a model-authored grant or native evidence. A trusted consumer must re-read protected provenance and independently verify the exact expected native files under owned/pinned workspace boundaries before using this allowlist. Matching bytes alone do not authorize deletion, rollback or adoption of an ambiguous artifact.

The source adapter retains its private ownership lock and coordinates with approved reads through the canonical checkout-root `.jarvis-workspace.lock`. Trusted inspections may independently validate and pin an existing root lock containing exactly `b"0"` as coordination metadata. This manifest grants no permission to create or acquire that lock. Malformed, nested or aliased locks, unknown `.before`/`.after` files and private directories remain foreign and must be preserved on refusal. This contract does not authorize Git staging/commit, publication, another editing batch, validation execution or artifact cleanup.

This increment explicitly depends on the protected history/mutation PR ancestry. It modifies no shared checkout/validation/publication modules, migrations, runtime orchestration or CI manifest. Native consumers may use it only after merge or by declaring their own explicit dependency. Current mutation/editing policies and preserved original lineage remain required; unsupported legacy-policy recovery safely refuses.

The protected artifact reader reports `SOURCE_ARTIFACTS_STORAGE_FAILED` for database
failures, including failures already translated by the nested history or editing
plan reader. Other domain errors, such as authorization or lineage refusals, retain
their original codes. Storage errors suppress private database details.

The protected reader requires acknowledged native completion before deriving a
manifest. A coherently changed mutable record cannot turn a native-only completion
crash gap into a completed artifact reference; missing durable acknowledgement
refuses with `SOURCE_ARTIFACTS_POSITION_INVALID`. Incomplete intents retain
`SOURCE_ARTIFACTS_INCOMPLETE`.
