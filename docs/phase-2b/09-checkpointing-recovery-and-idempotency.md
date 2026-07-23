# 09 - Checkpointing, Recovery, and Idempotency

This document extends the Phase 2A checkpointing concepts to handle the realities of real worker execution, network volatility with local models, and safe process interruption.

## Checkpoint Boundaries

A Phase 2B worker must durably record its state at specific boundaries to ensure that non-idempotent operations are never accidentally repeated due to a crash.

The system records checkpoints at the following events:
1. **Plan Received:** After a validated plan is received from the model, before executing step 1.
2. **Tool Request Generated:** When the Executor prepares to run a tool, but before the tool actually executes. (Contains the `Idempotency Key`).
3. **Tool Result Generated:** Immediately after a tool succeeds or fails safely, storing the output.
4. **Approval Requested:** When execution pauses for an Orange/Red action.
5. **Final Result:** When the task is validated as complete.

## Checkpoint Structure

```json
{
  "version": "1.0",
  "task_id": "task-123",
  "step_index": 2,
  "state": "executing",
  "last_tool_idempotency_key": "step-2-read_file-b4a1",
  "context": {
    "plan_id": "plan-456",
    "completed_steps": ["step-1"],
    "tool_results": {
      "step-1": "file list..."
    }
  }
}
```
*Note: Checkpoints use a version field to allow schema evolution in future phases.*

## Idempotency and Replay Prevention

When the Executor runs a tool, it generates an `Idempotency Key` based on the Task ID, Step ID, and Tool Parameters.

If the worker crashes *during* tool execution (between boundary 2 and 3), the recovery behavior depends on the tool's risk level:
* **Green (Read-only):** The tool is inherently idempotent. Upon recovery, the worker re-executes the tool using the same idempotency key, then creates the Step Result checkpoint.
* **Yellow/Orange (Write):** If a write operation is interrupted, the worker *cannot* blindly re-execute it. Phase 2B avoids this by limiting tools to read-only. For future write tools, the tool itself must implement idempotency (e.g., `upsert` semantics) or the system requires human intervention to resolve the ambiguous state.

## Crash Scenarios and Recovery Behavior

| Crash Point | System State on Restart | Required Recovery Behavior |
|---|---|---|
| Before claim commit | Task is `queued` | Another worker claims normally. |
| After claim commit, before planning | Task `in_progress`, worker lease active. | Wait for lease expiration, reclaim, start planning. |
| During model HTTP request | Lease active. No plan checkpoint. | Wait for lease expiration, reclaim, re-request plan. |
| After model response, before checkpoint | Plan received in memory, not on disk. | Wait for lease expiration, reclaim, re-request plan. (At-least-once planning). |
| After safe read tool succeeds, before checkpoint | Tool executed in memory. | Reclaim, re-execute read tool, write checkpoint. |
| During an active Approval Wait | Task `waiting_for_approval`. | No action needed. Wait for human. |
| After task complete, before commit | Task complete in memory. | Reclaim, load final checkpoint, observe completion, re-commit completion state safely. |

## Recovery After Termination

* **Clean Shutdown:** The supervisor sends SIGTERM. The worker catches it, finishes the current Green step, writes a checkpoint, sets the task lease to NULL, and exits. The next worker picks it up immediately.
* **Forced Termination (SIGKILL) / Power Loss:** The worker dies instantly. The lease remains in the database. Recovery relies strictly on the 30-second lease expiration mechanism. Once expired, a new worker reclaims the task and loads the last durable checkpoint.
