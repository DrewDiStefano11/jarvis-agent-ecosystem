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
change was needed. Publication is the next step; the PR body records exact head.

Pinned transport: `codex/research-pinned-transport`, worktree
`C:/Users/DDistefano/Documents/jarvis-agent-ecosystem/.worktrees/research-pinned-transport`.
New internal `app.research_transport`, explicit HTTPCore dependency, 25 passing
deterministic transport tests, plus `docs/research-transport.md`. Depends on both
prior slices. An exact contract-file snapshot was used during validation; replace
it with the committed contract branch before publication. Full backend run:
1600 passed, two skips, 46 Windows MAX_PATH diagnostics setup errors. All
diagnostics/report tests passed under a short isolated basetemp: 52 passed in
189.52 seconds. All 123 affected policy/contract/transport tests pass after the
policy repair. Ruff and all frontend gates pass. Inspect final dependency diff
before publication; no native admission/persistence/recovery is claimed.
Disabled by default, empty origin scope, no runtime/model registration. Not
published yet. No native admission, durable artifact storage or recovery is claimed.
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
