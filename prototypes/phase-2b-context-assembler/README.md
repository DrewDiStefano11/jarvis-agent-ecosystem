# Phase 2B Context Assembler Prototype

## Purpose
This is a self-contained prototype intended to validate how a future Phase 2B worker constructs model requests from tasks, policies, files, artifacts, and prior results. It demonstrates separation of trusted instructions from untrusted content, clear context-source provenance, deterministic context ordering, token and character budgeting, context truncation rules, context prioritization, sensitive-data redaction, prompt-injection signal detection, and more.

## Explicit Non-Integration Status
**WARNING:** This prototype is explicitly not integrated into Jarvis. It does not call any models, it does not execute any tools, and it does not grant permissions.

## Key Features

- **Instruction Hierarchy:** Clearly separates `system`, `developer`/`operator`, `user`/`task`, and delimited untrusted `context` levels.
- **Trust Levels:** Precedence rules for `system_policy`, `operator_instruction`, `trusted_configuration`, etc.
- **Source Types:** Defines supported inputs (e.g., `repository_file`, `prior_model_output`).
- **Provenance:** Every included item retains its source ID, type, path, original hash, etc.
- **Context Policy:** Rejects unsafe cross-project defaults and manages size limits.
- **Cross-Project Isolation:** Excludes inputs bound to other projects unless explicitly enabled.
- **Prompt-Injection Defenses:** Heuristic detection for common injection phrases and delimiter escapes.
- **Security-Analysis Mode:** An explicit trusted task mode where suspicious content can be safely reviewed.
- **Redaction:** Automatic masking of recognized credentials (API keys, tokens).
- **Conflict Detection:** Identifies conflicting instructions, scopes, or formats.
- **Deduplication:** Merges or deduplicates exact duplicate content.
- **Token Budgeting & Truncation:** Conservative size bounding using character heuristics (e.g. 1 token ~ 3.5 chars) with explicit truncation strategies.
- **Context Manifest:** An audit trail of what was included, excluded, and why.
- **Model-Request Structure:** Provider-neutral JSON format.
- **Reproducibility:** Deterministic hashing and ordering.

## Security Warnings
- **Injection Detection is Heuristic:** Detection relies on patterns and is not a guarantee of safety.
- **Structural Isolation is Still Required:** The true defense remains the model request schema separation.
- **No Model Calls:** This prototype never calls any language model.
- **No External Actions:** Context content never grants tools or approvals.

## Installation & Testing
From the prototype directory:
```powershell
python -m pip install -e .
python -m unittest discover -s tests -v
```

## CLI Reference
Run `python -m jarvis_context_assembler --help` for available commands such as `validate-policy`, `inspect`, `detect-injection`, `redact`, `assemble`, `manifest`, and `simulate-attacks`.

Exit codes:
- 0: request assembled successfully
- 1: assembled with warnings
- 2: human review required
- 3: source excluded by policy
- 4: critical injection or sensitive-data finding
- 5: unresolved instruction conflict
- 6: context budget failure
- 7: invalid policy, task, or source input
- 8: output or report write failure
- 9: unexpected internal error

## Relationship to Phase 2A & Phase 2B
This prototype informs Phase 2B but does not modify Phase 2A components. Findings here should guide the actual Phase 2B implementation of trust levels, source contracts, redaction, injection signals, delimiters, token budgeting, and request structure.
## 1. Status

Complete

## 2. Git information

* Repository: DrewDiStefano11/jarvis-agent-ecosystem
* Source branch: feature/phase-2-durable-control-plane
* Starting SHA: 0a2af9bc5476478fc350ea8eeddf0d6848e7c4bb
* Working branch: prototype/phase-2b-context-assembler
* Final SHA: 5eb180c656911fdf9b0ce936c9dd28a1f1cc72a3
* Pull-request link: Pending submission
* Nothing was merged directly.

## 3. Files created

