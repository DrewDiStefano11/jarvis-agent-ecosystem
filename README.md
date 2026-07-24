# Jarvis Agent Ecosystem

Jarvis combines the Phase 2A local deterministic simulation and durable SQLite control plane with Phase 2B fenced task leases and a production-integrated Context Assembler. Tasks, approvals, task ownership, context manifests, audit history, notifications, system state, event delivery, and workflow checkpoints survive backend restarts while all agents, model calls, tools, and external actions remain simulated or unavailable.

## What works in Phase 2A

- FastAPI HTTP API and multi-client WebSocket event stream
- deterministic 25-step Caribbean trip workflow with delegation, artifact handoff, review, revision, and delivery
- guarded agent state transitions, emergency stop/resume, retry, reset, and controlled failures
- React dashboard, hierarchical tasks, agent details, approval inbox, audit timeline, office, and system controls
- deterministic departments, five permanent agents, tasks, approvals, artifacts, notifications, and audit fixtures
- installable PWA metadata, offline shell, reconnection states, HTTP refresh fallback, and a 320px mobile layout
- YAML agent manifests validated by Pydantic
- SQLite persistence through typed SQLAlchemy models and Alembic head `20260724_03`
- transactional outbox, durable idempotency keys, workflow runs, per-step checkpoints, and safe restart recovery
- deterministic context assembly with provenance checks, trust ordering, redaction, injection signals, bounded truncation, durable manifests, and review gating
- registered worker lifecycle, atomic task acquisition, renewable fencing tokens, attempt history, cancellation revocation, and expired-lease recovery

## Explicit non-capabilities

No real AI models, autonomous workers, email, calendars, cloud files, browser/desktop automation, shell execution, financial services, authentication, external database servers, or production infrastructure are included. Context is assembled but never sent to a provider. Telemetry, tools, files, reports, and temporary agents are labeled simulations. LangGraph, Prefect, PostgreSQL, Redis, Phaser, Ollama, and all real integrations are deferred.

## Architecture

`apps/api` owns authoritative state and contracts. Route handlers use services, a durable repository, and explicit command boundaries; committed event envelopes are delivered from an SQLite outbox. `apps/web` retains the Phase 1 synchronization contract. See [ARCHITECTURE.md](ARCHITECTURE.md) and [Context assembler](docs/context-assembler.md).

## Fresh Windows setup

Prerequisites: Git, Python 3.11+, Node.js 20+, and pnpm 9+ (`corepack enable`). From PowerShell:

```powershell
git clone <private-repository-url> jarvis-agent-ecosystem
Set-Location jarvis-agent-ecosystem
git switch main

Set-Location apps/api
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m alembic upgrade head
python -m alembic current

Set-Location ..\web
pnpm install --frozen-lockfile
```

Copy `.env.example` and `apps/web/.env.example` to local `.env` files only when overriding defaults. Never commit them.

## Run locally

Open two PowerShell terminals from the repository root:

```powershell
# Terminal 1
Set-Location apps/api
.\.venv\Scripts\uvicorn.exe app.main:app --reload --host 127.0.0.1 --port 8000

# Terminal 2
Set-Location apps/web
pnpm dev
```

Open `http://localhost:5173`. API docs are at `http://127.0.0.1:8000/docs`, OpenAPI at `/openapi.json`, health at `/api/health`, and WebSocket events at `ws://127.0.0.1:8000/ws/events`. Use **System → Start demo** to run the demonstration.

## Verification commands

```powershell
# Backend
Set-Location apps/api
.\.venv\Scripts\ruff.exe format . --check
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\python.exe -m pytest -q

# Frontend
Set-Location ..\web
pnpm typecheck
pnpm lint
pnpm test
pnpm build
```

To test WebSocket behavior, open the application in two windows, start the demo, and confirm both windows advance in order. Close the API to see `reconnecting`/`offline` and last-known-state messaging; restart it to resynchronize. The backend WebSocket test also proves snapshot delivery and monotonic sequences.

## PWA and mobile testing

Run `pnpm build` followed by `pnpm vite preview`, then use browser Application tooling to inspect the manifest and service worker. Test at 320×700 and 390×844. Installation is supported from a secure/localhost browser context. A later remote-access phase can expose the HTTPS app so Safari on iPhone can use **Share → Add to Home Screen**; this phase adds no native iOS or remote-access capability.

## Troubleshooting

- CORS errors: keep the web origin at `http://localhost:5173` or set `WEB_ORIGIN` explicitly.
- WebSocket remains offline: verify the API uses port 8000 and `VITE_WS_URL` matches it.
- PowerShell blocks activation: run the virtual environment executables directly as shown in verification commands.
- Stale fixture state: use **System → Reset demo**; reset cancels the active runner before reseeding.
- Port in use: change both the server port and corresponding frontend environment URL.

## Repository map

```text
agents/manifests/  Versioned permanent-agent definitions
apps/api/          FastAPI contracts, repository, simulator, tests
apps/web/          React/Vite PWA and tests
docs/              Product, API, event, manifest, roadmap, testing docs
.github/workflows/ CI checks
```

Runtime data defaults to `apps/api/data/jarvis.db` and is ignored by Git. See [persistence](docs/persistence.md), [migrations](docs/migrations.md), [recovery](docs/recovery.md), and [task leases](docs/task-leases.md).
