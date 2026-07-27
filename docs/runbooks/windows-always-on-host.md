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
