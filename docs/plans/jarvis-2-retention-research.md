# Next slice investigation notes — proposals, not implemented behavior

Master requirements pasted-text-1 lines546–624 and pasted-text-4 prioritize the
request→bounded run→result→review→completion→archive/trash journey. Review slice
is implemented in current dirty branch; do not assume it is merged into main.

There is currently no task archive/trash/erasure implementation. Native TaskRow
and many FK references must remain authoritative. Goal/conversation/message rows
live in app/goals/repository.py and share native task creation transaction.

Candidate sequence:

1. Explicit native visibility/retention metadata linked to TaskRow, separate from
   execution status. Active/archived/trashed/erased; version, timestamps and previous
   disposition for restoration; default Trash30days with auto cleanup disabled.
   Strict resource-authorized, idempotent, bounded single/bulk commands. Preserve
   legacy response envelopes/default serialization when adding optional metadata.
2. Only terminal tasks or unadmitted queued drafts may archive/trash initially.
   Active runs, leases, descendants, ambiguous commands and pending execution
   authority block visibility changes; offer native cancellation separately. Do not
   pretend a generic visibility command authorizes cancelling work.
3. Transactionally fence every existing admission path, not only the Work UI:
   runtime creation/queue, task-lease claim, context assembly, correction/source
   creation and autonomous recovery must reject hidden/erased task scope. A stale
   worker or legacy route must not recreate hidden work. Check actual native
   transaction boundaries before choosing the shared guard.
4. Work list/projects/archive/Trash use shared state and real server queries with
   pagination and selection. Existing Tasks and Business Lab parity must remain
   accessible while navigation migrates. Restore preserves parent/child and
   correction relationships; existing immutable result evidence remains intact.
5. Content erasure needs a separate, explicit closure and an accurate confirmation.
   Append-only audit/native event payloads may already include original task text;
   do not mutate those records or promise physical removal from historical backups.
   Mutable task/message/context/model/artifact content plus all legacy/API/UI read
   projections must be inventoried. Redacted tombstones retain only required
   integrity references/hashes. The current ModelExecutionRepository._contract
   rejects result=None with a retained hash: an explicit native erased-content
   contract is necessary before removing mutable result bytes. Do not silently
   break hash/recovery invariants. Describe unavoidable retained safety data.
6. Test all guards, duplicate/conflicting commands, bulk rollback/bounds, restored
   lineage, migration protections, disabled sweeps, retention interruption/restart,
   and old API/admission paths. Real browser acceptance must archive, restore,
   trash, restore and eligible erasure with accurate outcomes and zero inference.

Do not introduce a second task engine, scheduler, quota ledger or approval model.
No production records/databases, real task-5259094efe, workers or credentials may
be touched. Use disposable fixtures throughout. A later app permission setup is
an explicit local UI workflow, not an implicit change to production authority.
