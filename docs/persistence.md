# Persistence

Phase 2A uses SQLite because it is durable, transactional, bundled with Python, and requires no service for normal local Windows development. The default database is `apps/api/data/jarvis.db`; `data/`, `*.db`, and SQLite sidecars are ignored by Git.

SQLAlchemy 2 typed models store scalar identity, state, timestamps, and foreign keys in columns. Validated JSON columns hold structures that gain little from normalization: agent capabilities/tool policies/performance/resource/office metadata, complete Pydantic payloads used by the compatibility layer, event envelopes, checkpoint maps, and miscellaneous metadata. Lists are never comma-separated. Task-agent, dependency, and blocker relationships use association tables.

Every connection enables foreign keys, WAL, a five-second busy timeout, and a bounded lifecycle. Repository methods do not expose SQL to routes. `UnitOfWork` documents the command transaction boundary. Normal services append audits; no application update/delete operation is exposed for audit rows.

SQLite is suitable for one local control-plane process. PostgreSQL can later replace the SQLAlchemy repository/session implementation while retaining Pydantic contracts and repository protocols.

Failed outbox rows remain stored after their configured retry ceiling. Health exposes an exhausted count and degrades while any exhausted record remains; dispatcher selection excludes those rows without deleting their audit evidence.

Phase 2B extends the same database and transaction model with `workers`, `task_leases`, and `task_attempts`. `task_leases.task_id` is the unique active-ownership boundary; each acquisition creates an immutable attempt and a random fencing token. SQLite `BEGIN IMMEDIATE` serializes claim selection, while guarded task-state updates and unique constraints prevent double acquisition. Priority ordering is urgent-to-low, then FIFO and task ID; incomplete `requires` dependencies exclude a task.

Lease acquisition, renewal, release, completion, failure, cancellation, and expiration each commit the task row/payload, attempt record, append-only audit, system sequence, and outbox envelope together. Routes contain no SQL. Lease tokens are treated as capabilities and are not stored in emitted payloads beyond a SHA-256 fingerprint. Short write transactions, WAL, and the existing busy timeout bound contention for concurrent local workers.

Back up only while the API is stopped, or use a SQLite-aware backup tool. Never copy only a live `.db` file while WAL sidecars may contain committed pages. Do not store credentials in this database.
