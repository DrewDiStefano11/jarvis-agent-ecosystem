[CmdletBinding(SupportsShouldProcess)]
param([string]$ConfigPath = (Join-Path $PSScriptRoot 'jarvis-host.json'), [switch]$Force)
# Lock, optional backup, safe stop/start with health verification.
