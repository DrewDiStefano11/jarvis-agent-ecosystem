# 10 - Validation, Review, and Completion

This document defines how the system determines whether a task is actually complete. A language model will often confidently declare a task finished even if it failed to achieve the user's objective or hallucinated the result. Phase 2B implements a strict completion gate.

## The Completion Gate

A worker cannot mark a task `Completed` simply because the model generated text. The task must pass through the following validation layers:

### 1. Structural Validation
* **Schema Validation:** Does the final model response match the `FinalResultSchema`?
* **Tool-result Validation:** Did all required steps in the plan actually execute and return a success status?

### 2. Policy Validation
* Were any required approvals bypassed? (Checked cryptographically or via SQLite constraints).
* Did the model attempt to reference artifacts that were never created?

### 3. Task-Specific Completion Criteria
* Tasks can optionally include deterministic completion validators (e.g., "Is the output valid JSON?" or "Does the specified file now exist?"). In Phase 2B (read-only), this usually means verifying that a specific artifact or summary was durably saved.

## Review Mechanisms

### Model Self-Review (Phase 2B)
Before submitting the final result, the worker prompts the model with its own work:
> "You were asked to do X. You executed steps Y. This is your drafted result Z. Does Z fully satisfy X? Reply with {"complete": true} or {"complete": false, "reason": "..."}."

If the model says `false`, it enters a replan loop.

### Independent Review (Deferred)
In future phases, a separate LLM call (or a different, larger model) will act as a reviewer agent to evaluate the worker's output against the original directive.

### Human Review
If the worker cannot reach confidence, or if the task was flagged as high-importance, the system transitions the task to `UnderReview` instead of `Completed`. A human must approve the final result.

## Handling Malformed and Unsupported Tasks

* **Error Detection:** If a model repeatedly fails structural validation (e.g., 3 consecutive failures to produce valid JSON), the worker stops retrying and marks the task `Failed`, leaving an audit trail of the raw model outputs for debugging.
* **Unsupported Claims:** If the model's final result relies on hallucinated data (e.g., claiming it read a file when the tool execution log shows it did not), the Validator rejects the completion.
* **Ambiguous Tasks:** If the user asks for something impossible (e.g., "Mow my lawn"), the model should use the `mark_unsupported` tool. The task transitions to `Blocked` or `Failed` with a clear message to the user.

## Examples

* **Code-analysis task (Success):** User asks to find unused imports. Model plans `list_files`, then `read_file` on 3 Python files. Model generates a JSON list of unused imports. Structural validation passes. Task `Completed`.
* **Unsupported action request (Blocked):** User asks to delete a database. Model recognizes this violates its instructions. Model calls `mark_unsupported("Cannot delete databases")`. Task `Blocked`.
* **Hallucination failure (Failed):** User asks to summarize a file. Model hallucinates the contents without calling `read_file`. The Validator sees the final result submitted without the required tool execution in the audit trail. Validation fails. Task retries, eventually `Failed`.
