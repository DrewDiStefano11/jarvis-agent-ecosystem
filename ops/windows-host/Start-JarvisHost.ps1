[CmdletBinding(SupportsShouldProcess)]
param([string]$ConfigPath = (Join-Path $PSScriptRoot 'jarvis-host.json'))
Import-Module (Join-Path $PSScriptRoot 'JarvisHost.Common.psm1') -Force
$cfg = Import-JarvisHostConfig -Path $ConfigPath
$lockFile = Join-Path (Resolve-JarvisHostPath -Path $cfg.stateDirectory -BaseDir $env:LOCALAPPDATA) 'start.lock'
$lockHandle = $null
$backendMetaFile = Join-Path $cfg.stateDirectory 'backend.json'
$frontendMetaFile = Join-Path $cfg.stateDirectory 'frontend.json'
try {
  $lockHandle = New-JarvisHostLock -LockPath $lockFile
  # Check existing valid owned processes for idempotency
  $existingBackend = Read-JarvisProcessMetadata -Path $backendMetaFile
  $existingFrontend = Read-JarvisProcessMetadata -Path $frontendMetaFile
  if ($existingBackend.pid) {
    $verifiedBack = Test-JarvisProcessOwnership -PID $existingBackend.pid -ExpectedExecutable $existingBackend.executable -ExpectedWorkingDir $existingBackend.workingDirectory -ExpectedArguments $existingBackend.arguments -ExpectedStartTime ([datetime]::Parse($existingBackend.processStartTime))
    if ($verifiedBack) { Write-Output "Backend already owned and verified: PID $($existingBackend.pid)"; return }
  }
  # Port checks: reject only unowned conflicts
  $portConf = Get-JarvisPortOwner -Port $cfg.backendPort
  if ($portConf -and -not $portConf.pid) { throw "Port conflict on $($cfg.backendPort)" }
  $portConfFront = Get-JarvisPortOwner -Port $cfg.frontendPort
  if ($portConfFront -and -not $portConfFront.pid) { throw "Port conflict on $($cfg.frontendPort)" }
  if ($PSCmdlet.ShouldProcess("Host", "Start backend and frontend")) {
    $backendInfo = Start-JarvisOwnedProcess -FilePath $cfg.backendExecutable -ArgumentList $cfg.backendArguments -WorkingDirectory $cfg.backendWorkingDir -LogDir $cfg.logDirectory
    Write-JarvisProcessMetadata -Path $backendMetaFile -Data $backendInfo
    $healthy = Wait-JarvisHealthEndpoint -Url $cfg.backendHealthUrl -TotalTimeoutSec $cfg.healthTimeoutSec -IntervalSec 2
    if (-not $healthy) {
      # Rollback: stop verified backend and clean metadata
      $rollbackBack = Stop-JarvisOwnedProcess -PID $backendInfo.pid -ExpectedExecutable $cfg.backendExecutable -ExpectedWorkingDir $cfg.backendWorkingDir -ExpectedArguments $cfg.backendArguments -ExpectedStartTime ([datetime]::Parse($backendInfo.processStartTime)) -GracefulTimeoutSec 10
      $tmpClear = $backendMetaFile + ".clear"
      Write-JarvisProcessMetadata -Path $tmpClear -Data @{ pid = $null; cleared = (Get-Date -Format 'o') }
      Move-Item -Path $tmpClear -Destination $backendMetaFile -Force
      throw "Backend health check failed after start; rolled back."
    }
    $frontendInfo = Start-JarvisOwnedProcess -FilePath $cfg.frontendExecutable -ArgumentList $cfg.frontendArguments -WorkingDirectory $cfg.frontendWorkingDir -LogDir $cfg.logDirectory
    Write-JarvisProcessMetadata -Path $frontendMetaFile -Data $frontendInfo
    $healthyFront = Wait-JarvisHealthEndpoint -Url $cfg.frontendHealthUrl -TotalTimeoutSec $cfg.healthTimeoutSec -IntervalSec 2
    if (-not $healthyFront) {
      # Rollback: stop verified frontend then verified backend, clean both
      $rollbackFront = Stop-JarvisOwnedProcess -PID $frontendInfo.pid -ExpectedExecutable $cfg.frontendExecutable -ExpectedWorkingDir $cfg.frontendWorkingDir -ExpectedArguments $cfg.frontendArguments -ExpectedStartTime ([datetime]::Parse($frontendInfo.processStartTime)) -GracefulTimeoutSec 10
      $tmpClearFront = $frontendMetaFile + ".clear"
      Write-JarvisProcessMetadata -Path $tmpClearFront -Data @{ pid = $null; cleared = (Get-Date -Format 'o') }
      Move-Item -Path $tmpClearFront -Destination $frontendMetaFile -Force
      $rollbackBack = Stop-JarvisOwnedProcess -PID $backendInfo.pid -ExpectedExecutable $cfg.backendExecutable -ExpectedWorkingDir $cfg.backendWorkingDir -ExpectedArguments $cfg.backendArguments -ExpectedStartTime ([datetime]::Parse($backendInfo.processStartTime)) -GracefulTimeoutSec 10
      $tmpClearBack = $backendMetaFile + ".clear"
      Write-JarvisProcessMetadata -Path $tmpClearBack -Data @{ pid = $null; cleared = (Get-Date -Format 'o') }
      Move-Item -Path $tmpClearBack -Destination $backendMetaFile -Force
      throw "Frontend health check failed after start; both processes rolled back."
    }
    Write-Output "Host started successfully: backend PID $($backendInfo.pid), frontend PID $($frontendInfo.pid)"
  }
} finally {
  if ($lockHandle) { Remove-JarvisHostLock -LockHandle $lockHandle }
}
