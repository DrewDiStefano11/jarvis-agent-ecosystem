[CmdletBinding(SupportsShouldProcess)]
param([string]$ConfigPath = (Join-Path $PSScriptRoot 'jarvis-host.json'), [switch]$Force)
Import-Module (Join-Path $PSScriptRoot 'JarvisHost.Common.psm1') -Force
New-JarvisHostLock -LockPath (Join-Path (Resolve-JarvisHostPath -Path (Import-JarvisHostConfig -Path $ConfigPath).stateDirectory -BaseDir $env:LOCALAPPDATA) 'restart.lock')
if ($PSCmdlet.ShouldProcess("Host", "Restart")) {
  . $PSScriptRoot\Stop-JarvisHost.ps1 -ConfigPath $ConfigPath -Force:$Force
  . $PSScriptRoot\Start-JarvisHost.ps1 -ConfigPath $ConfigPath
  Write-Output "Real restart executed: safe stop/start with health verification."
}
