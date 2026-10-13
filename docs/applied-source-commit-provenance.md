# Applied-source commit provenance

This integration depends on the native source mutation and protected mutation history interfaces, plus exact commit preparation. Its Git ancestry retains those dependencies; it does not install a second source editor or recovery journal.

`AppliedSourceCommitService.bind` consumes a complete mutation whose native checkpoint and journal acknowledgement both exist. The original editing record, creation ownership, runtime attempt, worker, mutation approval, final observation digest and checkpoint are checked through the protected history reader. The service independently measures the current source under the existing two operator approvals and returns the checkpointed commit preparation with an immutable applied-source receipt. It never replays the edit. A historical completed mutation alone cannot establish that current bytes still match.

The native editor retains deterministic `.jarvis-edit-<sha256>.before` files beside changed or deleted source files. The integration derives the exact names from the authenticated mutation operation and change positions, and derives their expected byte counts and hashes from the original source identities. It requires every expected backup to exist with those bytes. Unknown private files, leftover staging files, aliases, unsafe file identities and missing or changed backups remain refusals. Nothing deletes recovery artifacts or accepts caller-supplied ignore patterns.

New Git inspections use observation schema `1.1`, which includes a sorted bounded internal inventory of the validated Git pointer, workspace marker and authenticated backups. Their hashes participate in the observation fingerprint. The native lock belongs in the private owner directory outside the checkout; a checkout-root lock is a foreign protected artifact and refuses inspection. Inspection approval schema `1.1` additionally binds the exact history-derived artifact manifest before any Git read. Schema `1.0` records retain their original serialized fields and hash validation; existing records are not rewritten or granted new authority.

The applied-source receipt is an internal contract, not an HTTP or model execution tool. `git_commit_created` remains false. Git object creation, index writes, reference updates, GitHub publication and recovery after such effects require subsequent separately authorized native implementations.

Tests exercise a real native source mutation, protected history, independent approved measurement, durable preparation and replay without source execution. They also cover pending or forged history, current-source drift, and corrupted, missing or foreign private artifacts. Temporary databases and generated Git worktrees are isolated from runtime state.

The source-file limit counts ordinary source files only. The Git pointer, workspace
marker and authenticated retained backups are independently verified even when
enumerated after 4,096 source files; a 4,097th source file still refuses before
its content is read. Native count regressions exercise both boundaries.

This branch also retains PR #122's foreign staging refusal in its ancestry.
Completed recovery cannot return a legitimate source receipt while an unexpected
publication stage remains. Merge the source history and staging prerequisites
before this integration. Git preparation #109 and authorization #117 were merged
into feature branches after their parent branches had been merged into main;
those implementations are absent from main. This integration retains both
foundations and must be reviewed against main before human merging. Its complete
diff includes the missing preparation and authorization contracts and tests;
merging into the old authorization feature branch alone does not integrate them
into main. Synchronize and revalidate after source prerequisites reach main.
