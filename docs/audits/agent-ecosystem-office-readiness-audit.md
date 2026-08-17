# Agent Ecosystem Office-Readiness Audit

## Audit identity

- Repository: `DrewDiStefano11/jarvis-agent-ecosystem`
- Audit branch: `codex/audit-full-system-hardening-office-readiness`
- Effective starting SHA: `c1d90a051e4000bab0ba66c73262ed1a86cd0ab4`
- Remote `main` at preflight: `98226b2789533785d4a0d31edcf848a6b9b9971f`
- Local `main` at preflight: `698a9ec4705752ffdb3358fffc7381c0e1913e3a`
- Tracked files at preflight: 163
- Audit objective: harden the complete repository and prepare it to receive the
  production office implementation in a later pull request without importing that
  implementation during this audit.

### Approved preflight exception

The mission normally required autonomous-worker PR #47 to be merged before work
began. On 2026-07-29, PR #47 was still open and draft at exact head
`c1d90a051e4000bab0ba66c73262ed1a86cd0ab4`. The operator explicitly directed the
audit to proceed and to treat that head as though it had merged. The audit branch
therefore starts from that exact PR head. PR #47 is inherited baseline, not part of
the audit diff.

PR #47 was the only open pull request returned by GitHub at preflight. Therefore
there was no separate active pull request whose changed files overlapped this audit.

## Preflight inventory

- Governing `AGENTS.md`: present and read.
- Required `.agent/PLANS.md`: missing from the repository at the starting SHA.
- Repository README, architecture, contribution policy, API/event contracts,
  migration, persistence, recovery, security/identity, model-provider, runtime,
  autonomous-worker, task-lease, testing, CI, and package-script documentation:
  inspected before implementation.
- Generated/vendor/runtime paths excluded from changes and commits:
  `.env`, `.venv`, `node_modules`, `.pnpm-store`, `dist`, `build`, `coverage`,
  `.pytest_cache`, `.ruff_cache`, `__pycache__`, `*.pyc`, `*.db`, `*.db-shm`,
  `*.db-wal`, `data`, `*.egg-info`, and TypeScript build-info files.
- Initial tracked-artifact scan found no tracked runtime database, SQLite sidecar,
  dependency directory, build output, virtual environment, cache, or compiled
  Python artifact.
- Initial `git diff --check`: passed.

## Environment and dependency baseline

| Item | Result |
| --- | --- |
| System Python | 3.12.10 |
| Backend virtual-environment Python | 3.12.13 |
| Node.js | 24.16.0 |
| pnpm | 11.9.0 |
| Backend editable install | Passed; declared runtime/dev requirements satisfied |
| Backend `pip check` | Passed; no broken requirements |
| Frontend frozen-lockfile install | Passed; already up to date |

CI currently targets Python 3.12, Node.js 22, and pnpm 11. The local Node version is
newer than CI and will not be used as evidence of CI parity by itself.

## Untouched baseline validation

### Backend

From `apps/api`:

| Command | Result |
| --- | --- |
| `ruff format . --check` | Passed; 95 files already formatted |
| `ruff check .` | Passed |
| `python -m pytest -q` | Passed; 653 tests |
| `python -m alembic heads` | Passed; exactly `20260729_05 (head)` |
| Blank-database `alembic upgrade head` | Passed through all six revisions |
| Empty `20260729_05 -> 20260729_04` downgrade | Passed |
| Re-upgrade to `20260729_05` | Passed |

The backend suite emitted 753 warnings. Most are repeated FastAPI
`on_event("startup"/"shutdown")` deprecations; one collection warning reports the
Starlette TestClient compatibility path moving from `httpx` to `httpx2`. These
warnings existed before audit edits and are recorded rather than silently fixed.

### Frontend

From `apps/web`:

| Command | Result |
| --- | --- |
| `pnpm install --frozen-lockfile` | Passed |
| `pnpm typecheck` | Passed |
| `pnpm lint` | Passed |
| `pnpm test` | Passed; 1 file and 21 tests |
| `pnpm build` | Passed; 35 modules transformed |

Baseline production assets were approximately 0.65 kB HTML, 13.12 kB CSS, and
266.85 kB JavaScript before gzip.

## Section inventory and findings

Severity definitions:

- **P0**: critical safety, corruption, or severe security defect.
- **P1**: major correctness, authorization, persistence, or reliability defect.
- **P2**: meaningful defect, maintainability risk, or office-integration blocker.
- **P3**: worthwhile, low-risk improvement.
- **Deferred**: valid issue inappropriate for this merge-safe audit.

### Section 3 — Security, authorization, and fail-closed safety

