[CmdletBinding()]
param([string]$ConfigPath = (Join-Path $PSScriptRoot 'jarvis-host.json'))
Import-Module (Join-Path $PSScriptRoot 'JarvisHost.Common.psm1') -Force
$cfg = Import-JarvisHostConfig -Path $ConfigPath
Write-Output ("Preflight: repo " + $cfg.repoPath + "; backend port " + $cfg.backendPort + "; health URL " + $cfg.backendHealthUrl)
if (-not (Test-Path $cfg.repoPath)) { throw "Repo path missing" }
if ($cfg.backendPort -le 0 -or $cfg.backendPort -gt 65535) { throw "Invalid port" }
Write-Output "Preflight passed."
