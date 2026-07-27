[CmdletBinding(SupportsShouldProcess)]
param([string]$ConfigPath = (Join-Path $PSScriptRoot 'jarvis-host.json'))
Import-Module (Join-Path $PSScriptRoot 'JarvisHost.Common.psm1') -Force
$cfg = Import-JarvisHostConfig -Path $ConfigPath
$lockFile = Join-Path (Resolve-JarvisHostPath -Path $cfg.stateDirectory -BaseDir $env:LOCALAPPDATA) 'watchdog.lock'
try {
  New-JarvisHostLock -LockPath $lockFile
  $latchFile = Join-Path (Resolve-JarvisHostPath -Path $cfg.stateDirectory -BaseDir $env:LOCALAPPDATA) 'crash_loop.json'
  $latched = Get-JarvisCrashLoopLatch -LatchPath $latchFile
  if ($latched) { Write-Output "Watchdog: crash-loop latched. Manual reset required."; return 2 }
  $backendHealthy = Test-JarvisHealthEndpoint -Url $cfg.backendHealthUrl -TimeoutSec $cfg.healthTimeoutSec
  $frontendHealthy = Test-JarvisHealthEndpoint -Url $cfg.frontendHealthUrl -TimeoutSec $cfg.healthTimeoutSec
  $historyPath = Join-Path (Resolve-JarvisHostPath -Path $cfg.stateDirectory -BaseDir $env:LOCALAPPDATA) 'restarts.json'
  if ((-not $backendHealthy) -or (-not $frontendHealthy)) {
    $loop = Test-JarvisCrashLoop -HistoryPath $historyPath -WindowMinutes ([Math]::Round($cfg.watchdogIntervalSec / 60)) -MaxRestarts $cfg.maxRestartsWithinWindow
    if ($loop) { Set-JarvisCrashLoopLatch -LatchPath $latchFile; Write-Output "Watchdog: crash loop detected. Latched."; return 3 }
    if ($PSCmdlet.ShouldProcess("Host", "Restart due to health failure")) {
      Add-JarvisRestartRecord -HistoryPath $historyPath -Record @{ timestamp = (Get-Date -Format 'o'); reason = 'watchdog_health_failure'; backend = $backendHealthy; frontend = $frontendHealthy }
      . $PSScriptRoot\Restart-JarvisHost.ps1 -ConfigPath $ConfigPath
    }
  } else {
    Write-Output "Watchdog: both services healthy."
  }
} finally {
  Remove-JarvisHostLock -LockPath $lockFile
}
