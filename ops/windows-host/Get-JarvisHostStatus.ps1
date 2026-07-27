[CmdletBinding()]
param([string]$ConfigPath = (Join-Path $PSScriptRoot 'jarvis-host.json'), [switch]$AsJson)
Import-Module (Join-Path $PSScriptRoot 'JarvisHost.Common.psm1') -Force
$cfg = Import-JarvisHostConfig -Path $ConfigPath
$metaFile = Join-Path $cfg.stateDirectory 'backend.json'
$meta = Read-JarvisProcessMetadata -Path $metaFile
$status = @{ configValid = $true; backendHealth = (Test-JarvisHealthEndpoint -Url $cfg.backendHealthUrl -TimeoutSec $cfg.healthTimeoutSec); processOwned = if ($meta.pid) { Test-JarvisProcessOwnership -PID $meta.pid -ExpectedExecutable $meta.executable } else { $false } }
if ($AsJson) { $status | ConvertTo-Json } else { Write-Output ($status | Out-String) }
