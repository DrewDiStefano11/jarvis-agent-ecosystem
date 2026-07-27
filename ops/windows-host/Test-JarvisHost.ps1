[CmdletBinding()]
param([string]$ConfigPath = (Join-Path $PSScriptRoot 'jarvis-host.json'))
# Real non-mutating preflight/diagnostics with bounded checks and structured output.
