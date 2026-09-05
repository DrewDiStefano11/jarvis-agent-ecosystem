# Manage the local workforce

Open **Agents → Registered identities → Register identity**. Supply a display
name and a unique stable key such as `research-assistant`. Registration creates a
durable, provisioned identity. Activate it explicitly when ready; active, enabled
identities appear in the shared Planning target selector and office workforce.

The card supports profile edits, suspension/reactivation, and enablement. Its type
and description are metadata, not model instructions. Activation and enablement
grant no task, tool, model-provider, or administrative permissions. Existing
authorization and worker configuration still decide which work may execute.
Suspension or disablement removes the identity from new assignment choices;
existing runtime interruption and recovery remain controlled by the backend.

The **Effective capability** filter reads the existing capability catalog and
current assignments from the identity API. It does not assign capabilities or
grant tool access. **Refresh identities** reloads the registry and selected
capability membership. Read failures retain the previous registry with a visible
warning. The demonstration agents are labeled separately and keep their original
simulation controls.

If registration loses its acknowledgement, the client looks up the same stable
key and recovers a matching profile. A failed retry retains the original key and
payload; it never creates a replacement key automatically. A conflicting profile
is left unchanged. Closing the form does not cancel a registration already
accepted by the API.

## Validation

After installing the backend virtual environment and frozen frontend dependencies:

```powershell
pnpm --dir apps/web typecheck
pnpm --dir apps/web lint
pnpm --dir apps/web test
pnpm --dir apps/web build
node scripts/smoke-workforce.cjs
```

The smoke script starts an API and Vite on isolated loopback ports with a temporary
database configured before any application import. It runs Chromium headlessly,
tests registration and lifecycle controls against the real API, drops one accepted
registration acknowledgement, verifies shared Planning targets and no permission
grant, and restarts the API to verify durable profiles. Desktop/mobile screenshots
and service logs remain in the printed temporary evidence directory. All service
processes started by the script are stopped on completion. Set
`JARVIS_SMOKE_PYTHON` to another installed backend virtual environment's Python
executable when testing from a separate worktree. Inference is disabled during this
acceptance run; the script verifies workforce controls, not autonomous execution.
