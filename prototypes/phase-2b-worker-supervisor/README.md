# Phase 2B Isolated Local Worker Supervisor and Watchdog Prototype

## Prototype Purpose
This prototype builds a self-contained local process-supervision system that safely starts, observes, stops, restarts, and recovers a simulated Jarvis worker process. It serves to validate local worker-process supervision capabilities before integrating a real Phase 2B autonomous worker.

## Explicit Non-Integration Status
**WARNING:** This prototype is explicitly not integrated into the Jarvis main application or Phase 2A database.

## Fixed-Process Launch Restriction
**WARNING:** The supervisor launches only the bundled simulated worker (`jarvis_simulated_worker`).
**WARNING:** This prototype is not a general command runner. It will never execute user-supplied arbitrary scripts or shell commands.

## Architecture

### Supervisor Architecture
The supervisor process manages the lifecycle of a simulated worker, utilizing a robust watchdog to monitor its states continuously. It executes commands using only `sys.executable` and `shell=False` ensuring high security without executing arbitrary command strings.

### Simulated Worker Architecture
The simulated worker exposes simulated deterministic scenarios for testing but does not:
- Call a model
- Access the network
- Modify the repository
- Run tools
- Launch child processes
- Execute shell commands

## Features

### Durable State
Uses an isolated SQLite database to manage `supervisor_state`, `worker_instances`, `supervisor_events`, and `restart_attempts`.

### Process Identity
Avoids relying exclusively on OS-level PIDs, using random start tokens to protect against termination of unverified processes.

### Readiness and Heartbeats
Readiness is strictly distinguished from simple process existence. The worker must explicitly signal its ready status and keep emitting regular heartbeats for its health verification.

### Watchdog Behavior
The watchdog evaluates process existence, heartbeat age, readiness timeouts, and limit configurations on a safe polling interval without CPU-intensive busy loops.

### Graceful Shutdown and Forced Termination
Graceful shutdown applies first with bounds; if necessary, forced termination applies strictly on verified process identities.

### Restart Policy and Crash-Loop Protection
Exponential backoff delays are configured along with limits. Repeated crashes trigger Crash-Loop state, putting an end to unbounded automatic restarts.

### Stable-Runtime Reset
Healthy running past a stable-runtime threshold safely resets restart attempt counts.

### Pause Behavior & Emergency Stop
Supports pausing new launches and enforcing durable emergency stops that survive supervisor process restarts.

### Supervisor Restart Recovery & Duplicate Supervisor Prevention
Durable states ensure seamless resume when the supervisor itself restarts. Supervisor lease conflicts efficiently prevent duplicate supervisors.

### Log Capture & Retention
Captures standard output and standard error with bounded log limits to ensure predictable disk usage.

### Process Inspection Layer (`psutil`)
This prototype uses `psutil` because supervisor recovery may need to inspect a worker process that is no longer a child of the current supervisor process.

Limitations of a purely standard-library approach:
* `os.waitpid()` generally works only for child processes owned by the current supervisor process.
* `os.kill(pid, 0)` can indicate that a PID exists but does not establish worker identity or provide a reliable exit code.
* Windows ctypes process inspection would require substantial platform-specific implementation and testing.
* PID reuse must be considered during supervisor recovery.
* Cross-platform process creation-time and status inspection are more reliable through `psutil`.

**Durable worker instance IDs and process-start tokens remain authoritative. psutil is used only as an operating-system process-inspection layer. A PID match or `psutil.pid_exists(pid)` result alone is never sufficient authorization to terminate a process.**

If the supervisor cannot sufficiently verify process identity, it must refuse termination and return:
`worker_identity_unverified`

## Known Limitations
- Does not integrate with Phase 2A.
- Does not run a real worker or model.
- Does not modify Task Scheduler or install Windows services.
- Does not execute tools.
- Not a general-purpose command runner.
- Multi-machine supervision is not provided.

## How Findings Inform Phase 2B
Future Phase 2B should integrate similar process launch restrictions, robust durable supervisor states and leases, heartbeat/readiness architectures, deterministic restart/crash-loop limits, and bounded log capturing to maintain safe, predictable operational bounds.

## Installation and Execution

```powershell
# From the prototype directory:
python -m pip install -e .
```

### Windows PowerShell CLI Examples
```powershell
python -m jarvis_worker_supervisor init --runtime-dir runtime
python -m jarvis_worker_supervisor start --runtime-dir runtime --scenario healthy
python -m jarvis_worker_supervisor stop --runtime-dir runtime
python -m jarvis_worker_supervisor status --runtime-dir runtime
python -m jarvis_worker_supervisor emergency-stop --runtime-dir runtime
python -m jarvis_worker_supervisor report --runtime-dir runtime --format json
```

### Exit Codes
- 0: Operation succeeded
- 1: Normal non-running condition
- 2: Invalid configuration
- 3: Supervisor ownership conflict
- 4: Worker startup/readiness failure
- 5: Worker unhealthy
- 6: Shutdown failure
- 7: Crash loop active
- 8: Paused / Emergency-stopped
- 9: Database failure
- 10: Log failure
- 11: Unexpected internal error
- 12: Worker identity unverified

### Running Tests
```powershell
python -m unittest discover -s tests -v
```

### Required Scenarios
The supervisor supports simulating `healthy`, `crash-immediately`, `hang-after-ready`, `log-flood`, `stop-heartbeats`, `ignore-shutdown`, and others for deterministic evaluation.

### Windows Task Scheduler Example
Refer to `examples/example-windows-task-scheduler.xml` for a sanitized conceptual deployment reference.
