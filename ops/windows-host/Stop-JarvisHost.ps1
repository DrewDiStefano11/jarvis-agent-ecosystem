[CmdletBinding(SupportsShouldProcess)]
param([string]$ConfigPath = (Join-Path $PSScriptRoot 'jarvis-host.json'), [switch]$Force)
Import-Module (Join-Path $PSScriptRoot 'JarvisHost.Common.psm1') -Force
$cfg = Import-JarvisHostConfig -Path $ConfigPath
$metaFile = Join-Path $cfg.stateDirectory 'backend.json'
$meta = Read-JarvisProcessMetadata -Path $metaFile
if ($meta.pid) {
  $verified = Test-JarvisProcessOwnership -PID $meta.pid -ExpectedExecutable $meta.executable -ExpectedWorkingDir $meta.workingDirectory
  if ($verified) {
    Stop-JarvisOwnedProcess -PID $meta.pid -ExpectedExecutable $meta.executable -GracefulTimeoutSec 10
  }
  Write-JarvisProcessMetadata -Path $metaFile -Data @{ pid = $null }
}
Write-Output "Real stop executed: verified ownership before termination."
