[CmdletBinding(SupportsShouldProcess)]
param([string]$ConfigPath = (Join-Path $PSScriptRoot 'jarvis-host.json'))
Import-Module (Join-Path $PSScriptRoot 'JarvisHost.Common.psm1') -Force
$cfg = Import-JarvisHostConfig -Path $ConfigPath
$lockFile = Join-Path (Resolve-JarvisHostPath -Path $cfg.stateDirectory -BaseDir $env:LOCALAPPDATA) 'start.lock'
try {
  New-JarvisHostLock -LockPath $lockFile
  $portConflict = Get-JarvisPortOwner -Port $cfg.backendPort
  if ($portConflict) { throw "Port conflict: $cfg.backendPort occupied by unrelated process" }
  $portConflict = Get-JarvisPortOwner -Port $cfg.frontendPort
  if ($portConflict) { throw "Port conflict: $cfg.frontendPort occupied by unrelated process" }
  if ($PSCmdlet.ShouldProcess("Host", "Start backend and frontend")) {
    $backendMeta = Start-JarvisOwnedProcess -FilePath $cfg.backendExecutable -ArgumentList $cfg.backendArguments -WorkingDirectory $cfg.backendWorkingDir -LogDir $cfg.logDirectory
    Write-JarvisProcessMetadata -Path (Join-Path $cfg.stateDirectory 'backend.json') -Data $backendMeta
    $healthy = Wait-JarvisHealthEndpoint -Url $cfg.backendHealthUrl -TotalTimeoutSec $cfg.healthTimeoutSec -IntervalSec 2
    if (-not $healthy) { throw "Backend health check failed" }
    $frontendMeta = Start-JarvisOwnedProcess -FilePath $cfg.frontendExecutable -ArgumentList $cfg.frontendArguments -WorkingDirectory $cfg.frontendWorkingDir -LogDir $cfg.logDirectory
    Write-JarvisProcessMetadata -Path (Join-Path $cfg.stateDirectory 'frontend.json') -Data $frontendMeta
    $healthy = Wait-JarvisHealthEndpoint -Url $cfg.frontendHealthUrl -TotalTimeoutSec $cfg.healthTimeoutSec -IntervalSec 2
    if (-not $healthy) { throw "Frontend health check failed; rolling back" }
    Write-Output "Host started successfully."
  }
} finally {
  Remove-JarvisHostLock -LockPath $lockFile
}
