# Run the integrated local Hub

Use Python 3.12, Node 22, and pnpm 11 (the CI versions). This remains a trusted,
loopback-only application. Runtime actor IDs select task-scoped local permissions;
they are not internet authentication. Do not expose the API or proxy it publicly.

## Install and start

Windows PowerShell, from the repository root:

```powershell
Set-Location apps/api
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m alembic upgrade head
Set-Location ../web
pnpm install --frozen-lockfile
pnpm office:assets
```

`office:assets` downloads the immutable original 8K floor from the pinned public
prototype commit and checks SHA-256 before replacing any local file. It needs
network access on first setup; subsequent invocations verify the cached file.
`pnpm build` also runs this check and fails visibly if the asset is unavailable.
The source image is intentionally not duplicated as a 50 MB Git blob.

Start the API and frontend in separate terminals:

```powershell
# Terminal 1, repository root
Set-Location apps/api
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# Terminal 2, repository root
Set-Location apps/web
pnpm dev
```

On macOS/Linux replace `.venv\Scripts\python.exe` with `.venv/bin/python` and
use forward slashes. Open `http://localhost:5173`. The initial workflow is an
explicit simulation; **System → Start demo** runs only that seeded workflow.
The Office uses the real prototype artwork and shares Hub state.

## Enable actual local planning

1. In **Tasks**, create a task. Open its details and copy its task ID.
2. Open **Planning**, select the task, and click **Prepare local planner for this task**. This explicitly provisions task-scoped runtime permissions and selects the configured worker identity (or creates the initial local identity). The page displays the actor ID when initial configuration is still needed. The equivalent CLI remains available from `apps/api`:

   ```powershell
   .\.venv\Scripts\python.exe -m app.autonomous_worker.setup --task-id TASK_ID
   ```

   The command returns `actorId`. Repeating either setup action is safe. It grants only task-scoped
   runtime permissions, never tools or `runtime.admin`; existing denies and
   suspended identities remain effective. Use the in-app action for each additional task you
   intend this identity to execute. It does not enable a model or start a worker.
3. Create `apps/api/.env` from the root `.env.example`. The API, setup CLI and
   worker must use the same database URL and working directory. Configure an
   installed local Ollama model and the returned actor ID:

   ```dotenv
   JARVIS_AUTONOMOUS_WORKER_ENABLED=true
   JARVIS_AUTONOMOUS_WORKER_ACTOR_ID=RETURNED_ACTOR_ID
   JARVIS_AUTONOMOUS_WORKER_INSTANCE_ID=local-planning-worker-1
   JARVIS_MODEL_EXECUTION_MODE=local_only
   JARVIS_MODEL_OLLAMA_ENABLED=true
   JARVIS_MODEL_OLLAMA_BASE_URL=http://127.0.0.1:11434
   JARVIS_MODEL_OLLAMA_MODEL=YOUR_INSTALLED_MODEL
   JARVIS_MODEL_PROVIDER_PRIORITY=ollama
   JARVIS_MODEL_ALLOW_REMOTE=false
   ```

   Model and provider settings must also be visible to the API so readiness
   accurately reflects configuration. Restart the API after editing `.env`.
   Keep secrets in local environment files; the UI never receives API keys.
   The existing [provider guide](../model-providers.md) documents the alternative
   OpenAI-compatible adapter. Planning still requires a loopback endpoint and
   does not silently fall back to cloud inference.
4. In a third terminal, from `apps/api`, run:

   ```powershell
   .\.venv\Scripts\python.exe -m app.autonomous_worker
   ```

5. Open **Planning**, select the task, prepare its planner (or select an already-authorized actor and target),
   and choose **Queue local plan**. This explicitly submits the
   task text as operator-approved context. Context checks may require review
   before any execution is queued.
6. Inspect runtime history and persisted model results on the same page. The
   worker validates and deterministically reviews the result. Bounded revisions
   may run; exhausted or unsafe work is visibly failed or paused for human review.
   A task's result link returns to this view. Cancellation in task details revokes
   task ownership; the worker observes the authoritative cancellation.

Provider readiness means configured, not proven inference. The execution result
records the provider, model, request count, failure code and actual returned output.
Retrying a partially acknowledged planning submission reuses its command/context
IDs while that submission remains in the form. Clearing the form does not cancel
existing work. After reloading the page, inspect history before submitting again.

## Validation

Run the commands in the root README. Additionally, after installing both apps:

```powershell
# From repository root; adjust Python path if your venv is elsewhere
apps/api/.venv/Scripts/python.exe scripts/smoke-local-planning.py
```

The smoke script uses an isolated database, an actual API process, a separate
worker process, and the frontend's actual submission function. Its HTTP provider
is explicitly deterministic test inference. It verifies idempotent resubmission,
one provider request, deterministic review, task completion and the durable result
reference. It does not establish real model quality or browser rendering.

## Current boundaries

- Only bounded `planning_review` executes real models; advice is not tool execution.
- General coding/research tools, cloud escalation and Business Lab are not enabled.
- The office's floor registration remains unverified. Candidate room/door/path
  inspection is opt-in. No real identity has an invented desk or walking animation.
- The reviewed Windows supervisor work remains in PR #53; this guide does not
  replace it with another process supervisor.
- Browser visual QA must run on a host with browser access to the local application.

The CI `runtime-browser` job additionally installs Chromium and runs the browser
path against actual API and worker processes. It uploads desktop/mobile office
screenshots and planning completion evidence. To run that path locally after
`pnpm exec playwright install chromium` in `apps/web`, set
`JARVIS_SMOKE_BROWSER=true` and `SMOKE_ARTIFACT_DIR` to an output directory, then
run the same smoke script. Inference remains the explicitly labeled fixture.
