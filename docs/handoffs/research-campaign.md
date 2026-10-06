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
Exact implementation head / PR / CI / review state: recorded at publication below.

Additive pure URL, public-address, DNS-answer and redirect policy. No networking,
HTTP route, model tool, database migration or execution authorization was added.
See `docs/research-retrieval.md` for the transport requirements and deliberate limits.
Focused adversarial tests found and fixed port-zero fallback and cover malformed
bracketed authority suffixes and empty fragments. All 70 focused tests pass,
including suppression of unsafe parser exception chains.
Backend Ruff check and format check pass. Frontend typecheck, ESLint, 104 Vitest
tests and production build pass. Full backend validation passed: 1593 tests,
two existing skips, two dependency deprecation warnings, 866.14 seconds. The final
parser-chain/empty-fragment additions also passed the 70-case focused suite.
The shared Python environment lacked psutil; validation uses this worktree's
isolated `.venv` with declared dependencies. Windows sandbox execution stalled
in database/async tests; the same tests progress with normal filesystem access.

## Independent next slices prepared while gates run

Retrieval contracts: `codex/research-retrieval-contracts`, worktree
`C:/Users/DDistefano/Documents/jarvis-agent-ecosystem/.worktrees/research-retrieval-contracts`.
New strict/frozen `app.models.research_retrieval` records and tests, plus
`docs/research-retrieval-contracts.md`. Depends on destination policy. Focused
26 tests pass using the pending policy dependency; complete branch validation is
required after stacking the committed dependency. Not published yet.

Pinned transport: `codex/research-pinned-transport`, worktree
`C:/Users/DDistefano/Documents/jarvis-agent-ecosystem/.worktrees/research-pinned-transport`.
New internal `app.research_transport`, explicit HTTPCore dependency, 25 passing
deterministic transport tests, plus `docs/research-transport.md`. Depends on both
prior slices; complete branch validation is required after stacking dependencies.
Disabled by default, empty origin scope, no runtime/model registration. Not
published yet. No native admission, durable artifact storage or recovery is claimed.
An actual bounded public GET of `https://example.com/` on 2026-10-06 at 14:00:07
UTC returned 577 bytes of HTML through the default pinned backend and verified
TLS; digest and timestamp were printed without retaining the page. This is
transport smoke evidence, not end-to-end autonomous research acceptance.

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
