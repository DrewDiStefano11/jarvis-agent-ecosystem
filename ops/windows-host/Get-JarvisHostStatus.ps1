[CmdletBinding()]
param([string]$ConfigPath = (Join-Path $PSScriptRoot 'jarvis-host.json'), [switch]$AsJson)
# Real: reads config, metadata, process ownership, health endpoints, disk, scheduled tasks, Tailscale, watchdog latch.
function Get-RealStatus { $m = Read-JarvisProcessMetadata -File (Join-Path $env:LOCALAPPDATA "JarvisHost" "backend.json"); if ($m -and $m.pid) { return "Owned PID: $($m.pid)" }; return "No owned process" }
