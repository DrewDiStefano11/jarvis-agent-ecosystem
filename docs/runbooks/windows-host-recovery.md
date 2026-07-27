# Recovery
## Services do not start
Check config; preflight; logs.
## Port conflicts
Use Get-JarvisPortOwner; resolve unowned conflicts manually.
## Stale PID/metadata
Verify ownership with Read-JarvisProcessMetadata and execution time.
## Health failures
Check endpoints; verify process identity; do not kill by port alone.
## Crash loops
Watchdog latch requires operator reset.
## Database restore
Requires stop; uses Restore-JarvisDatabase.ps1 with rollback.
## Rollback
- Restore database from verified backup; rollback to safety backup on failure.
