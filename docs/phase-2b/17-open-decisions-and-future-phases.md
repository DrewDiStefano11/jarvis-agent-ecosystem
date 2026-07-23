# 17 - Open Decisions and Future Phases

This document lists design choices that were intentionally deferred or left open for the Phase 2B implementation. It separates decisions made for Phase 2B from possibilities intended for later phases.

## Phase 2B Open Decisions

### 1. Model Provider Strategy
* **Question:** Should we default strictly to Ollama, or build a generic OpenAI-compatible client first?
* **Options:**
  1. Ollama-first (easier local setup for many users).
  2. Generic OpenAI-first (supports LM Studio, vLLM, and actual OpenAI for debugging).
* **Recommended Phase 2B Choice:** Generic OpenAI-compatible client first, with configuration options to point to `localhost:11434/v1` (Ollama's OpenAI compatibility layer).
* **Reasoning:** Minimizes proprietary provider code. Ollama supports OpenAI API contracts natively now.
* **Trigger for Reconsideration:** If Ollama's OpenAI compatibility layer fails to support strict structured JSON schema outputs reliably.

### 2. Checkpoint Storage Format
* **Question:** Should checkpoints be stored as full JSON snapshots or as event-sourced diffs?
* **Options:**
  1. Full JSON snapshots at every boundary.
  2. Diffs/Events appending to the Phase 2A audit log.
* **Recommended Phase 2B Choice:** Full JSON snapshots (updating a `checkpoint` JSON column on the workflow/task run).
* **Reasoning:** Simpler to implement and reason about for restart recovery in a SQLite environment.
* **Trigger for Reconsideration:** When task state becomes so large that rewriting it every step causes SQLite latency.

### 3. Streaming vs. Non-Streaming Model Calls
* **Question:** Should the worker request streaming responses from the LLM?
* **Recommended Phase 2B Choice:** Non-streaming.
* **Reasoning:** We require structured, valid JSON before any execution can begin. Streaming JSON partials adds immense complexity to the Validator without providing value to a headless background worker.
* **Trigger for Reconsideration:** When UI requirements demand showing the user the model's "typing" process in real-time.

### 4. Maximum Task Duration
* **Question:** What is the hard timeout for a single task?
* **Recommended Phase 2B Choice:** 15 minutes.
* **Reasoning:** Local models can be slow. 15 minutes allows for several reads, replans, and generations without hanging indefinitely.
* **Trigger for Reconsideration:** User feedback indicates valid tasks are timing out.

## Deferred to Future Phases (Phase 2C and Beyond)

### When to introduce Git Tools (Commits, Pushes, Merges)
* **Decision:** Deferred.
* **Reasoning:** Git operations represent "Orange/Red" risk because they affect external systems or shared state. We must prove the "Green" read-only loop is stable in Phase 2B before trusting the model to create commits.

### When to introduce Multi-Agent Delegation
* **Decision:** Deferred.
* **Reasoning:** Phase 2B focuses on getting *one* worker to operate reliably. Multi-agent delegation requires complex inter-task dependencies, shared memory, and coordination that would delay the first milestone of useful autonomy.

### When PostgreSQL becomes justified
* **Decision:** Deferred.
* **Reasoning:** SQLite (with WAL mode and busy timeouts) is perfectly capable of handling a single Worker Manager and API process for a single user. PostgreSQL will be justified only when we require multiple load-balanced API servers or a swarm of distributed workers on different machines.

### When to introduce a Reviewer Agent
* **Decision:** Deferred.
* **Reasoning:** Phase 2B relies on structural validation and the model's own self-review. A separate Reviewer Agent requires doubling the inference cost/time and managing complex task-handoff state. This is slated for Phase 2C.

### Prompt Storage and Privacy
* **Decision:** Keep in memory.
* **Reasoning:** To avoid massive database bloat and potential leakage of sensitive file contents into logs, the raw assembled prompt is *not* durably stored. Only the tool execution summaries and generated plans are stored. A dedicated local vector database for long-term memory is deferred to Phase 3.
