# 06 - Planning and Execution Contracts

This document defines how the worker translates a durably stored task into executable steps using a language model. Because model output is untrusted input, the system mandates a strict, schema-driven interaction loop.

## The Planning Loop

1. **Context Assembly:** The worker collects the user's task description, system policy constraints, tool metadata, and previously completed steps (if recovering/replanning).
2. **Prompt Construction:** The payload is formatted for the Local Model Provider Contract (System prompt + User task).
3. **Model Request:** The worker asks the model to output a structured plan.
4. **Validation:** The JSON response is parsed and validated against `PlanSchema`.
5. **Execution:** The worker executes the plan step-by-step, taking checkpoints between each.

## Structured Schemas

### 1. Task Envelope (Internal Context)
Represents the state of the task passed into the planner.
```json
{
  "task_id": "task-123",
  "directive": "Analyze the codebase for hardcoded credentials.",
  "workspace_root": "simulated://workspace",
  "tools_available": ["read_file", "list_files"],
  "max_steps": 10
}
```

### 2. Model Planning Request
The payload sent to the LLM.
```json
{
  "messages": [
    {
      "role": "system",
      "content": "You are a safe, autonomous planner. You can only use the tools provided. Break the task into logical, sequential steps. Do not hallucinate tools. Output JSON."
    },
    {
      "role": "user",
      "content": "Task: Analyze the codebase for hardcoded credentials."
    }
  ],
  "response_format": { "type": "json_schema" }
}
```

### 3. Structured Plan Schema
The validated response expected from the LLM.
```json
{
  "plan_id": "plan-456",
  "steps": [
    {
      "step_id": "step-1",
      "tool": "list_files",
      "parameters": {
        "path": "simulated://workspace/src"
      },
      "expected_outcome": "A list of source files to analyze."
    },
    {
      "step_id": "step-2",
      "tool": "read_file",
      "parameters": {
        "path": "simulated://workspace/src/auth.py"
      },
      "dependencies": ["step-1"],
      "expected_outcome": "Contents of the auth file to inspect for secrets."
    }
  ]
}
```

### 4. Step Result Checkpoint
After a tool executes, the result is saved durably.
```json
{
  "step_id": "step-2",
  "status": "success",
  "output": "def login():\n    password = 'super_secret_password'\n...",
  "artifact_references": []
}
```

## Constraints and Handling Failures

* **Maximum Step Count / Plan Depth:** To prevent infinite loops, a plan cannot exceed a configurable maximum (e.g., 10 steps). If the task requires more, the model must output a partial plan and request a replan.
* **Maximum Retries:** If the model returns malformed JSON, the Validator rejects it. The worker retries the model request up to 3 times, appending the validation error to the prompt (`"Your previous output failed validation: ..."`).
* **Hallucinated Tools:** If the model requests `rm_rf` but it is not in `tools_available`, the Validator rejects the plan before any execution occurs.
* **Ambiguous or Unsupported Tasks:** The model is instructed to output a specific "blocked" tool/step if it cannot complete the task safely. The worker will transition the task to `Blocked` and notify the user.
* **Cancellation Points:** The worker checks for user-initiated task cancellation between every step execution.
* **Checkpoint Boundaries:** The worker durably commits to SQLite *before* calling the model, *after* receiving the plan, and *after* every tool execution.
* **Context Limits:** If the output of `read_file` is too large for the model's context window, the Executor truncates it, returns a truncation warning in the step result, and forces the model to replan using search tools or smaller reads.
