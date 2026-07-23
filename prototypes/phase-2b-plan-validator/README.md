# Phase 2B Plan Validator Prototype

## Purpose
This is a self-contained prototype intended to inform the future Phase 2B implementation after Phase 2A is finalized. It validates structured plans returned by a local language model before those plans can be considered executable.

**This prototype never executes plans.**

## Explicit Non-Integration Status
This prototype does not integrate with the current FastAPI application, simulator, database, frontend, worker runtime, or task system. It does not replace runtime permission enforcement and does not define the final Phase 2B database schema.

## Security Posture
Model output must be treated as hostile, malformed, incomplete, misleading, or unsafe until proven otherwise.
Validation does not make unrestricted tools safe. The validator relies on deterministic rules and bounded execution parameters to reject unsafe or unsupported inputs.

## Plan Contract Overview
A valid execution plan defines the objective, assumptions, completion criteria, final output, and a directed acyclic graph (DAG) of steps. The task envelope is authoritative over constraints (like maximum steps, allowed tools).

## Supported Validation
*   **Task-Envelope Authority:** Enforces permissions (e.g., allowed/denied tools, maximum retries) independent of the model's desires.
*   **Tool Registry:** Maps actions to risk levels and validation requirements (e.g., allowed paths).
*   **Approval Analysis:** Calculates whether a step (and thus the plan) requires human approval based on tool risk and action context.
*   **Graph Validation:** Detects cycles, missing dependencies, and duplicate steps.
*   **Path Validation:** Detects path traversal, absolute paths, and paths outside allowed workspace roots.
*   **Network Validation:** Blocks or limits URL accesses based on task constraints.

## Exit Codes
* `0` = valid and automatically executable
* `1` = valid but approval required
* `2` = valid but blocked or human review required
* `3` = invalid schema or malformed model response
* `4` = invalid dependency graph
* `5` = policy violation or prohibited action
* `6` = unsupported tool or step type
* `7` = configuration or task-envelope error
* `8` = report write failure
* `9` = unexpected internal error

## Installation
Preferably install using pip in your virtual environment:

```powershell
# From the prototype directory:
python -m pip install -e .
```

## Running Without Installation
```powershell
python -m jarvis_plan_validator --help
```

## Example Commands (PowerShell)

### Validate a plan
```powershell
python -m jarvis_plan_validator validate `
  --task-envelope examples/task-envelope.json `
  --model-response examples/valid-read-only-plan.json
```

### Strict JSON validation
```powershell
python -m jarvis_plan_validator validate `
  --task-envelope examples/task-envelope.json `
  --model-response examples/invalid-extra-prose-response.txt `
  --strict-json
```

### Validate a direct plan file
```powershell
python -m jarvis_plan_validator validate-plan `
  --task-envelope examples/task-envelope.json `
  --plan examples/valid-read-only-plan.json
```

### Inspect tool policy
```powershell
python -m jarvis_plan_validator tools
```

### Print schemas
```powershell
python -m jarvis_plan_validator schema --name execution-plan
```

### Produce JSON report
```powershell
python -m jarvis_plan_validator validate `
  --task-envelope TASK.json `
  --model-response RESPONSE.txt `
  --format json `
  --output validation-report.json
```

## Example Outcomes
* `valid-read-only-plan.json` -> 0 (valid, automatic)
* `invalid-path-traversal-plan.json` -> 5 (policy violation)
* `invalid-cycle-plan.json` -> 4 (graph error)

## Testing
Run the deterministic unit test suite with:
```powershell
python -m unittest discover -s tests -v
```

## Known Limitations
* This does not call a model.
* This does not execute plans.
* This does not guarantee a plan is semantically correct.
* This cannot guarantee real filesystem symlink containment (must be enforced at runtime).
* This does not approve real actions.

## Relationship to Phase 2A and Phase 2B
Phase 2A implements deterministic simulations with an SQLite control plane. Phase 2B will integrate real model output safely. This prototype informs Phase 2B by demonstrating how to build a robust safety boundary. Some components of this prototype may become shared contracts, runtime validation checks, or test fixtures in the future Phase 2B.
