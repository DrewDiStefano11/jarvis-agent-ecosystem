# Migrations

Alembic migrations are the authoritative schema history. Application startup may run Alembic upgrades when configured, but it must never substitute `Base.metadata.create_all()` for migrations.

Revision `20260720_01` creates the frozen Phase 2A durable control-plane schema for simulator state, audit history, transactional outbox, idempotency records, workflow runs, and checkpoints.

Revision `20260723_02` adds worker registrations, unique active task leases, fenced lease tokens, and task-attempt history after the durable control-plane baseline.

Revision `20260724_03` adds durable context assemblies after the task-lease revision, with a task foreign key, unique canonical input hash, request/policy/status fields, security counters, and the validated redacted payload. Upgrade, downgrade-to-Phase-2A, and re-upgrade are covered against isolated temporary databases.

Revision `a87a487dd714` adds the normalized agent identity and authorization schema. It creates identities, ranks, roles, permissions, capabilities, teams, time-bounded assignments, supervisor relationships, delegation and approval-authority boundaries, typed resource access, seat priority policies, and append-only identity audit events. It does not alter the simulator compatibility tables and is reversible.

Revision `20260729_04` adds the durable agent-runtime SQL control plane. It widens audit/outbox correlation identifiers to the shared 120-character exact-preservation limit and creates runtime run, event, attempt, checkpoint, and processed-command tables. Its downgrade checks correlation-ID representability before any destructive DDL so unsafe narrowing leaves runtime tables and existing rows intact.

Revision `20260729_05` adds one focused `model_executions` table for the Phase 2C staged protocol and validated planning/review result. A unique `(runtime_run_id, runtime_attempt_id)` constraint protects one committed result per runtime attempt; recovery, task, worker, context, and result lookup indexes are explicit. Downgrade to `20260729_04` is allowed only while the table is empty because committed execution results are not representable at the older revision.

To intentionally start clean in development, stop the API, back up anything needed, and delete the database plus its `-wal` and `-shm` sidecars from `apps/api/data`; then run `python -m alembic upgrade head`. This destroys local durable state and should never be automated against an uncertain path. Do not edit SQLite tables manually.
