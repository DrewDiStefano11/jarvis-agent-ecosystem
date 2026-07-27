# Windows Always-On Host
## Prerequisites
- Windows 10/11, PowerShell 5.1+
## Configuration
Edit jarvis-host.json; validate with Import-JarvisHostConfig.
## Installation
Run Install-JarvisHost.ps1; creates tasks.
## Startup and Supervision
Start/Restart/Stop with verified process ownership.
## Watchdog
Invoke-JarvisWatchdog.ps1 with crash-loop latch.
## Backup
Backup-JarvisDatabase.ps1 with WAL-safe SQLite backup.
## Restore
Restore-JarvisDatabase.ps1 requires confirmation.
## Tailscale
Private Serve only; no Funnel; ACL recommendations.
## Security Limitations
Private network boundary only; no application-level auth.
## Manual Verification
Run Test-JarvisHost.ps1; verify process ownership; inspect logs.
## Manual Windows Verification Checklist
- Run Install-JarvisHost.ps1 with -WhatIf; confirm no mutation.
- Verify scheduled tasks with Get-ScheduledTask.
- Inspect PID metadata in %LOCALAPPDATA%\JarvisHost.
- Confirm loopback bindings only.
## Exact Commands
Install: ops/windows-host/Install-JarvisHost.ps1 -ConfigPath jarvis-host.json
Start: ops/windows-host/Start-JarvisHost.ps1 -ConfigPath jarvis-host.json
Stop: ops/windows-host/Stop-JarvisHost.ps1 -ConfigPath jarvis-host.json
Restart: ops/windows-host/Restart-JarvisHost.ps1 -ConfigPath jarvis-host.json
Status: ops/windows-host/Get-JarvisHostStatus.ps1 -ConfigPath jarvis-host.json -AsJson
Watchdog: ops/windows-host/Invoke-JarvisWatchdog.ps1 -ConfigPath jarvis-host.json
Backup: ops/windows-host/Backup-JarvisDatabase.ps1 -ConfigPath jarvis-host.json
Restore: ops/windows-host/Restore-JarvisDatabase.ps1 -BackupPath backups/jarvis.db -ConfigPath jarvis-host.json
Tailscale configure: ops/windows-host/Configure-JarvisTailscale.ps1 -ConfigPath jarvis-host.json
Tailscale remove: ops/windows-host/Remove-JarvisTailscale.ps1
Uninstall: ops/windows-host/Uninstall-JarvisHost.ps1 -ConfigPath jarvis-host.json
Consistent parameters verified across all 12 scripts
## Manual Windows Verification Checklist (Mandatory Before Ready)
- [ ] Run Install-JarvisHost.ps1 with -WhatIf; confirm zero mutation and correct task names.
- [ ] Verify scheduled tasks exist and contain absolute paths; remove unrelated tasks not affected.
- [ ] Confirm log, backup, state directories exist and are writable.
- [ ] Inspect jarvis-host.json; validate required fields.
- [ ] Run Start-JarvisHost.ps1 with -WhatIf; confirm no mutation.
- [ ] Start host on real machine; verify backend health check passes; verify frontend health passes.
- [ ] Confirm PID metadata files contain executable, arguments, working directory, process start time, instance ID, timestamp.
- [ ] Confirm no unrelated process is killed during Stop.
- [ ] Confirm Restart creates optional backup, stops safely, verifies health, reports partial failures.
- [ ] Confirm Watchdog acquires exclusive lock, checks health independently, verifies process ownership, records restart history, sets crash-loop latch, exits with meaningful code, releases lock.
- [ ] Confirm Backup uses Python sqlite3, writes temporary file, verifies integrity, atomically renames, cleans temporary files, writes structured metadata, applies retention inside backup directory only.
- [ ] Confirm Restore requires services stopped, creates verified safety backup, restores temporarily, verifies, atomically replaces, verifies after replace, rolls back on failure.
- [ ] Confirm Tailscale Configure reads CLI syntax, preserves existing routes, writes toolkit manifest, applies only toolkit routes.
- [ ] Confirm Remove-Tailscale removes only toolkit-owned routes; unrelated routes preserved.
- [ ] Confirm Status reports healthy/degraded/stopped/unsafe/misconfigured classifications; includes process ownership, health, ports, tasks, disk, Tailscale.
- [ ] Confirm Diagnostics archive excludes database, .env, secrets, tokens; includes redacted config, status, recent logs, manifest.
- [ ] Confirm Uninstall stops owned processes, removes owned tasks/routes/state; preserves database/config/logs/backups by default; deletes preserved data only with explicit switches; supports -WhatIf.
- [ ] Confirm no public firewall rules created; backend/frontend bound to localhost; no Tailscale Funnel enabled.
