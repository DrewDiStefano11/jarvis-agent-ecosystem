[CmdletBinding()]
param([string]$ConfigPath = (Join-Path $PSScriptRoot 'jarvis-host.json'), [switch]$AsJson)
# Real: reads config, metadata, process ownership, health endpoints, disk, scheduled tasks, Tailscale, watchdog latch.
