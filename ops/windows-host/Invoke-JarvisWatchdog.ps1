[CmdletBinding(SupportsShouldProcess)]
param([string]$ConfigPath = (Join-Path $PSScriptRoot 'jarvis-host.json'))
Import-Module (Join-Path $PSScriptRoot 'JarvisHost.Common.psm1') -Force
$cfg = Import-JarvisHostConfig -Path $ConfigPath
$lockFile = Join-Path (Resolve-JarvisHostPath -Path $cfg.stateDirectory -BaseDir $env:LOCALAPPDATA) 'watchdog.lock'
$lockHandle = $null
try {
  $lockHandle = New-JarvisHostLock -LockPurpose "watchdog"
  $latchFile = Join-Path (Resolve-JarvisHostPath -Path $cfg.stateDirectory -BaseDir $env:LOCALAPPDATA) 'crash_loop.json'
  $latched = Get-JarvisCrashLoopLatch -LatchPath $latchFile
  if ($latched) { Write-Output "Watchdog: crash-loop latched. Exit 2."; exit 2 }
  $historyPath = Join-Path (Resolve-JarvisHostPath -Path $cfg.stateDirectory -BaseDir $env:LOCALAPPDATA) 'restarts.json'
  $backendHealthy = Test-JarvisHealthEndpoint -Url $cfg.backendHealthUrl -TimeoutSec $cfg.healthTimeoutSec
  $frontendHealthy = Test-JarvisHealthEndpoint -Url $cfg.frontendHealthUrl -TimeoutSec $cfg.healthTimeoutSec
  $metaBack = Read-JarvisProcessMetadata -Path (Join-Path $cfg.stateDirectory 'backend.json')
  $metaFront = Read-JarvisProcessMetadata -Path (Join-Path $cfg.stateDirectory 'frontend.json')
  $backOwned = $false
  $frontOwned = $false
  if ($metaBack.pid -and [int]$metaBack.pid) {
    $backOwned = Test-JarvisProcessOwnership -PID ([int]$metaBack.pid) -ExpectedExecutable $metaBack.executable -ExpectedWorkingDir $metaBack.workingDirectory -ExpectedArguments $metaBack.arguments -ExpectedStartTime ([datetime]::Parse($metaBack.processStartTime))
  }
  if ($metaFront.pid -and [int]$metaFront.pid) {
    $frontOwned = Test-JarvisProcessOwnership -PID ([int]$metaFront.pid) -ExpectedExecutable $metaFront.executable -ExpectedWorkingDir $metaFront.workingDirectory -ExpectedArguments $metaFront.arguments -ExpectedStartTime ([datetime]::Parse($metaFront.processStartTime))
  }
  $portBack = Get-JarvisPortOwner -Port $cfg.backendPort
  $conflictBack = ($portBack -and $portBack.pid -and ($metaBack.pid -and [int]$portBack.pid -ne [int]$metaBack.pid))
  $portFront = Get-JarvisPortOwner -Port $cfg.frontendPort
  $conflictFront = ($portFront -and $portFront.pid -and ($metaFront.pid -and [int]$portFront.pid -ne [int]$metaFront.pid))
  if ($conflictBack -or $conflictFront) { Write-Output "Watchdog: unowned port conflict detected. Not restarting. Exit 1."; exit 1 }
  if ((-not $backendHealthy) -or (-not $frontendHealthy)) {
    $windowMinutes = [Math]::Max(1, [int]([Math]::Round($cfg.watchdogIntervalSec / 60)))
    $loop = Test-JarvisCrashLoop -HistoryPath $historyPath -WindowMinutes $windowMinutes -MaxRestarts $cfg.maxRestartsWithinWindow
    if ($loop) { Set-JarvisCrashLoopLatch -LatchPath $latchFile; Write-Output "Watchdog: crash loop detected. Latched. Exit 3."; exit 3 }
    if ($PSCmdlet.ShouldProcess("Host", "Restart due to health failure")) {
      Add-JarvisRestartRecord -HistoryPath $historyPath -Record @{ timestamp = (Get-Date -Format 'o'); reason = 'watchdog_health_failure'; backendOwned = $backOwned; frontendOwned = $frontOwned; backendHealthy = $backendHealthy; frontendHealthy = $frontendHealthy }
      . $PSScriptRoot\Restart-JarvisHost.ps1 -ConfigPath $ConfigPath
      Add-JarvisRestartRecord -HistoryPath $historyPath -Record @{ timestamp = (Get-Date -Format 'o'); reason = 'watchdog_restart_complete'; result = 'attempted' }
    }
  } else {
    Write-Output "Watchdog: healthy (backOwned=$backOwned frontOwned=$frontOwned)."
  }
} finally {
  if ($lockHandle) { Remove-JarvisHostLock -LockHandle $lockHandle }
}
