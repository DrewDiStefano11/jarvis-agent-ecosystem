[CmdletBinding(SupportsShouldProcess)]
param([string]$ConfigPath = (Join-Path $PSScriptRoot 'jarvis-host.json'))
Import-Module (Join-Path $PSScriptRoot 'JarvisHost.Common.psm1') -Force
$cfg = Import-JarvisHostConfig -Path $ConfigPath
New-JarvisHostLock -LockPath (Join-Path $cfg.stateDirectory 'start.lock')
if ($PSCmdlet.ShouldProcess("Host", "Start backend/frontend")) {
  $outLog = Join-Path $cfg.logDirectory 'start.log'
  Write-Output "Real start: validated config, acquired lock, would start processes with PID tracking and bounded health."
}
