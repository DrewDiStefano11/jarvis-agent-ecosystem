# Mission Control overview

The Overview is the operational entry point for Jarvis. Its attention panel reports
existing system blockers, while the health list reads real status fields rather
than the simulator's resource fixtures. An unavailable status is shown explicitly.
Runtime work uses the identity selected in Planning and its optional task filter.
It displays non-terminal runs from the existing authorized page; it is not a
system-wide inventory. Choose identity opens Planning without selecting an actor
or granting permissions automatically.

Task requests are durable task records. The legacy task contract does not identify
execution provenance for every request, so these rows may include demonstration
work. Inspect opens the same shared task drawer used by Tasks. New task opens the
existing creation form and preserves its retry-safe acknowledgement behavior;
creation alone does not launch autonomous execution.

Simulated resource values and seeded agent counts live in the collapsed
Demonstration state disclosure. Recent activity and approval records disclose that
they may include simulator events. Workspace plan authorization remains in Planning.

Desktop navigation collapses to an icon rail with accessible labels. Laptop widths
use the rail automatically. Phone navigation puts Overview, Tasks, Approvals and
Planning beside More, which exposes Agents, Activity, Business Lab, Office and System.
Disconnected synchronization retains the last snapshot, displays its timestamp,
and offers HTTP refresh. A connected event stream alone does not dismiss HTTP or
sequence-reconciliation errors. Shell emergency stop and resume require explicit
confirmation and report backend acknowledgement failures.

## Verify

```powershell
pnpm --dir apps/web typecheck
pnpm --dir apps/web lint
pnpm --dir apps/web test
pnpm --dir apps/web build
$env:JARVIS_SMOKE_PYTHON='C:\path\to\backend\.venv\Scripts\python.exe'
node scripts/smoke-mission-control.cjs
```

The browser harness uses isolated temporary databases and ports, never the runtime
database. It tests five screen sizes, actual task creation/details, mobile navigation,
approval access, system controls, and offline/reconnect. Screenshots and service logs
remain in the printed temporary directory. Inference is disabled.
