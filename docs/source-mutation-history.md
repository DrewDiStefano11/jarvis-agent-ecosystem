# Protected source mutation recovery history

The internal `MutationHistoryReader` provides recorded mutation intent and native checkpoint status to an authenticated actor with task-read and workspace permission. It has no HTTP route, model tool or mutation entry point. It performs no filesystem read or write.

The reader verifies the editing proposal, native creation lineage and acknowledged creation checkpoint, original independent operator approval, immutable mutation plan, journal integrity and completed-prefix position. A recorded acknowledgement requires the exact native mutation checkpoint. Original approvals may have expired: history verifies provenance, while execution requires a fresh exact-plan approval through the mutation service.

Reads remain available after emergency stop without requiring a live lease, write permission or active execution attempt. They still require protected workspace access and preserved creation lineage. Unsupported lineage changes and corrupt evidence safely refuse.

Every response explicitly reports `current_source_measured: false` and `execution_authorized: false`. A historical completion checkpoint does not prove current filesystem bytes after later operator edits, and cannot authorize resumption, rollback, cleanup, validation, commits or publication. Pending unacknowledged journal intent is distinguishable from native checkpoint evidence. No source text, lease token or private ownership nonce is returned.

This PR explicitly depends on the authorized mutation adapter. It introduces no migrations, runtime orchestrator, checkpoint writer or shared checkout changes. Recovery remains governed by the adapter's original ownership, live lease/attempt fence, exact operator approval and filesystem verification.

