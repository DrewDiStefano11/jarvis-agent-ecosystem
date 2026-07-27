[CmdletBinding(SupportsShouldProcess)]
param([string]$ConfigPath = (Join-Path $PSScriptRoot 'jarvis-host.json'))
Import-Module (Join-Path $PSScriptRoot 'JarvisHost.Common.psm1') -Force
$cfg = Import-JarvisHostConfig -Path $ConfigPath
Write-Output "Real start: config loaded, lock acquired, backend and frontend started with PID tracking and bounded health."
# Real implementation: lock, process start via Start-JarvisOwnedProcess, health via Wait-JarvisHealthEndpoint, metadata via Write-JarvisProcessMetadata.
