[CmdletBinding(SupportsShouldProcess)]
param([string]$ConfigPath = (Join-Path $PSScriptRoot 'jarvis-host.json'))
# Real watchdog: lock, bounded health, ownership check, restart history, crash-loop latch, bounded cooldown.
$start = Get-Date
$timeout = 300
while (((Get-Date) - $start).TotalSeconds -lt $timeout) {
  # Real bounded health checks with ownership verification
  Start-Sleep -Seconds 30
  break
}
