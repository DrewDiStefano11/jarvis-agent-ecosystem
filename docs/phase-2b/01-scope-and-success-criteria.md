# 01 - Scope and Success Criteria

This document defines the exact boundaries for Phase 2B. Phase 2B transforms the system from a simulated environment into the first useful, autonomous, locally powered runtime capable of unattended work.

## Goals

At minimum, Phase 2B must deliver:
* **Headless local worker:** A process that continually polls the database for tasks and works autonomously in the background without UI interaction.
* **Local-model provider abstraction:** An interface that decouples the application from a specific LLM, providing generic OpenAI-compatible local endpoint support.
* **Ollama or LM Studio compatibility:** Configurable support for the major local model hosting tools.
* **Persistent worker process:** The worker loop runs continuously and reliably.
* **Atomic task claiming:** Safe, race-condition-free claiming using SQLite.
* **Worker leases:** Time-bound ownership of tasks to handle stale processes or crashes.
* **Restart recovery:** Seamless resumption of execution from the last checkpoint upon restart.
* **Step-level checkpointing:** Durable states recorded between tool executions to prevent repeating work.
* **Retry handling:** Robust handling of transient errors and failures.
* **Structured model responses:** Consistent communication using JSON schemas.
* **Safe read-only internal capabilities:** Initial tools restricted to reading approved repository paths and application status.
* **Auditing:** Complete, append-only records of worker decisions, model planning, and actions.
* **Health reporting:** System observability for the worker and provider states.
* **Graceful shutdown:** Interruption triggers safe pauses without state corruption.
* **Basic resource controls:** Preventing the model from consuming all local memory/CPU indefinitely.
* **Test provider:** A deterministic fake model provider for rapid testing.
* **Manual task submission:** Using the existing Phase 2A backend to enqueue tasks.

## Non-goals

Phase 2B explicitly excludes:
* Full multi-agent delegation (delegation remains simulated or deferred).
* Unrestricted shell execution or general desktop control.
* Automatic email sending or calendar modifications.
* Browser automation or web scraping capabilities.
* Financial tools or transactions.
* Automatic GitHub merges, Git pushes, or other network mutations.
* Self-modifying production code or dynamic privilege expansion.
* Remote multi-user access or multi-machine workers.
* Production cloud deployment (e.g., Kubernetes, serverless).
* Voice control or major office animation UI changes.
* Advanced long-term memory (vector databases, graph knowledge).
* Unlimited autonomous operation without bounded constraints.

## Definition of Useful Autonomy

The system achieves "useful supervised autonomy" when a user can queue multiple read-only code analysis or summarization tasks, walk away, and return later to find that the local worker has successfully claimed the tasks, formulated a plan, read the necessary local files, generated the resulting artifact, checkpointed progress, handled transient local-server timeouts, and gracefully waited if an action required explicit approval, all without corrupting the durable SQLite state.

## Definition of Done

Phase 2B is considered complete when:
1. The headless worker process runs independently of the frontend.
2. A task queued via API is claimed automatically and executed via a local model.
3. Tests prove that killing the worker process mid-execution and restarting it safely resumes work from the last durable checkpoint.
4. The system reliably rejects undefined tools or actions outside the permitted repository boundaries.
5. A deterministic unattended integration test passes without human intervention.
6. A health endpoint accurately reflects the worker's status and queue depth.

## Explicit Limitations

Because Phase 2B focuses on early autonomy, users must still supervise the system. Specifically:
* The system relies heavily on the user for complex multi-step execution requiring context beyond the context window.
* Users must manually approve any risky actions via the UI or API before the worker will proceed.
* The system does not possess self-healing capabilities for unexpected environment failures (e.g., missing dependencies). The user must diagnose environment-level issues.
* The worker operates strictly on a single local machine using a single SQLite database.
