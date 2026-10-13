# Native source artifact verification

This increment explicitly combines the native inspector from PR #118 with the
expected artifact contract from PR #120. Both unmerged heads are retained in Git
ancestry. Its review base is #118; #120 remains independently reviewable on #114.

`MutationInspector(app).verify_artifacts(actor, request)` accepts the same
`ApplySourceMutation` identity and exact-plan authorization references as native
source inspection. It performs no mutation or journal transition. It reads the
completed protected history, validates the expected manifest against the original
editing plan, and measures source and artifacts within one existing owned native
namespace. The original live lease, active attempt, worker, independent operator
approval, permissions and emergency stop are checked again before returning.

The native result embeds the unchanged metadata-only expected manifest, the full
source inspection, and exactly two ordered measurements per approved change:

* A modify/delete original must remain a canonical regular single-link file with
  exact approved bytes and digest. Its current native device and inode are observed
  while retained read pins prevent writes and deletion.
* An add original and every publication stage must be absent. Only native
  file-not-found establishes absence; access denial is a refusal.

The existing creation owner/claim, checkout registration, branch, HEAD, raw base
index, creation marker, metadata and source pins remain required. Foreign artifacts
are refused and preserved. An existing canonical checkout-root
`.jarvis-workspace.lock` is pinned and byte-validated as coordination metadata;
verification never creates or acquires it. Nested, aliased or malformed locks
remain foreign artifacts. This recognition grants no lock or cleanup authority.
Native identity measurement uses the extended Windows path spelling, including for
derived siblings longer than the canonical source path. Results contain digests and
bounded metadata, never source text, lease tokens or private owner nonces.

`native_artifacts_verified` describes the artifacts measured during this call.
It does not imply that the current source matches the completed mutation: callers
must inspect `source_inspection.matches_completed_mutation` separately. A later
ordinary source edit can yield false there while retained artifacts remain exact.
The fingerprint excludes measurement time, and includes the exact manifest, source
inspection and measured artifact identities, so repeated unchanged observations
have the same fingerprint.

Execution, deletion and durable artifact ownership flags are always false. An inode
measured now does not prove that it is the original inode retained at mutation time:
the mutation journal does not persist that cleanup provenance. A foreign replacement
with identical approved bytes can be measured and still confers no ownership grant.
No cleanup, rollback, edit chaining, Git commit, validation receipt, HTTP route,
worker dispatcher or unrestricted execution is introduced.

The result is an ephemeral observation. Native pins are released on return, so a
consumer planning future side effects must independently retain/reestablish its own
trusted native pins and authority. Deserializing a result or recomputing a fingerprint
does not establish native evidence; this model is not an operator authorization or
a persisted evidence receipt. Bounds include 64 changes, 128 measurements, 64 KiB
per retained file, 1 MiB of approved aggregate original bytes, and fixed-width native
device/inode integers.

The shared inspector repeats its live authority fence after protected history,
metadata/source namespace checks and result construction, while all native pins
remain held. A stop, permission revocation or lease expiry in that final interval
cannot return a current artifact measurement.
