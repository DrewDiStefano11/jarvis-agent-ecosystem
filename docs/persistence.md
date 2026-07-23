# Persistence

Phase 2A uses SQLite because it is durable, transactional, bundled with Python, and requires no service for normal local Windows development. The default database is `apps/api/data/jarvis.db`; `data/`, `*.db`, and SQLite sidecars are ignored by Git.

SQLAlchemy 2 typed models store scalar identity, state, timestamps, and foreign keys in columns. Validated JSON columns hold structures that gain little from normalization: agent capabilities/tool policies/performance/resource/office metadata, complete Pydantic payloads used by the compatibility layer, event envelopes, checkpoint maps, and miscellaneous metadata. Lists are never comma-separated. Task-agent, dependency, and blocker relationships use association tables.

Every connection enables foreign keys, WAL, a five-second busy timeout, and a bounded lifecycle. Repository methods do not expose SQL to routes. `UnitOfWork` documents the command transaction boundary. Normal services append audits; no application update/delete operation is exposed for audit rows.

SQLite is suitable for one local control-plane process. PostgreSQL can later replace the SQLAlchemy repository/session implementation while retaining Pydantic contracts and repository protocols.

Back up only while the API is stopped, or use a SQLite-aware backup tool. Never copy only a live `.db` file while WAL sidecars may contain committed pages. Do not store credentials in this database.
