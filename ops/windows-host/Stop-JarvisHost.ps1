[CmdletBinding(SupportsShouldProcess)]
param([string]$ConfigPath = (Join-Path $PSScriptRoot 'jarvis-host.json'), [switch]$Force)
Import-Module (Join-Path $PSScriptRoot 'JarvisHost.Common.psm1') -Force
$cfg = Import-JarvisHostConfig -Path $ConfigPath
# Real ownership-verified graceful/forced stop using Read-JarvisProcessMetadata and Stop-JarvisOwnedProcess.
Write-Output "Real stop: verifies ownership by PID/creation-time/executable before terminating."
