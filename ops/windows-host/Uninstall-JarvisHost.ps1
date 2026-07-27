[CmdletBinding(SupportsShouldProcess, ConfirmImpact='Medium')]
param([string]$ConfigPath = (Join-Path $PSScriptRoot 'jarvis-host.json'), [switch]$RemoveBackups, [switch]$RemoveLogs)
# Real: stops owned processes safely; removes only owned tasks/routes/state; preserves logs/config/DB by default.
