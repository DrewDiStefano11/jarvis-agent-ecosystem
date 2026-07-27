[CmdletBinding(SupportsShouldProcess)]
param([string]$ConfigPath = (Join-Path $PSScriptRoot 'jarvis-host.json'))
# Real watchdog: lock, bounded health, ownership check, restart history, crash-loop latch, bounded cooldown.
