[CmdletBinding()]
param([string]$ConfigPath = (Join-Path $PSScriptRoot 'jarvis-host.json'))
Import-Module (Join-Path $PSScriptRoot 'JarvisHost.Common.psm1') -Force
$exitCode = 0
try {
  $cfg = Import-JarvisHostConfig -Path $ConfigPath
  if (-not (Test-Path $cfg.repoPath)) { throw "Repo path missing" }
  $backendPortAvailable = (Get-JarvisPortOwner -Port $cfg.backendPort) -eq $null
  if (-not $backendPortAvailable) { throw "Port $($cfg.backendPort) occupied by unrelated process" }
  $dbPathDir = Split-Path $cfg.databasePath
  if ($dbPathDir -and -not (Test-Path $dbPathDir)) { throw "Database parent directory missing" }
  Write-Output "Preflight passed."
} catch {
  Write-Error $_.Exception.Message
  $exitCode = 1
}
exit $exitCode
