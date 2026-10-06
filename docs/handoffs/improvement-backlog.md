# Improvement backlog implementation checkpoint

PR #71 is reconciled with merged #69/main
`9d5eab1c5e169f7c99f3cf1e9e2ecafa3a725fdf`. The original `808bc03` head passed all
four PR-event CI gates and clean exact-head review. The combined backlog/evidence/
verifier/worker/migration integration passes 230 tests (137.55 seconds), with
Ruff clean. Fresh exact-head CI/review are required after publication; no merge
has been attempted. Existing admission, RBAC, scope deduplication, capacity,
audit/outbox and native task boundaries are preserved.

Branch `codex/improvement-backlog` starts from freshly fetched main
`ff11aba814b8caf67ca5b8f2af415e827e6ec63b` after #68 merged. Other sessions and
PR #63 remain untouched. This is the next independent Self-Build prerequisite;
#69 and remote-control gates take priority when actionable.

## Intended vertical slice

Select the next bounded eligible proposal from persisted native Improvement Lab
analyses, using their recorded critical/high/medium/low priority and evidence
confidence rather than inventing a numeric quality score. Persist its immutable
baseline/proposal/weakness/evidence references and experiment criteria, create a
native queued task, and emit native audit/outbox in the same transaction. Expose
an operable local operator command and native authorized read projection.

Selection is not approval of the proposed change. It does not admit a model run,
execute source edits, widen filesystem scopes, invoke Codex/computer/shell, grant
permissions, or create an execution ledger. The existing worker/runtime/tool
approval paths remain authoritative. Missing evidence creates evidence-gathering
work; it must never be relabeled as a validated experiment. Preserve protected
under-review tasks and checkpoint recovery semantics.

## Design constraints to resolve in implementation

- Use existing ImprovementRecordRow append-only storage for bounded selection
  provenance and native TaskRow lifecycle. No competing migration is needed.
- Reuse native task creation/idempotency/SystemState write fence/outbox helpers.
  Add a narrow same-session integration point if required; SQL stays in repositories.
- Explicit local actor identity and native RBAC are required at selection and
  commitment. Emergency stop must deny admission within the write fence.
- Stable semantic weakness identity must suppress duplicate active work across
  repeated/new baselines. Terminal work may be reconsidered only with fresh evidence.
- Bound every scan and output; retain deterministic tie-breaking and reasons.
  Empty, incomplete, rejected and blocked candidates have explicit outcomes.
- Test real SQLite/RBAC/task/outbox, concurrency, uncertain acknowledgement,
  restart, protected/terminal tasks, revoked permission, emergency stop, invalid
  lineage and immutable criteria. Do not claim an autonomous coding executor.

## Current implementation state

The vertical slice is implemented on this isolated branch. The selector orders
native recorded priority/confidence/impact/frequency and stable IDs; incomplete
evidence selects evidence gathering. Append-only ImprovementRecordRow admission
links to native TaskRow; read projection uses actual task status. Native broker
and repository accept a narrow same-session authorization callback, including
empty/blocked idempotency receipts. Registry audit identity is preserved without
putting it in the simulated-agent foreign key.

The loopback API and explicit operator CLI are documented in
[improvement backlog](../improvement-backlog.md). Grants are native
`select_improvement` and separate `read_improvement` on
`administrative_function/improvement_backlog`. No runtime execution or proposal
approval is admitted. SQLite fencing remains explicit and scans fail closed.

Validation: 20 targeted tests pass, covering fresh CLI/native HTTP admission,
idempotent replay after lost acknowledgement, two concurrent independent native
repositories, protected/terminal tasks, starvation prevention, commit-time
deny/suspension/stop, empty receipt permission revocation and altered immutable
lineage and capacity consumed after selection but before fenced commitment.
Full backend: 1,385 passed/two existing skips in 609.83 seconds before the final
capacity guard. Final backlog/self-improvement/persistence package: 136 passed.
Reconciled main `ee0dd09`: backlog/self-improvement/persistence/coordination migration
integration package passes 139 tests. Frontend typecheck/ESLint/104 Vitest tests/build
pass after reconciliation. API Ruff and script Ruff pass. Inherited migration head
is `20260907_11`; no new migration/dependency. Use fresh
validated short Windows temp roots for full pytest. No relevant tests are skipped.

Freshly fetched main `ee0dd09bf1825ab635e7a61d8fe2ae192f6e0444` is reconciled.
Push/open/attach a dedicated PR, request fresh exact-head
review and required CI, then select useful independent work while gates run.
Record final SHA in PR comments rather than creating a self-referential commit.

Shared callback/RBAC changes in remote-control and #69 are not imported as
unmerged dependencies. Reconcile native integration if either merges. #69 has a
new locally reproduced cancellation/completion race under repair; #70's default
HTTPS authority normalization fix is pushed at `50fd6deb` and awaiting fresh gates.
Do not touch #63. Adaptive executable-node work remains dependent on its own
prerequisites. Continuous development does not end at this checkpoint.
