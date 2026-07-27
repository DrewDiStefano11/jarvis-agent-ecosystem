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