| Severity | Finding and evidence | Resolution and regression coverage |
| --- | --- | --- |
| P1 | Identity/control-plane routes trusted a caller-supplied actor even though this phase has no user authentication. A LAN bind or proxy would expose privilege-creating routes. | HTTP and WebSocket traffic now reject non-loopback peers, and `WEB_ORIGIN` must be a credential-free structural loopback origin. Tests prove remote peers cannot reach health or identity mutations and invalid origins fail configuration. |
| P1 | Default validation could reflect rejected values, while worker metadata and lease failure objects accepted arbitrary nested JSON that could enter SQLite, audit, and outbox records. | Mutation contracts forbid unexpected fields; identifiers, text, and metadata are bounded and secret-aware; the 422 handler returns only bounded locations/types. Adversarial tests prove secret-bearing and oversized values are neither persisted nor reflected. |
| P1 | Identity requests ignored unknown keys and context metadata used `str(dict)` as a shallow size check. | Identity contracts now forbid extras. Context metadata uses recursive safe normalization with depth, item, serialized-size, control-character, and secret-key checks. |
| P2 | Idempotency headers were unconstrained at HTTP despite a 200-character database column. Retry, duration, token, pricing, model-name, and URL settings also lacked practical upper bounds. | All idempotent routes use one printable 200-character contract. Settings impose finite operational/cardinality limits, covered by parameterized tests. |
| P2 | Public not-found messages interpolated attacker-controlled path IDs. | Codes and statuses remain stable, but messages are generic and never echo path input. |

No subprocess, shell, browser, arbitrary filesystem, tool invocation, redirecting
provider, or remote-model execution path was found in the production worker. Local
model transports remain disabled by default and structurally loopback-gated.

### Section 2 — Backend, persistence, runtime, and migrations

| Severity | Finding and evidence | Resolution and regression coverage |
| --- | --- | --- |
| P1 | WebSocket connect and every client `resync` called the durable global emitter. A client could advance sequence, grow the outbox, create audit traffic, and broadcast snapshots to unrelated clients. | Snapshots are requester-only frames at the committed cursor and do not mutate sequence, audit, or outbox. Commands are bounded. A multi-client test proves isolation and zero durable growth. |
| P1 | The main repository cached the global cursor while lease/worker repositories advanced the same database row. A later event could reuse or rewind a committed sequence. | Main allocation refreshes the committed cursor before incrementing. A regression test advances it independently and proves the next event continues correctly; the existing unique outbox constraint remains the fail-closed guard. |
| P1 | Worker health could 500 on malformed queued state and could report healthy for corrupt results, exhausted outbox, or lost ownership when the worker toggle was disabled. | Health handles malformed persisted state and safety hazards degrade health regardless of enablement. API normalization exposes no SQL, path, or payload. Corrupt-state tests cover enabled and disabled configurations. |
| P2 | Deprecated startup/shutdown decorators generated hundreds of warnings and fragmented cancellation/disposal ownership. | One FastAPI lifespan owns dispatcher startup, lease recovery, simulator cancellation, clean-shutdown persistence, and engine disposal. Full-suite warnings fell from 753 to one external compatibility warning. |
| P2 | Initial snapshots consumed sequence 1, making the first domain event sequence 2. | Snapshots report cursor 0 and the first durable domain event is sequence 1; API/context/WebSocket expectations changed together. |

Route handlers still contain no SQL. No migration history changed. Blank upgrade,
schema-drift check, final-revision downgrade, and re-upgrade all pass.

### Section 4 — Frontend and office-integration readiness

| Severity | Finding and evidence | Resolution and regression coverage |
| --- | --- | --- |
| P1 | The service worker intercepted every GET, including cross-origin API reads, cached API responses, and could return the HTML shell for a failed API request. | It now handles only same-origin frontend GETs, caches only successful frontend responses, uses navigation-only shell fallback, and deletes obsolete caches. Syntax is part of `pnpm lint`. |
| P2 | Malformed WebSocket JSON threw from `onmessage`; online/reconnect could create overlapping sockets; concurrent refreshes could apply stale responses. | The shared store validates framing/collection shapes, requests resync on malformed/gapped data, owns one socket/timer, ignores stale callbacks, bounds cursors, and generation-orders refreshes. Tests cover malformed frames, reuse, duplicates, gaps, and interleaved runtime sessions. |
| P2 | Mobile omitted Office/System. Modal drawers did not move/trap/restore focus or close on Escape. | All routes are in mobile nav. Shared dialog focus management implements focus entry, Tab trapping, Escape, and trigger restoration, with keyboard regression coverage. |
| P2 | Frontend defaults used `localhost` while the API binds IPv4 `127.0.0.1`; browser QA reproduced a persistent socket error. | API/WebSocket defaults and examples now use `127.0.0.1`; live QA then reported connected. |
| P2 | Root-absolute PWA paths blocked non-root Vite bases; unknown routes silently redirected without useful titles/404. | PWA URLs honor `BASE_URL`, manifest URLs are relative, routes set titles, and unknown paths show a recoverable 404. |

