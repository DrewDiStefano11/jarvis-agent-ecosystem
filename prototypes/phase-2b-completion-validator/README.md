# Phase 2B Completion Validator Prototype

## Prototype Purpose
This is a self-contained prototype that determines whether a worker’s claimed result actually satisfies the original task requirements. It demonstrates how Jarvis can prevent a worker or model from marking work complete merely because it generated an answer.

## Explicit Non-Integration Status
This prototype is an isolated implementation. It is **not integrated** into Jarvis Phase 2A. It does not call any models, modify real task state, execute tools, run real project tests, inspect the actual repository, or perform full semantic-quality review.

## Why Worker Self-Declared Completion is Insufficient
A task is not complete because the model says it is, because an artifact exists, or because no exception occurred. It is complete only when authoritative completion criteria are satisfied, required outputs/evidence exist, and no blocking errors, pending approvals, or prohibited actions remain.

## Authoritative vs. Untrusted Inputs
* **Authoritative Inputs**: Task envelopes, completion criteria, required artifact definitions, policy versions, approval states, explicitly supplied trusted checks (like test results), and deterministic operator decisions.
* **Untrusted Inputs**: Worker success claims, model confidence, unverified model claims of test passing, unverified file existence, model claims of safety, unverified source support.

## Core Concepts
* **Completion Criteria**: Can be content, artifact, format, schema, test, policy, approval, manual review, etc.
* **Evidence Trust Levels**: Includes authoritative, trusted validator, trusted tool, operator, worker claim, model claim, etc.
* **Artifact Validation**: Verifies metadata matching. Does not cryptographically verify external files here.
* **Approval Validation**: Checks for missing, expired, rejected, or improperly scoped approvals.
* **Trusted Checks**: Ensures explicitly supplied test suites or receipts are correctly applied.
* **Contradiction Detection**: Detects deterministic mismatches (e.g. claimed "completed" but with an empty/placeholder artifact).
* **Unsupported Claims**: Detects claims unsupported by provided evidence (e.g., claiming "all tests passed" without a trusted test receipt).
* **Recommendation Rules**: Returns deterministic outcomes like `accept`, `accept_with_warnings`, `request_revision`, `retry_step`, `replan_task`, `request_approval`, `block`, `human_review`, and `reject`.
* **Score Limitations**: The prototype calculates a deterministic score (0-100), but scoring *does not override* gating. A critical policy violation forces a rejection regardless of the score.
* **Manual-Review Gates**: Some criteria explicitly require manual review and cannot be auto-accepted.
* **Task-Type Profiles**: Customizable checks based on task types (documentation, patch-proposal, etc.).

## Known Limitations
* This does not call a model.
* This does not perform full semantic-quality review.
* This does not verify external sources.
* This does not run real project tests.
* This does not inspect the actual repository.
* This does not integrate with Phase 2A.
* This does not authenticate trusted evidence cryptographically.
* This does not replace human review for subjective tasks.
* This does not change real task state.

## Relationship to Phase 2A & How Findings Inform Phase 2B
This prototype defines contracts and logic meant to eventually be integrated into Phase 2B, showing how the durable control plane from 2A will interact with output validation.

## Warning
Deterministic validation cannot fully judge semantic quality. A high score does not override policy or approval failures.

## Installation
```powershell
python -m pip install -e .
```

## CLI Reference & Windows PowerShell Examples

```powershell
# Validate full result
python -m jarvis_completion_validator validate `
  --task examples/task-envelope-example.json `
  --result examples/valid-complete-result.json `
  --artifacts examples/valid-complete-artifacts.json

# Validate incomplete result
python -m jarvis_completion_validator validate `
  --task examples/task-envelope-example.json `
  --result examples/incomplete-result.json

# Show criterion status
python -m jarvis_completion_validator criteria `
  --task examples/task-envelope-example.json `
  --result examples/valid-complete-result.json `
  --artifacts examples/valid-complete-artifacts.json

# Validate artifacts only
python -m jarvis_completion_validator artifacts `
  --task examples/task-envelope-example.json `
  --artifacts examples/valid-complete-artifacts.json

# Inspect findings
python -m jarvis_completion_validator findings `
  --task examples/task-envelope-example.json `
  --result examples/contradictory-result.json

# Produce JSON report
python -m jarvis_completion_validator validate `
  --task TASK.json `
  --result RESULT.json `
  --artifacts ARTIFACTS.json `
  --format json `
  --output completion-report.json

# Print schemas
python -m jarvis_completion_validator schema --name completion-report
```

## Exit Codes
* 0 = accepted
* 1 = accepted with warnings
* 2 = revision requested
* 3 = retry step
* 4 = replan required
* 5 = approval required
* 6 = blocked
* 7 = human review required
* 8 = rejected
* 9 = invalid input or schema
* 10 = report write failure
* 11 = unexpected internal error

## Testing
Run tests securely without network access:
```powershell
python -m unittest discover -s tests -v
```

## Example Scenarios (To Be Documented In Full)
Scenarios covered in test fixtures and E2E include:
1. Fully complete result (accept)
2. Complete with warnings (accept_with_warnings)
3. Missing required artifact (request_revision)
4. Worker claims tests passed without evidence (request_revision/retry)
5. Trusted test failure (retry_step/request_revision)
6. Fundamental output mismatch (replan_task)
7. Approval pending (request_approval)
8. Approval bypass attempt (reject)
9. Required external input missing (block)
10. Manual review criterion (human_review)
11. Contradictory result (request_revision/reject)
12. Wrong task artifact (reject)
13. Placeholder-only document (request_revision)
14. Policy violation (reject)