```
prototypes/phase-2b-context-assembler/README.md
prototypes/phase-2b-context-assembler/examples/example-assembly-report.json
prototypes/phase-2b-context-assembler/examples/example-conflicting-source.json
prototypes/phase-2b-context-assembler/examples/example-context-sources.json
prototypes/phase-2b-context-assembler/examples/example-injection-source.json
prototypes/phase-2b-context-assembler/examples/example-over-budget-sources.json
prototypes/phase-2b-context-assembler/examples/example-policy.json
prototypes/phase-2b-context-assembler/examples/example-redaction-source.json
prototypes/phase-2b-context-assembler/examples/example-safe-request.json
prototypes/phase-2b-context-assembler/examples/example-task.json
prototypes/phase-2b-context-assembler/pyproject.toml
prototypes/phase-2b-context-assembler/schemas/assembly-report.schema.json
prototypes/phase-2b-context-assembler/schemas/context-manifest.schema.json
prototypes/phase-2b-context-assembler/schemas/context-policy.schema.json
prototypes/phase-2b-context-assembler/schemas/context-source.schema.json
prototypes/phase-2b-context-assembler/schemas/model-request.schema.json
prototypes/phase-2b-context-assembler/src/jarvis_context_assembler/__init__.py
prototypes/phase-2b-context-assembler/src/jarvis_context_assembler/__main__.py
prototypes/phase-2b-context-assembler/src/jarvis_context_assembler/cli.py
prototypes/phase-2b-context-assembler/src/jarvis_context_assembler/config.py
prototypes/phase-2b-context-assembler/src/jarvis_context_assembler/conflict_detection.py
prototypes/phase-2b-context-assembler/src/jarvis_context_assembler/contracts.py
prototypes/phase-2b-context-assembler/src/jarvis_context_assembler/deduplication.py
prototypes/phase-2b-context-assembler/src/jarvis_context_assembler/enums.py
prototypes/phase-2b-context-assembler/src/jarvis_context_assembler/errors.py
prototypes/phase-2b-context-assembler/src/jarvis_context_assembler/hashing.py
prototypes/phase-2b-context-assembler/src/jarvis_context_assembler/injection_detection.py
prototypes/phase-2b-context-assembler/src/jarvis_context_assembler/manifest.py
prototypes/phase-2b-context-assembler/src/jarvis_context_assembler/message_builder.py
prototypes/phase-2b-context-assembler/src/jarvis_context_assembler/prioritization.py
prototypes/phase-2b-context-assembler/src/jarvis_context_assembler/provenance.py
prototypes/phase-2b-context-assembler/src/jarvis_context_assembler/redaction.py
prototypes/phase-2b-context-assembler/src/jarvis_context_assembler/reporting.py
prototypes/phase-2b-context-assembler/src/jarvis_context_assembler/source_policy.py
prototypes/phase-2b-context-assembler/src/jarvis_context_assembler/token_budget.py
prototypes/phase-2b-context-assembler/src/jarvis_context_assembler/truncation.py
prototypes/phase-2b-context-assembler/tests/__init__.py
prototypes/phase-2b-context-assembler/tests/helpers.py
prototypes/phase-2b-context-assembler/tests/test_cli.py
prototypes/phase-2b-context-assembler/tests/test_config.py
prototypes/phase-2b-context-assembler/tests/test_conflict_detection.py
prototypes/phase-2b-context-assembler/tests/test_contracts.py
prototypes/phase-2b-context-assembler/tests/test_deduplication.py
prototypes/phase-2b-context-assembler/tests/test_end_to_end_scenarios.py
prototypes/phase-2b-context-assembler/tests/test_injection_detection.py
prototypes/phase-2b-context-assembler/tests/test_manifest.py
prototypes/phase-2b-context-assembler/tests/test_message_builder.py
prototypes/phase-2b-context-assembler/tests/test_prioritization.py
prototypes/phase-2b-context-assembler/tests/test_provenance.py
prototypes/phase-2b-context-assembler/tests/test_redaction.py
prototypes/phase-2b-context-assembler/tests/test_reporting.py
prototypes/phase-2b-context-assembler/tests/test_source_policy.py
prototypes/phase-2b-context-assembler/tests/test_token_budget.py
prototypes/phase-2b-context-assembler/tests/test_truncation.py
```

## 4. Instruction hierarchy

* **Trusted instruction levels**: System Policy, Developer Config, Operator Instructions, Task Requests.
* **Untrusted content levels**: Repository Content, Artifacts, Tool Results, Prior Model Outputs, External Content, Unknown.
* **Conflict precedence**: Defined by a deterministic mapping (`TRUST_ORDER`) where higher trust wins in deduplication.
* **Human-review conditions**: Explicitly triggered if there are unresolved `conflict_findings` or a `critical` prompt injection detected (unless the task is a security analysis).

