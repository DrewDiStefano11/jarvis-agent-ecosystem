# Migrations

Alembic is authoritative. Application startup never calls `create_all`; development startup upgrades to head only when `JARVIS_AUTO_MIGRATE=true` (the default). Production-like runs may disable that setting and migrate explicitly.

From PowerShell:

```powershell
Set-Location apps/api
python -m alembic upgrade head
python -m alembic current
python -m alembic history
```

Initial revision `20260720_01` creates departments, agents, tasks, approvals, artifacts, notifications, append-only audits, system state, workflow runs/checkpoints, outbox, idempotency, and task association tables. CI upgrades a blank temporary database. Tests never use the developer database.

Revision `20260723_02` adds durable context assemblies with a task foreign key, unique canonical input hash, request/policy/status fields, security counters, and the validated redacted payload. Upgrade, downgrade-to-base, and re-upgrade are covered against isolated temporary databases.

To intentionally start clean in development, stop the API, back up anything needed, and delete the database plus its `-wal` and `-shm` sidecars from `apps/api/data`; then run `python -m alembic upgrade head`. This destroys local durable state and should never be automated against an uncertain path. Do not edit SQLite tables manually.