`apps/web/src/state` remains the only frontend domain-state authority. Office uses
shared agent/task selection and no second socket/store. Large future images can be
served from `public` instead of entering JavaScript.

Browser checks covered all seven routes, live connection, Office shared details,
390×844 mobile navigation, focus/Escape restoration, and fresh console errors.
All passed.

### Section 1 — Repository architecture, tooling, and maintainability

| Severity | Finding and evidence | Resolution and regression coverage |
| --- | --- | --- |
| P2 | Runtime documentation described FastAPI, SQLAlchemy, identity, and worker integration as future/nonexistent despite durable Phase 2C composition. README and web metadata also used stale phase labels. | Runtime, roadmap, README, API/event, identity, manifest, and web metadata now distinguish the pure domain core from current durable composition and state the actual local-only boundary. |
| P2 | CI did not exercise the documented final-revision downgrade/re-upgrade or reject tracked databases, sidecars, dependencies, build outputs, caches, `.env`, and compiled artifacts. | CI now runs the migration round trip and a Windows integrity job. Service-worker syntax is included in frontend lint. |

The 163-file starting inventory was inspected by directory and ownership boundary.
No duplicate frontend domain store, SQL in routes, second outbox, runtime database,
vendored dependencies, abandoned implementation, or office-prototype code was
found or added.

### Section 5 — Cross-cutting reliability and final readiness

Restart, fencing, cancellation, emergency stop, duplicate command/event, model
timeout/repair, corrupt result, outbox, reconnect, context review gate, and malformed
persisted-state paths are covered by the 673-test backend suite. New tests close the
uncovered cursor, health, resync, stale-PWA, and reconnect combinations.

## Tests and final validation

- API: local-peer enforcement, safe 422 envelopes, idempotency bounds, committed
  cursor refresh, requester-only resync, and sequence-zero snapshots.
- Lease/context/identity/config: secret-bearing nested metadata, unknown fields,
  oversized identifiers/results, pricing cardinality, and retry/duration bounds.
- Worker: malformed queued snapshots and disabled-worker durable safety hazards.
- Frontend: malformed frames, socket reuse, mobile Office/System, dialog keyboard
  lifecycle, route titles, and recoverable 404.

| Gate | Result |
| --- | --- |
| Backend Ruff | Passed; 95 files |
| Backend pytest | Passed; 673 tests, one external Starlette/httpx2 warning |
| Alembic | One head; blank upgrade, `check`, downgrade/re-upgrade passed |
| Frontend install/typecheck/lint/test/build | Passed; 25 tests, 35 modules |
| Browser QA | Passed on desktop and 390×844 |
| Secret/artifact/database/build scans | Passed |
| Diff and mixed-line-ending checks | Passed |

## Deferred items

### Deferred P1 — production user authentication

- Evidence: `X-Jarvis-Actor-Id` is caller-supplied and is not a credential.
- Reason: credential/session architecture and trusted-proxy policy require product
  and infrastructure decisions outside this merge-safe local phase.
- Mitigation: HTTP/WebSocket peers and web origins are loopback-only; documentation
  prohibits proxy/LAN/public exposure.
- Follow-up: select authentication and proxy policy before any remote bind.
- Risk: bypassing loopback would permit privilege escalation.

### Deferred P2 — generated frontend runtime contracts

- Evidence: TypeScript interfaces mirror OpenAPI manually; framing and collection
  shapes are runtime-checked, but not every nested field.
- Reason: selecting generator/validation tooling changes the developer workflow and
  should be coordinated with office integration.
- Follow-up: adopt OpenAPI generation/runtime validation and compatibility policy.
- Risk: future backend changes could drift from hand-written types.

### Deferred P3 — external TestClient warning

- Evidence: one final warning asks consumers to migrate Starlette's TestClient path
  from `httpx` to `httpx2`.
- Reason: no runtime defect was reproduced and blind dependency upgrades are
  prohibited.
- Follow-up: assess the FastAPI/Starlette-supported migration separately.

### Deferred P3 — absent `.agent/PLANS.md`

- Evidence: the mission referenced this path, but it is absent and no repository
  policy defines its format.
- Reason: inventing contributor policy here would be speculative.
- Follow-up: owners decide whether to adopt a durable plan convention.

## Remaining office-integration risks

- The real office must own renderer-only/transient visual state while adapting the
  shared store's agent/task selection.
- Persistent mounting, inert hidden content, sprite/image memory budgets, and actual
  asset dimensions require the real office code/assets; empty scaffolding here
  would not prove them.
- Remote deployment remains prohibited until production authentication is selected.
- The office PR should adopt generated/runtime-validated contracts or extend shared
  validators for new nested data.

No current repository defect blocks beginning that self-contained integration.

## Final readiness conclusion

**READY FOR OFFICE INTEGRATION**, provided the loopback authentication mitigation
remains intact and the future office PR implements its renderer-local state,
persistent mounting, inert hidden state, and real-asset performance tests.
