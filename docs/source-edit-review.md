# Protected source editing review

This main-based increment uses the merged source-change and persisted editing-plan
contracts. It has no dependency on unmerged mutation, validation or Git changes.

`SourceEditingReviewReader(app).read(actor, editing_record_id)` returns a bounded
source-text view of the exact persisted proposal. The existing planning service
protects the read with task and workspace permissions and validates the original
record, request, source observation, projected state and plan digests. The response
binds the editing record and editing plan hash, source plan hash, original workspace
plan and authorization provenance reference. Historical review remains available
after stop or abandonment to an actor with current read permissions.

`preview_source_changes(plan, observation)` and `verify_source_review` are pure
helpers. They revalidate the immutable source plan and its supplied trusted original
observation. They do not observe current files, inspect a workspace, write a plan,
renew a lease or operator grant, create audit/outbox events, or mutate source.

Each file includes its canonical path, add/modify/delete operation, exact before and
after digest/byte count/mode, whole-file line counts, final-LF presence and one
structured contiguous hunk. A linear common-prefix/common-suffix algorithm keeps
three unchanged context lines on each side. Multiple separated edits can share one
larger hunk; this intentionally makes no claim of a minimal edit script. Identical
text with a mode-only change remains visible through its mode metadata. Empty-file
presence is distinct from absence, even when the hunk contains no lines.

Hunk lines are typed context/remove/add text, retaining exact CRLF, lone CR, Unicode
and missing-final-newline bytes. Only LF separates lines. Range coordinates follow
the usual zero-count insertion/deletion convention. A consumer can reconstruct the
approved replacement from the original and the hunk; it should display code as
escaped text, never executable markup. Source text is excluded from model repr and
input-bearing validation errors, and never added to audit or outbox records.

Limits are 64 files, the existing 64 KiB/file and 1 MiB original-plus-replacement
source bounds, 4,096 lines per file, 8,192 aggregate original-plus-replacement lines,
and a 2 MiB serialized review. Aggregate and per-file line budgets are checked
before any hunk objects are constructed. Oversized views refuse with detailed
sanitized codes instead of truncating the exact plan. Escaping expansion also counts
against the serialized bound. Some valid large proposals can therefore require a
different separately authorized review interface; this helper does not approve
them or weaken the plan limits.

The deterministic review fingerprint binds the view policy, exact source plan and
its references, all file metadata and hunk text. Reverification reconstructs the
expected view and rejects even coherently rehashed substituted text or metadata.
Both `current_source_measured` and `execution_authorized` are always false. A view
can explain an exact plan but cannot authorize it, establish workspace ownership,
prove current source or validation results, or refresh future execution authority.
No HTTP route, model importer, dispatcher, code/shell execution, source mutation,
cleanup, commit or publication is enabled here.
