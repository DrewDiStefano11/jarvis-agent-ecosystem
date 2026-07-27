[CmdletBinding()]
param([string]$ConfigPath = (Join-Path $PSScriptRoot 'jarvis-host.json'))
Import-Module (Join-Path $PSScriptRoot 'JarvisHost.Common.psm1') -Force
$issues = @()
if (-not (Test-Path $ConfigPath)) { $issues += "Config missing" }
$cfg = Import-JarvisHostConfig -Path $ConfigPath
if ($cfg.backendPort -le 0) { $issues += "Invalid backend port" }
if ($issues.Count -gt 0) { Write-Error ($issues -join '; '); exit 1 }
Write-Output "Toolkit validation passed."