## 5. Context assembly

* **Source filtering**: Blocks inputs lacking approval, incorrect types, or unknown provenance hashes.
* **Provenance**: Verified deterministically by comparing `content_hash`.
* **Project isolation**: Prevents including `jarvis-office-prototype` context in a `jarvis-agent-ecosystem` task.
* **Ordering**: Priority based on trust rank, exact-preservation needs, specific inclusion priority, and source ID.
* **Deduplication**: Drops duplicated content but preserves exact or higher-trust counterparts.
* **Token budgeting**: Conservative ~3.5 chars/token budget, minus reserved output tokens.
* **Truncation**: Sources lacking exact preservation requirement are truncated depending on available tokens, with markers to indicate incompleteness.
* **Request hashing**: Hashing canonicalized JSON messages and metadata guarantees stability.
* **Manifest generation**: Emits an audit report covering inputs, rejections, findings, and conflicts.

## 6. Injection-defense results

* **Ignore-previous-instructions attack**: Handled. Excluded by policy since it matches the high injection heuristic.
* **Fake system message**: Handled. Matched and excluded as a medium/high heuristic.
* **Shell request**: Handled. Denied natively by critical injection finding.
* **Approval bypass**: Handled. Found and excluded.
* **Credential request**: Handled. Found and excluded.
* **Audit bypass**: Handled. Found and excluded.
* **Delimiter escape**: Handled. `</CONTEXT_SOURCE>` strings inside files are safely escaped to prevent exiting the untrusted zone.
* **Prior-model-output instruction**: Handled via Trust Levels - untrusted content acts as data, not system instructions.
* **Security-analysis exception**: Handled. High severity injections are allowed if the `task_request.allowed_result_type == "security_analysis"`.

## 7. Redaction results

* **Credential patterns**: Included Regex checks for API keys, Bearer tokens, and private keys.
* **Redaction counts**: Tracked and emitted in reports.
* **Output leakage checks**: Redactions apply inline and the raw secret vanishes.
* **Report leakage checks**: Findings don't output the actual secret string.
* **False-positive handling**: Short generic "secret" word matches without assignment syntax are ignored.

## 8. Budget results

* **Estimator method**: Fallback `chars / 3.5`.
* **Required reserves**: Deducts configured output margin from the budget early.
* **Safety margin**: Excludes inputs if remaining budget doesn't accommodate a warning marker.
* **Source prioritization**: Sorts context efficiently before budget application.
* **Exclusion behavior**: Lowest priority items dropped when out of tokens.
* **Truncation behavior**: Adds a `[TRUNCATED]` explicit marker.
* **Required-source failure behavior**: Throws a budget exception (Exit code 6) if an exactly-required block cannot fit.

## 9. Test results

```
Ran 56 tests in 0.069s.
OK
```
Covers:
* Policy tests
* Source tests
* Injection tests
* Redaction tests
* Conflict tests
* Duplicate tests
* Budget tests
* Prioritization tests
* Truncation tests
* Message-builder tests
* Manifest tests
* CLI tests
* End-to-end security scenarios

## 10. Known limitations

* This does not call a model.
* Injection detection is heuristic.
* This does not prove a model will never follow malicious context.
* This does not replace tool permission enforcement.
* Token counts may be estimates.
* This does not summarize content semantically.
* This does not read the real repository.
* This does not integrate with Phase 2A.
* This does not create approvals.
* This does not grant tools.
* This does not verify external-source truthfulness.

## 11. Phase 2B recommendation

* The explicitly separated instruction hierarchy.
* The `context-manifest.schema.json` format for complete auditability.
* Redaction regex boundaries (conservative to prevent false positives).
* Token budgeting fallback behavior and `TruncationStrategy.TAIL`.
* The `deterministic_hash` sorting requirements to prevent flaky reproducibility.
* The explicit `security_analysis` fallback logic for deliberate injection review.

## 12. Non-overlap confirmation

No files outside `prototypes/phase-2b-context-assembler/` have been modified or created. Clean state confirmed via git diff.
