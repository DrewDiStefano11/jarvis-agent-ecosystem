# Adversarial coverage for source publication-stage refusal

This safety increment depends explicitly on PR #112 and retains its ancestry. It
does not depend on the later history, inspection or expected-artifact contracts.
The core stage-absence guards and pending/completed/deletion regressions are now
backported to that foundation. This branch retains the additional native-error,
final pin-boundary, late namespace-insertion and real service recovery coverage,
without duplicating production changes in its parent-relative diff.

An isolated regression demonstrated that a foreign regular file at the exact
deterministic `.jarvis-edit-<hash>.after` name could be ignored during recovery.
The pending published-file fast path and already acknowledged prefix path checked
the destination and retained original, while excluding that stage name from source
measurements. A namespace watch armed afterward could not identify a preexisting
artifact. Both interrupted-publication and completed-replay repros failed before
the fix because no refusal occurred.

The adapter now requires native publication-stage absence for acknowledged prefix
files and before the published-file fast path succeeds. Delete operations require
absence before their original is moved, since they never create a publication
stage. After all source effects, every deterministic publication-stage path is
rechecked under final source pins and the armed namespace watch before completion.
Later insertion remains covered by the existing completion checkpoint guard.

Only native file-not-found establishes absence. Existing files, directories, unsafe
topology, access denial and other native errors refuse. Unexpected artifacts remain
untouched, source already published remains published, and pending progress is
preserved for operator investigation. No artifact adoption, cleanup, rollback,
new ownership claim or additional source execution interface is introduced.

Legitimate owned partial stages continue through the original durable device/inode
and byte-prefix recovery path. The before/after naming and private owner-directory
lock contracts are unchanged. This changes the existing mutation implementation
policy digest, so plans and grants from a different deployed policy continue to
refuse under existing current-policy rules; it does not migrate or refresh their
authority. No production database or workspace is modified by these tests.

The integrated source-mutation repair retains the publishing handle through file
acknowledgement and keeps pending staging identity durable when the runtime
checkpoint or journal acknowledgement is interrupted. The pending published-file
path still checks publication-stage absence before acknowledging that pinned inode.
Completed-prefix and final-stage checks remain in place. A foreign same-byte
destination and a recreated publication stage each refuse independently; neither
can become completion evidence through recovery.
