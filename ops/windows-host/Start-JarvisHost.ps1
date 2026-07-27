[CmdletBinding(SupportsShouldProcess)]
param([string]$ConfigPath = (Join-Path $PSScriptRoot 'jarvis-host.json'))
Import-Module (Join-Path $PSScriptRoot 'JarvisHost.Common.psm1') -Force
$cfg = Import-JarvisHostConfig -Path $ConfigPath
$lockPath = Join-Path (Resolve-JarvisHostPath -Path $cfg.stateDirectory -BaseDir $env:LOCALAPPDATA) 'start.lock'
$lockHandle = $null
try {
  $lockHandle = New-JarvisHostLock -LockPurpose "start"
  $existingBackend = Read-JarvisProcessMetadata -Path (Join-Path $cfg.stateDirectory 'backend.json')
  if ($existingBackend.pid) {
    $verifiedBack = Test-JarvisProcessOwnership -PID ([int]$existingBackend.pid) -ExpectedExecutable $existingBackend.executable -ExpectedWorkingDir $existingBackend.workingDirectory -ExpectedArguments $existingBackend.arguments -ExpectedStartTime ([datetime]::Parse($existingBackend.processStartTime))
    if ($verifiedBack) {
      Write-Output "Backend already verified running at PID $($existingBackend.pid)"
      return
    }
  }
  $portBack = Get-JarvisPortOwner -Port $cfg.backendPort
  if ($portBack -and -not $portBack.pid) { throw "Port conflict: backend port $($cfg.backendPort) occupied by unrelated process" }
  $portFront = Get-JarvisPortOwner -Port $cfg.frontendPort
  if ($portFront -and -not $portFront.pid) { throw "Port conflict: frontend port $($cfg.frontendPort) occupied by unrelated process" }
  if ($PSCmdlet.ShouldProcess("Host", "Start backend and frontend")) {
    $backendInfo = Start-JarvisOwnedProcess -FilePath $cfg.backendExecutable -ArgumentList $cfg.backendArguments -WorkingDirectory $cfg.backendWorkingDir -LogDir $cfg.logDirectory
    Write-JarvisProcessMetadata -Path (Join-Path $cfg.stateDirectory 'backend.json') -Data $backendInfo
    $healthy = Wait-JarvisHealthEndpoint -Url $cfg.backendHealthUrl -TotalTimeoutSec $cfg.healthTimeoutSec -IntervalSec 2
    if (-not $healthy) {
      $rollbackBack = Stop-JarvisOwnedProcess -PID $backendInfo.pid -ExpectedExecutable $cfg.backendExecutable -ExpectedWorkingDir $cfg.backendWorkingDir -ExpectedArguments $cfg.backendArguments -ExpectedStartTime ([datetime]::Parse($backendInfo.processStartTime)) -GracefulTimeoutSec 10
      $tmp = (Join-Path $cfg.stateDirectory 'backend.json') + ".clear"
      @{ pid = $null; cleared = (Get-Date -Format 'o') } | ConvertTo-Json | Set-Content -Path $tmp -NoNewline
      Move-Item -Path $tmp -Destination (Join-Path $cfg.stateDirectory 'backend.json') -Force
      throw "Backend health check failed after start; rolled back."
    }
    $frontendInfo = Start-JarvisOwnedProcess -FilePath $cfg.frontendExecutable -ArgumentList $cfg.frontendArguments -WorkingDirectory $cfg.frontendWorkingDir -LogDir $cfg.logDirectory
    Write-JarvisProcessMetadata -Path (Join-Path $cfg.stateDirectory 'frontend.json') -Data $frontendInfo
    $healthyFront = Wait-JarvisHealthEndpoint -Url $cfg.frontendHealthUrl -TotalTimeoutSec $cfg.healthTimeoutSec -IntervalSec 2
    if (-not $healthyFront) {
      Stop-JarvisOwnedProcess -PID $frontendInfo.pid -ExpectedExecutable $cfg.frontendExecutable -ExpectedWorkingDir $cfg.frontendWorkingDir -ExpectedArguments $cfg.frontendArguments -ExpectedStartTime ([datetime]::Parse($frontendInfo.processStartTime)) -GracefulTimeoutSec 10
      @{ pid = $null; cleared = (Get-Date -Format 'o') } | ConvertTo-Json | Set-Content -Path ((Join-Path $cfg.stateDirectory 'frontend.json') + ".clear") -NoNewline
      Move-Item -Path ((Join-Path $cfg.stateDirectory 'frontend.json') + ".clear") -Destination (Join-Path $cfg.stateDirectory 'frontend.json') -Force
      Stop-JarvisOwnedProcess -PID $backendInfo.pid -ExpectedExecutable $cfg.backendExecutable -ExpectedWorkingDir $cfg.backendWorkingDir -ExpectedArguments $cfg.backendArguments -ExpectedStartTime ([datetime]::Parse($backendInfo.processStartTime)) -GracefulTimeoutSec 10
      @{ pid = $null; cleared = (Get-Date -Format 'o') } | ConvertTo-Json | Set-Content -Path ((Join-Path $cfg.stateDirectory 'backend.json') + ".clear") -NoNewline
      Move-Item -Path ((Join-Path $cfg.stateDirectory 'backend.json') + ".clear") -Destination (Join-Path $cfg.stateDirectory 'backend.json') -Force
      throw "Frontend health check failed; rolled back both."
    }
    Write-Output "Host started: backend PID $($backendInfo.pid), frontend PID $($frontendInfo.pid)"
  }
} finally {
  if ($lockHandle) { Remove-JarvisHostLock -LockHandle $lockHandle }
}
