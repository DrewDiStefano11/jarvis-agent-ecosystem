# 07 - Tool Security and Permission Model

This document defines the Phase 2B tool boundary. Phase 2B begins with a highly restrictive set of tools to ensure safety during unattended operation. The model cannot bypass the tool registry or alter its own permissions.

## Initial Tool Categories

### Safe Read-Only Capabilities (Implemented in Phase 2B)
These tools do not mutate the local environment and are generally considered "Green" risk.
* **`list_files`**: Lists files within approved workspace roots.
* **`read_file`**: Reads the contents of a specific file within an approved root.
* **`search_text`**: Greps for text within approved files.
* **`read_task_history`**: Reads previous audits or task results from the database.

### Controlled Write Capabilities (Deferred or Highly Sandboxed)
These tools are introduced late in Phase 2B or in Phase 2C, requiring strict constraints.
* **`write_artifact`**: Writes text to an explicitly approved output directory (e.g., `data/artifacts/`).
* **`create_patch_proposal`**: Generates a git diff but does *not* apply it.

### Prohibited Capabilities (Strictly Forbidden in Phase 2B)
* Arbitrary shell commands (`bash`, `cmd`, `PowerShell`).
* Package installation (`pip install`, `npm install`).
* System configuration changes.
* Credential access or arbitrary environment variable reads.
* Browser control or web scraping.
* Email sending or calendar mutation.
* Git pushes or merges.
* File deletion outside of a temporary sandbox.
* Network access to unapproved external hosts.
* Privilege escalation of any kind.

## Tool Registry and Execution Boundaries

The system maintains a centralized `ToolRegistry`. Every tool must define:
1. **Input Schema:** A Pydantic model defining valid arguments.
2. **Output Schema:** A defined return structure.
3. **Risk Level:** A static property defining the baseline risk (see Document 08).
4. **Execution Logic:** The isolated Python function that performs the work.

### Execution Constraints

* **Workspace Roots:** Tools like `read_file` are restricted to a `workspace_root` (e.g., the specific repository being analyzed).
* **Path Normalization:** All file paths must be normalized and checked against the root to prevent path traversal (e.g., `../../etc/passwd`).
* **Symbolic-link Handling:** Tools must refuse to follow symlinks that point outside the approved workspace root.
* **File-size Limits:** File reads are capped (e.g., 1MB) to prevent memory exhaustion and context window overflow.
* **Execution Timeouts:** Every tool execution is wrapped in a timeout (e.g., 5 seconds) to prevent hangs.
* **Prompt-injection Resistance:** Tool outputs fed back into the model must be sanitized or wrapped in strict XML-style delimiters to prevent the model from interpreting file contents as new instructions.
* **Denied-tool Handling:** If a model hallucinates a tool name, the Executor intercepts it and returns a standard `TOOL_NOT_FOUND` error to the model, forcing a replan.

## Threat Table: Tool Execution

| Threat | Likelihood | Impact | Existing Phase 2A Control | Required Phase 2B Control |
|---|---|---|---|---|
| Model requests execution of `rm -rf /` | High (Hallucination) | Critical | N/A (Simulated) | Tool Registry rejects unknown tools. |
| Path Traversal (`../../../secret`) | Medium | High | N/A | Path normalization and root bound checks in the `read_file` tool. |
| Symlink escape | Low | High | N/A | Symlink resolution checks against `workspace_root`. |
| Out of memory via massive file read | Medium | Low | N/A | Hardcoded file-size limit in `read_file`. |
| Prompt injection via source code read | Medium | Medium | N/A | Wrap tool outputs in `<content>` tags; rely on structured JSON model. |
