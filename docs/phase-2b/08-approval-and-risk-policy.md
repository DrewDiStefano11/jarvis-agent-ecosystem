# 08 - Approval and Risk Policy

This document defines the policy engine that governs whether a requested action requires human authorization. While Phase 2B focuses primarily on read-only (Green) tasks, the approval system must be robust enough to handle future capability expansion without requiring architectural changes.

## Risk Levels

Every tool in the Tool Registry is assigned a baseline risk level. The Policy Engine evaluates the tool, its parameters, and the current task context to determine the final risk level of a requested step.

### Green
* **Meaning:** Safe, read-only, or highly constrained internal operations.
* **Examples:** `read_file`, `list_files`, `check_status`.
* **Behavior:** Automatic execution permitted.
* **Notification:** None.
* **Approval:** Not required.
* **Audit:** Required (standard tool execution audit).

### Yellow
* **Meaning:** Low-risk state mutations bounded by a safe sandbox.
* **Examples:** `write_artifact` (to a specific temp directory).
* **Behavior:** Automatic execution permitted by default, but configurable by the user to require approval.
* **Notification:** Optional.
* **Approval:** Not required by default.
* **Audit:** Required, including exact payload written.

### Orange
* **Meaning:** Moderate-risk external mutations or irreversible local changes.
* **Examples:** `create_patch` (if applied directly), interacting with non-critical local APIs.
* **Behavior:** Execution is paused.
* **Notification:** Required. User is prompted via the UI.
* **Approval:** **Required.**
* **Reversal:** Reversal data (e.g., git stash commands) must be provided in the approval request if applicable.

### Red
* **Meaning:** High-risk external actions.
* **Examples:** `git_push`, `send_email`.
* **Behavior:** Execution is paused.
* **Notification:** Required. Urgent alert.
* **Approval:** **Required.** Must explicitly type/confirm the action.
* **Reversal:** Often not possible; user must manually intervene if wrong.

### Black
* **Meaning:** Strictly prohibited actions based on system policy.
* **Examples:** Attempting to read outside workspace roots, modifying worker configuration.
* **Behavior:** Execution is outright rejected. The worker is told the action failed due to policy.
* **Notification:** Logged as a security event.
* **Approval:** **Prohibited.** Cannot be approved.

## Approval Lifecycle

1. **Creation:** When the Executor encounters an Orange or Red tool request, it writes a `PendingApproval` record to the SQLite database.
2. **Worker State:** The worker transitions to `WaitingForApproval`, releases its lease on the task, and returns to polling other work.
3. **Disclosure:** The UI/API presents the exact tool name, affected resources (e.g., file paths), and the exact payload to the user.
4. **Resolution:**
   * **Approved:** The approval record is marked `approved`. The next worker poll picks up the task, sees the approval, and executes the tool.
   * **Rejected:** The approval record is marked `rejected`. The worker resumes the task, receives a `TOOL_REJECTED` error, and the model must replan.
5. **Expiration:** Approvals expire if not acted upon (e.g., after 24 hours). An expired approval behaves identically to a rejection.
6. **Cancellation:** If the parent task is cancelled, all pending approvals for it are marked cancelled.
7. **Replay Prevention:** An approval record is tied cryptographically or via UUID to the specific tool request checkpoint. If the worker crashes and resumes, it consumes the same approval. A new, slightly different tool request generates a completely new approval requirement.
8. **Immutability:** A language model cannot, under any circumstances, modify the risk level of a tool or approve its own requests.
