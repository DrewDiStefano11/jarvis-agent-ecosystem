# Autonomous research campaign handoff

## Campaign audit — 2026-10-06

Base: `9d5eab1c5e169f7c99f3cf1e9e2ecafa3a725fdf` (main, PR #69 merged).
Independent result verification is present. Existing research behavior is simulated;
catalog acquisition is a pinned-source operator workflow, not a general retriever.
Model-provider HTTP clients have provider-specific authority and must not be reused
as arbitrary network tools. Native runtime authorization, leases, cancellation,
checkpoints, transactional outbox and verification remain the integration boundary.

Open work observed: #70 remote operation control (`5289ffee`), #71 improvement
backlog (`afbdbd1c`), #72 CI evidence (`8d776a66`), #73 planning correction
(`6d600a10`). Their repository/runtime/config/event/docs-contract files are avoided.
The primary checkout contains unrelated untracked validation files; none was changed.
No Coordinator project marker was present; no shared board was created.

## First research PR: destination policy

Branch: `codex/research-retrieval-policy`.
Worktree: `C:/Users/DDistefano/Documents/jarvis-agent-ecosystem/.worktrees/research-retrieval-policy`.
PR: https://github.com/DrewDiStefano11/jarvis-agent-ecosystem/pull/74.
Validated implementation commit: `19f5a9b7290e822ad5f8b5b42caee498ebda714c`.
The initial exact-head review at `f3bed23567a5338511fe031049b992904c1e56ce`
found P2 documentation-prefix classification drift for `3fff::/20` on older
supported Python releases. An emulated older-classifier regression reproduced
acceptance before repair. The explicit exclusion now rejects the prefix without
relying on the standard-library classification; 72 focused tests pass.
Fresh hosted CI and exact-head Codex review are pending. The PR body and review request
record the current publication head, including subsequent handoff-only commits.

Additive pure URL, public-address, DNS-answer and redirect policy. No networking,
HTTP route, model tool, database migration or execution authorization was added.
See `docs/research-retrieval.md` for the transport requirements and deliberate limits.
Focused adversarial tests found and fixed port-zero fallback and cover malformed
bracketed authority suffixes and empty fragments. All 72 focused tests pass,
including suppression of unsafe parser exception chains.
Backend Ruff check and format check pass. Frontend typecheck, ESLint, 104 Vitest
tests and production build pass. Full backend validation passed: 1593 tests,
two existing skips, two dependency deprecation warnings, 866.14 seconds. The final
parser-chain/empty-fragment and review repair additions also passed the focused suite.
The shared Python environment lacked psutil; validation uses this worktree's
isolated `.venv` with declared dependencies. Windows sandbox execution stalled
in database/async tests; the same tests progress with normal filesystem access.

## Independent next slices prepared while gates run

Retrieval contracts: `codex/research-retrieval-contracts`, worktree
`C:/Users/DDistefano/Documents/jarvis-agent-ecosystem/.worktrees/research-retrieval-contracts`.
New strict/frozen `app.models.research_retrieval` records and tests, plus
`docs/research-retrieval-contracts.md`. Depends on PR #74 at policy repair head
`0d0c2e5f4541e0b1a9c01f5eab2c5bb108edfb6c`. All 98 combined policy/contract
tests pass (26 contract cases plus 72 policy cases). Backend Ruff check/format
pass. Frontend typecheck, ESLint, 104 Vitest tests and build pass.
The full backend run passed 1573 tests with two skips but had 48 diagnostics
fixture-copy errors from Windows MAX_PATH in the deep local basetemp. Every
diagnostics/report test was rerun in a short isolated temp directory: 52 passed,
188.93 seconds. No test was skipped to work around the errors and no production
change was needed. PR: https://github.com/DrewDiStefano11/jarvis-agent-ecosystem/pull/76.
Validated implementation commit: `963302750c57d495c77dc8a19eb3d44b6b86f4b2`.
Hosted CI and exact-head review are pending; the PR body records the current head
including handoff-only publication updates. This PR is stacked on #74.

Pinned transport: `codex/research-pinned-transport`, worktree
`C:/Users/DDistefano/Documents/jarvis-agent-ecosystem/.worktrees/research-pinned-transport`.
New internal `app.research_transport`, explicit HTTPCore dependency, 31 passing
deterministic transport tests, plus `docs/research-transport.md`. Depends on both
prior slices. The three byte-identical contract-file snapshots used during
validation were removed and replaced by the committed dependency #76 at
`0eb663091c33483da78fe9f5f77fbcd81f3aefea`. Full backend run:
1600 passed, two skips, 46 Windows MAX_PATH diagnostics setup errors. All
diagnostics/report tests passed under a short isolated basetemp: 52 passed in
189.52 seconds. All 129 affected policy/contract/transport tests pass after the
policy repair. Ruff and all frontend gates pass. Inspect final dependency diff
before publication; no native admission/persistence/recovery is claimed.
Final dependency inspection and the 129-case affected suite pass on the actual
stacked branch. Only transport module/tests, explicit HTTPCore dependency,
transport documentation and this handoff are part of the transport diff.
A final cancellation audit added explicit stream cleanup when TLS initialization
is interrupted before the HTTP layer owns the connection. Its dedicated
regression passes along with all transport cases. The temporary dependency
snapshots were verified byte-identical before removal and are not committed.
Exact-head review at `bad2ae1b1c70bb57a8ccb3139f0fe194b9f6e7f3` found P2
acceptance of DEL and UTF-8 C1 controls. Both were reproduced with failing
regressions. Validation now checks decoded Unicode category Cc, preserving tab,
LF and CR; valid multilingual/layout text has positive coverage. All 29 transport
tests and 127 combined affected cases pass. Fresh review is requested after push.
Later reconciliation adds two giant Content-Length cases under 1 KiB and 8 KiB
header budgets. Both return fixed errors, suppress raw header exception chains
and close the connection. The parser already rejects excessive digit counts;
no new exception handler was needed. Final transport count is 31, combined 129.
PR: https://github.com/DrewDiStefano11/jarvis-agent-ecosystem/pull/77.
Validated implementation commit: `28c7eaff25b04cb0bc5e19b1c1444d6895395e1f`.
Disabled by default, empty origin scope, no runtime/model registration. Hosted CI
and exact-head review are pending; the PR body records the current publication
head. No native admission, durable artifact storage or recovery is claimed.
An actual bounded public GET of `https://example.com/` on 2026-10-06 at 14:00:07
UTC returned 577 bytes of HTML through the default pinned backend and verified
TLS; digest and timestamp were printed without retaining the page. This is
transport smoke evidence, not end-to-end autonomous research acceptance.

Search contract preparation: `codex/research-search-contract`, worktree
`C:/Users/DDistefano/Documents/jarvis-agent-ecosystem/.worktrees/research-search-contract`.
Provider-neutral discovery models, pure normalization/deduplication, stable identity
and digest validation, and an adapter protocol; no provider or dispatcher activated.
26 focused search tests and 72 policy cases pass. Ruff and all frontend gates pass;
full backend validation is in progress. Source/snippet text remains untrusted and
search results are discovery leads rather than retrieved evidence.

New mission-control-shell and mission-control-agent-operations worktrees were
observed during the later overlap audit. Their frontend files are untouched.
Native execution integration will touch files active in #70/#73; defer that
integration while continuing independent source/search foundations.

## Next dependency

1. Build a DNS-pinned, bounded read-only transport with isolated deterministic tests.
2. Add native admission and durable retrieval request/result/provenance records,
   audit/outbox and recovery; keep model-facing access disabled until enforced.
3. Search-provider contract and source/evidence model, then bounded planning,
   claim grounding and independent verification.
4. Browser isolation follows mature retrieval; no personal profiles, credentials,
   arbitrary JavaScript, downloads or write actions.

Fetch current main and inspect open PRs again at each branch boundary. Do not merge,
force-reset, delete branches or modify other sessions' worktrees. The campaign
request authorizes feature pushes, PR publication and exact-head Codex review.

## Main reconciliation checkpoint

The human merged #74 and #73. Current main is
`1ee7a3cd72ec78a5765a9529a1c0faa26fefcfcb`. Retrieval contracts were reconciled
with this main without conflicts. All 133 affected policy/contract and newly merged
planning-correction/coordination-verification tests pass in 60.31 seconds. Backend
Ruff and frontend typecheck, ESLint, 104 Vitest tests and build pass. PR #76 is
retargeted to main at `c2d0b814e4670c8f5c7348ab3ad2582f9d6e3c6c`.
Transport #77 consumed that dependency without conflicts. Its final 129 affected
tests, Ruff and all required frontend gates pass on the reconciled source.
Provenance implementation is checkpointed locally at `c152afe` in its dedicated
source worktree, with full backend 1675 passing plus 106 affected reset cases;
it must consume the reconciled transport before publication. No merge into main
or feature-branch deletion was performed by this session.
