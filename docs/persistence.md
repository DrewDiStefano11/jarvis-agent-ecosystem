# Persistence

Phase 2A uses SQLite because it is durable, transactional, bundled with Python, and requires no service for normal local Windows development. The default database is `apps/api/data/jarvis.db`; `data/`, `*.db`, and SQLite sidecars are ignored by Git.

SQLAlchemy 2 typed models store scalar identity, state, timestamps, and foreign keys in columns. Validated JSON columns hold structures that gain little from normalization: agent capabilities/tool policies/performance/resource/office metadata, complete Pydantic payloads used by the compatibility layer, event envelopes, checkpoint maps, and miscellaneous metadata. Lists are never comma-separated. Task-agent, dependency, and blocker relationships use association tables.

`context_assemblies` stores task/project identity, deterministic input/request hashes, status, policy version, aggregate security counts, and the complete validated redacted payload. `input_hash` is unique. The task foreign key prevents orphaned context state. Recognized credentials are removed before persistence; raw sources are not stored in a second table.

Every connection enables foreign keys, WAL, a five-second busy timeout, and a bounded lifecycle. Repository methods do not expose SQL to routes. `UnitOfWork` documents the command transaction boundary. Normal services append audits; no application update/delete operation is exposed for audit rows.

SQLite is suitable for one local control-plane process. PostgreSQL can later replace the SQLAlchemy repository/session implementation while retaining Pydantic contracts and repository protocols.

Failed outbox rows remain stored after their configured retry ceiling. Health exposes an exhausted count and degrades while any exhausted record remains; dispatcher selection excludes those rows without deleting their audit evidence.

Context rows enter the same unit of work as audit, outbox, and terminal idempotency state. Metrics are recalculated from loaded rows and therefore survive restart without counter drift.

Back up only while the API is stopped, or use a SQLite-aware backup tool. Never copy only a live `.db` file while WAL sidecars may contain committed pages. Do not store credentials in this database.
