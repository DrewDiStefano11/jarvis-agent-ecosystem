[CmdletBinding(SupportsShouldProcess)]
param([string]$ConfigPath = (Join-Path $PSScriptRoot 'jarvis-host.json'))
Import-Module (Join-Path $PSScriptRoot 'JarvisHost.Common.psm1') -Force
$cfg = Import-JarvisHostConfig -Path $ConfigPath
New-JarvisHostLock -LockPath (Join-Path $cfg.stateDirectory 'watchdog.lock')
if ($PSCmdlet.ShouldProcess("Host", "Watchdog check")) {
  $healthy = (Test-JarvisHealthEndpoint -Url $cfg.backendHealthUrl -TimeoutSec $cfg.healthTimeoutSec)
  $loop = Test-JarvisCrashLoop -HistoryPath (Join-Path $cfg.stateDirectory 'restarts.json') -WindowMinutes 30 -MaxRestarts 5
  if (-not $healthy -and -not $loop) {
    Add-JarvisRestartRecord -HistoryPath (Join-Path $cfg.stateDirectory 'restarts.json') -Record @{ timestamp = (Get-Date -Format 'o'); reason = 'health' }
    Write-Output "Watchdog: unhealthy, restart within limits."
  } else {
    Write-Output "Watchdog: healthy or crash-loop latched."
  }
}
